// Login providers on the app side (server/app/login_providers.py): the buttons on /login and "Forbundne logins" on
// /konto. A provider replaces only the one-time code; the second-factor rules after it are the same.
import type { CSSProperties } from "react";

export type ProviderKind = "entra" | "google" | "github" | "amazon" | "saml";
export type PublicProvider = { key: string; kind: ProviderKind; label: string; name: string };
export type ProviderList = { providers: PublicProvider[]; required: (PublicProvider & { domain: string }) | null; sso: boolean };

/** Why a login at a provider was refused (?sso_error=<code>, server/app/login_providers.py REFUSALS). */
export const SSO_ERRORS: Record<string, string> = {
  no_account: "Du har ikke en konto her. Kontakt en administrator.",
  blocked: "Din bruger er spærret. Kontakt en administrator.",
  domain: "Din e-mail hører ikke til et domæne, der må bruge denne udbyder.",
  unverified: "Udbyderen har ikke bekræftet din e-mail. Log ind med en kode, og forbind kontoen under Konto.",
  other_account: "Din konto er allerede forbundet med en anden konto hos udbyderen.",
  taken: "Kontoen hos udbyderen er allerede forbundet med en anden bruger.",
  expired: "Login tog for lang tid eller blev startet i en anden browser. Prøv igen.",
  disabled: "Udbyderen er slået fra.",
  failed: "Login hos udbyderen mislykkedes. Prøv igen, eller log ind med en kode.",
  denied: "Du afbrød login hos udbyderen.",
  test_account: "Testkonti kan ikke logge ind.",
};

export function ssoErrorText(code: string | null): string | null {
  return code ? SSO_ERRORS[code] ?? SSO_ERRORS.failed : null;
}

/** The address that starts a login at the provider; it comes back to /login (second factor, passkey offer) or `next`. */
export function startUrl(key: string, next: string): string {
  return `/api/auth/providers/${encodeURIComponent(key)}/start?next=${encodeURIComponent(next)}`;
}

export function ProviderIcon({ kind, size = 16 }: { kind: ProviderKind; size?: number }) {
  if (kind === "entra") {
    return (
      <svg width={size} height={size} viewBox="0 0 21 21" aria-hidden="true">
        <rect x="1" y="1" width="9" height="9" fill="#f25022" /><rect x="11" y="1" width="9" height="9" fill="#7fba00" />
        <rect x="1" y="11" width="9" height="9" fill="#00a4ef" /><rect x="11" y="11" width="9" height="9" fill="#ffb900" />
      </svg>
    );
  }
  const letter: CSSProperties = { fontWeight: 700, fontSize: size, lineHeight: 1, width: size, textAlign: "center" };
  if (kind === "google") return <span aria-hidden="true" style={{ ...letter, color: "#1a73e8" }}>G</span>;
  if (kind === "github") return <span aria-hidden="true" style={{ ...letter, fontSize: size * 0.75 }}>GH</span>;
  if (kind === "amazon") return <span aria-hidden="true" style={{ ...letter, color: "#c2410c" }}>a</span>;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="#1d4ed8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z" />
      <circle cx="16.5" cy="7.5" r=".5" fill="#1d4ed8" />
    </svg>
  );
}
