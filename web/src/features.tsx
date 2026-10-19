// Features and rings in the app on /. A feature is shown when its ring is at least the user's level;
// rings are set in /admin/features. Wrap UI in <FeatureGate feature="key">. Usage of a shown feature is
// sent in batches (at most once a minute per key) so /admin can show who uses what.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { useAuth } from "./auth";

type FeatureState = { key: string; ring: number; enabled: boolean; observe: boolean };
type Features = { level: number; byKey: Map<string, FeatureState> } | null;

const FeaturesContext = createContext<Features>(null);

export function FeaturesProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [features, setFeatures] = useState<Features>(null);
  useEffect(() => {
    api.get<{ level: number; features: FeatureState[] }>("/api/features")
      .then((r) => setFeatures({ level: r.level, byKey: new Map(r.features.map((f) => [f.key, f])) }))
      .catch(() => setFeatures(null));
  }, [user?.id]);
  return <FeaturesContext.Provider value={features}>{children}</FeaturesContext.Provider>;
}

/** True while loading (nothing flickers away) and for keys that are not registered yet. */
export function useFeature(key: string): boolean {
  const features = useContext(FeaturesContext);
  if (!features) return true;
  return features.byKey.get(key)?.enabled ?? true;
}

export function FeatureGate({ feature, children }: { feature: string; children: ReactNode }) {
  const enabled = useFeature(feature);
  const { user } = useAuth();
  useEffect(() => {
    if (enabled && user) noteUsage(feature);
  }, [enabled, feature, user]);
  return enabled ? <>{children}</> : null;
}

// ── Usage batching ──────────────────────────────────────────────────────────

const pending = new Set<string>();
const lastSent = new Map<string, number>();
let timer = 0;

function noteUsage(key: string) {
  if (Date.now() - (lastSent.get(key) ?? 0) < 60_000) return;
  pending.add(key);
  timer ||= window.setTimeout(flush, 30_000);
}

function flush(keepalive = false) {
  window.clearTimeout(timer);
  timer = 0;
  if (!pending.size) return;
  const keys = [...pending];
  pending.clear();
  keys.forEach((k) => lastSent.set(k, Date.now()));
  fetch("/api/features/usage", {
    method: "POST", keepalive, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Gaer": "1" }, body: JSON.stringify({ keys }),
  }).catch(() => {});
}

window.addEventListener("pagehide", () => flush(true));
