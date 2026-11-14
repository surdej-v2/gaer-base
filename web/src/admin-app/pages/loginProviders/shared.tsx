// Shared by /admin/login-providers and its setup pages (server/app/login_providers.py, routes/login_providers.py).
import type { CSSProperties } from "react";
import { KeyRound } from "lucide-react";

export type Kind = "entra" | "google" | "github" | "amazon" | "saml";
export type Status = "active" | "ready" | "incomplete";

export type ProviderSummary = {
  key: string; kind: Kind; name: string; label: string; enabled: boolean; protocol: "oidc" | "oauth2" | "saml";
  subtitle: string; audience: string; status: Status; missing: string[]; domains: string[];
  new_users: "existing" | "signup"; only_matching_domain: boolean; linked: number; cert_days: number | null;
  last_login_at: string | null; enabled_at: string | null; updated_at: string;
};

export type GroupMap = { claim: string; group_id: number };
export type Config = {
  domains: string[]; new_users: "existing" | "signup"; button_label: string; only_matching_domain: boolean;
  client_id?: string; tenant_mode?: "single" | "multi"; tenant_id?: string; tenants?: string[]; hosted_domain?: string;
  group_map?: GroupMap[];
  metadata_source?: "url" | "xml" | "manual"; metadata_url?: string; refresh?: boolean; attr_email?: string; attr_name?: string;
  attr_groups?: string; nameid_format?: "persistent" | "email"; want_encrypted?: boolean; allow_idp_initiated?: boolean;
};

export type LastTest = { at: string; ok: boolean; claims: string[]; email_domain: string | null; email_verified: boolean | null; groups: number; error: string | null };

export type ProviderDetail = ProviderSummary & {
  config: Config;
  urls: Record<string, string>;
  secret_set: boolean;
  client_secret_set_at: string | null;
  last_test: LastTest | null;
  idp: { entity_id: string | null; sso_url: string | null; slo_url: string | null; fetched_at: string | null; error?: string | null;
         certs: { sha256: string; not_after: string }[] } | null;
  stats: { errors_24h: number; last_login_at: string | null };
  groups: { id: number; name: string; slug: string }[];
  can_map_groups: boolean;
  default_button: string;
};

export type Rules = { link: "verified_email" | "user_linked"; new_users: "signup" | "never"; required: { domain: string; provider: string }[] };

export const KIND_LABEL: Record<Kind, string> = {
  entra: "Microsoft Entra ID", google: "Google", github: "GitHub", amazon: "Amazon", saml: "SAML 2.0",
};

export const STATUS: Record<Status, [string, string, string]> = {
  active: ["Aktiv", "rgba(22,163,74,0.12)", "#166534"],
  ready: ["Klar · ikke slået til", "rgba(37,99,235,0.1)", "#1d4ed8"],
  incomplete: ["Ikke færdig", "#f1f5f9", "#475569"],
};

/** Where an admin registers Gær with the provider. */
export const CONSOLE: Partial<Record<Kind, [string, string]>> = {
  entra: ["Åbn App registrations i Entra", "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"],
  google: ["Åbn Credentials i Google Cloud", "https://console.cloud.google.com/apis/credentials"],
  github: ["Åbn OAuth Apps på GitHub", "https://github.com/settings/developers"],
  amazon: ["Åbn Login with Amazon-konsollen", "https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html"],
};

export function MicrosoftMark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 21 21" aria-hidden="true">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" /><rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" /><rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

/** The provider's mark in a rounded square (the overview and the setup pages). */
export function ProviderMark({ kind, size = 40 }: { kind: Kind; size?: number }) {
  const box: CSSProperties = {
    flex: "none", width: size, height: size, borderRadius: size >= 48 ? 12 : 10, display: "flex", alignItems: "center",
    justifyContent: "center", border: "1px solid #e2e8f0", background: "#ffffff", fontWeight: 700,
  };
  if (kind === "entra") return <span style={box}><MicrosoftMark size={Math.round(size * 0.45)} /></span>;
  if (kind === "google") return <span style={{ ...box, color: "#1a73e8", fontSize: size * 0.45 }} aria-hidden="true">G</span>;
  if (kind === "github") return <span style={{ ...box, background: "#0f172a", color: "#ffffff", border: 0, fontSize: size * 0.32 }} aria-hidden="true">GH</span>;
  if (kind === "amazon") return <span style={{ ...box, color: "#c2410c", fontSize: size * 0.42 }} aria-hidden="true">a</span>;
  return (
    <span style={{ ...box, background: "rgba(37,99,235,0.1)", color: "#1d4ed8", border: 0 }}>
      <KeyRound size={Math.round(size * 0.45)} strokeWidth={2} aria-hidden="true" />
    </span>
  );
}

export function StatusPill({ status }: { status: Status }) {
  const [text, bg, fg] = STATUS[status];
  return <span style={{ display: "inline-flex", padding: "3px 10px", borderRadius: 9999, background: bg, color: fg, fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>{text}</span>;
}

export function CertPill({ days }: { days: number | null }) {
  if (days === null) return null;
  const warn = days <= 30;
  return (
    <span style={{ display: "inline-flex", padding: "2px 8px", borderRadius: 9999, fontSize: 11, fontWeight: 700, whiteSpace: "nowrap",
      background: warn ? "rgba(249,115,22,0.12)" : "#f1f5f9", color: warn ? "#9a3412" : "#475569" }}>
      Certifikat: {days} {days === 1 ? "dag" : "dage"}
    </span>
  );
}

/** "firma.dk, datter.dk · kun eksisterende brugere" */
export function whoText(p: Pick<ProviderSummary, "domains" | "new_users" | "only_matching_domain" | "kind" | "audience" | "status">): string {
  if (p.status === "incomplete" && !p.domains.length) return p.audience;
  const domains = p.domains.length ? p.domains.join(", ") : "Alle e-mails";
  const who = p.only_matching_domain ? "vises når e-mailen matcher" : p.new_users === "signup" ? "nye brugere kan oprettes" : "kun eksisterende brugere";
  return `${domains} · ${who}`;
}

export const mono: CSSProperties = { fontFamily: "var(--hm-font-mono)" };
export const codeBox: CSSProperties = {
  ...mono, padding: "8px 12px", borderRadius: 8, background: "#f8fafc", border: "1px solid #e2e8f0", fontSize: 13,
  overflowX: "auto", whiteSpace: "nowrap", flex: 1, minWidth: 0,
};
export const section: CSSProperties = {
  background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 16, padding: 20, display: "flex", flexDirection: "column", gap: 14, minWidth: 0,
};
export const h2: CSSProperties = { margin: 0, fontSize: 16, fontWeight: 700 };
export const hint: CSSProperties = { margin: 0, fontSize: 13, color: "#475569" };
export const fieldLabel: CSSProperties = { display: "flex", flexDirection: "column", gap: 6, fontSize: 13, fontWeight: 500 };
export const inputStyle: CSSProperties = {
  height: 36, padding: "0 10px", border: "1px solid #e2e8f0", borderRadius: 8, font: "14px var(--hm-font-sans)", boxSizing: "border-box",
  minWidth: 0, background: "#ffffff", color: "#020817",
};
