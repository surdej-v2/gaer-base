// Features and rings in the app on /. A feature is shown when its ring is at least the user's level;
// rings are set in /admin/features. Wrap UI in <FeatureGate feature="key">. Usage of a shown feature is
// sent in batches (at most once a minute per key) so /admin can show who uses what.
//
// The review panel (review/) can preview the app as another ring sees it: setPreview() sets the level to view as
// and local per-feature ring changes. It only changes what this browser shows, never what the server allows.
import {
  createContext, useCallback, useContext, useEffect, useId, useMemo, useState, useSyncExternalStore, type ReactNode,
} from "react";
import { api } from "./api";
import { useAuth } from "./auth";

export type FeatureState = { key: string; ring: number; enabled: boolean; observe: boolean };
type Features = { level: number; byKey: Map<string, FeatureState> } | null;

/** level null = the user's own level. overrides: feature key → ring, only in this browser. */
export type RingPreview = { level: number | null; overrides: Record<string, number>; markers: boolean };

type FeaturesCtx = {
  features: Features;
  preview: RingPreview | null;
  setPreview: (preview: RingPreview | null) => void;
};

const FeaturesContext = createContext<FeaturesCtx>({ features: null, preview: null, setPreview: () => {} });

export function FeaturesProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [features, setFeatures] = useState<Features>(null);
  const [preview, setPreviewState] = useState<RingPreview | null>(null);
  useEffect(() => {
    api.get<{ level: number; features: FeatureState[] }>("/api/features")
      .then((r) => setFeatures({ level: r.level, byKey: new Map(r.features.map((f) => [f.key, f])) }))
      .catch(() => setFeatures(null));
  }, [user?.id]);
  const setPreview = useCallback((p: RingPreview | null) => setPreviewState(p), []);
  const value = useMemo(() => ({ features, preview, setPreview }), [features, preview, setPreview]);
  return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

/** For the review panel: the real features and level, and the preview it controls. */
export function useFeatures(): FeaturesCtx {
  return useContext(FeaturesContext);
}

/** The ring a feature has in this browser (a local change from the review panel wins). */
export function effectiveRing(ctx: FeaturesCtx, key: string): number | null {
  return ctx.preview?.overrides[key] ?? ctx.features?.byKey.get(key)?.ring ?? null;
}

/** The level the app is shown as: the preview's, else the user's own. */
export function viewLevel(ctx: FeaturesCtx): number {
  return ctx.preview?.level ?? ctx.features?.level ?? 4;
}

function featureOn(ctx: FeaturesCtx, key: string): boolean {
  if (!ctx.features) return true;
  if (!ctx.preview) return ctx.features.byKey.get(key)?.enabled ?? true;
  const ring = effectiveRing(ctx, key);
  return ring === null ? true : ring >= viewLevel(ctx);
}

/** True while loading (nothing flickers away) and for keys that are not registered yet. */
export function useFeature(key: string): boolean {
  return featureOn(useContext(FeaturesContext), key);
}

/** The same check as useFeature, for a list of keys (e.g. filtering a menu). */
export function useFeatureCheck(): (key: string) => boolean {
  const ctx = useContext(FeaturesContext);
  return useCallback((key: string) => featureOn(ctx, key), [ctx]);
}

export function FeatureGate({ feature, children }: { feature: string; children: ReactNode }) {
  const enabled = useFeature(feature);
  const { user } = useAuth();
  const { preview } = useContext(FeaturesContext);
  const id = useId();
  useEffect(() => {
    if (enabled && user) noteUsage(feature);
  }, [enabled, feature, user]);
  useEffect(() => gates.set(id, feature, enabled), [id, feature, enabled]);
  if (!enabled) return null;
  // In review mode the gate marks its content, so the panel can draw ring markers and find the feature of an element.
  return preview ? <span data-gaer-feature={feature} style={{ display: "contents" }}>{children}</span> : <>{children}</>;
}

// ── Gates on the page right now (for "Denne side" in the review panel) ─────

export type MountedGate = { key: string; enabled: boolean };

const gates = (() => {
  const items = new Map<string, MountedGate>();
  const listeners = new Set<() => void>();
  let snapshot: MountedGate[] = [];
  const emit = () => {
    snapshot = [...items.values()];
    listeners.forEach((l) => l());
  };
  return {
    set(id: string, key: string, enabled: boolean) {
      items.set(id, { key, enabled });
      emit();
      return () => {
        items.delete(id);
        emit();
      };
    },
    subscribe(l: () => void) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    snapshot: () => snapshot,
  };
})();

export function useMountedGates(): MountedGate[] {
  return useSyncExternalStore(gates.subscribe, gates.snapshot, gates.snapshot);
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
