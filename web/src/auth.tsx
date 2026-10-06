import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { api } from "./api";

export type User = {
  id: number;
  phone: string;
  display_name: string | null;
  role: "user" | "admin";
  passkeys: number;
  grace_left: number;
};

export type PublicConfig = {
  app_name: string;
  logo_url: string | null;
  consent_version: number;
  consent_title: string;
  consent_text: string;
  consent_accept: string;
  consent_decline: string;
  first_user: boolean;
};

type AuthState = {
  user: User | null;
  scope: "full" | "enroll" | null;
  loading: boolean;
  config: PublicConfig | null;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [scope, setScope] = useState<AuthState["scope"]>(null);
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState<PublicConfig | null>(null);

  const refresh = useCallback(async () => {
    const [me, cfg] = await Promise.all([
      api.get<{ user: User | null; scope?: "full" | "enroll" }>("/api/auth/me"),
      api.get<PublicConfig>("/api/public/config"),
    ]);
    setUser(me.user);
    setScope(me.scope ?? null);
    setConfig(cfg);
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (config) document.title = config.app_name;
  }, [config]);

  const logout = useCallback(async () => {
    await api.post("/api/auth/logout");
    setUser(null);
    setScope(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, scope, loading, config, refresh, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

// Passkeys (WebAuthn) — the server creates the options and verifies the result.

type Options<T> = { challenge_id: string; options: T };

export async function registerPasskey(name?: string): Promise<void> {
  const { challenge_id, options } = await api.post<Options<Parameters<typeof startRegistration>[0]["optionsJSON"]>>(
    "/api/auth/passkey/register/options",
  );
  const credential = await startRegistration({ optionsJSON: options });
  await api.post("/api/auth/passkey/register/verify", { challenge_id, credential, name });
}

export async function loginWithPasskey(): Promise<void> {
  const { challenge_id, options } = await api.post<
    Options<Parameters<typeof startAuthentication>[0]["optionsJSON"]>
  >("/api/auth/passkey/login/options");
  const credential = await startAuthentication({ optionsJSON: options });
  await api.post("/api/auth/passkey/login/verify", { challenge_id, credential });
}

export function passkeyErrorText(e: unknown): string {
  if (e instanceof Error && (e.name === "NotAllowedError" || e.name === "AbortError")) {
    return "Handlingen blev afbrudt. Prøv igen.";
  }
  return e instanceof Error ? e.message : "Noget gik galt";
}
