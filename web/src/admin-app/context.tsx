// State shared by the shell and the pages: period on Statistik, active impersonation and menu badges.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";

export type Impersonated = { id: number; display_name: string; role: "user" | "admin" };
export type Health = { ok: boolean; version: string; ms: number } | null;

type AdminState = {
  range: number;
  setRange: (days: number) => void;
  impersonating: Impersonated | null;
  refreshImpersonation: () => Promise<void>;
  missingFeatures: number;
  setMissingFeatures: (n: number) => void;
  health: Health;
};

const AdminContext = createContext<AdminState | null>(null);

export function AdminProvider({ children }: { children: ReactNode }) {
  const [range, setRange] = useState(30);
  const [impersonating, setImpersonating] = useState<Impersonated | null>(null);
  const [missingFeatures, setMissingFeatures] = useState(0);
  const [health, setHealth] = useState<Health>(null);

  const refreshImpersonation = useCallback(async () => {
    try {
      const r = await api.get<{ user: Impersonated | null }>("/api/impersonation/status");
      setImpersonating(r.user);
    } catch {
      setImpersonating(null);
    }
  }, []);

  useEffect(() => {
    void refreshImpersonation();
    api.get<{ missing: unknown[] }>("/api/admin/features").then((r) => setMissingFeatures(r.missing.length), () => {});
    const t0 = performance.now();
    fetch("/health", { credentials: "same-origin" })
      .then((r) => r.json())
      .then((h: { ok: boolean; version: string }) => setHealth({ ...h, ms: Math.round(performance.now() - t0) }))
      .catch(() => setHealth({ ok: false, version: "", ms: 0 }));
  }, [refreshImpersonation]);

  return (
    <AdminContext.Provider value={{ range, setRange, impersonating, refreshImpersonation, missingFeatures, setMissingFeatures, health }}>
      {children}
    </AdminContext.Provider>
  );
}

export function useAdmin(): AdminState {
  const ctx = useContext(AdminContext);
  if (!ctx) throw new Error("useAdmin outside AdminProvider");
  return ctx;
}
