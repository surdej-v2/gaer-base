// /konto#forbundne-logins: the user's own accounts at login providers (server/app/login_providers.py). Optional: the
// one-time code by e-mail always works. Connecting or removing one needs the same proof as changing a passkey (the
// step-up dialog opens by itself). Coming back from a provider: ?linked=<key> or ?sso_error=<code>.
import { useEffect, useState } from "react";
import { api, errorText } from "../api";
import { ProviderIcon, ssoErrorText, type ProviderKind } from "../loginProviders";
import { Alert, fmtDate, Loading, useLoad } from "../ui";

type Linked = { email: string | null; created_at: string; last_used_at: string | null; via: "user" | "verified_email" | "signup" };
type Row = { key: string; kind: ProviderKind; name: string; enabled: boolean; domains: string[]; available: boolean; linked: Linked | null };

export default function LinkedLogins() {
  const list = useLoad(() => api.get<{ providers: Row[] }>("/api/me/identities"));
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Back from the provider: say how it went, then tidy the address.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const linked = q.get("linked");
    const failed = q.get("sso_error");
    if (!linked && !failed) return;
    if (failed) setError(ssoErrorText(failed));
    if (linked) setMessage("Kontoen er forbundet. Næste gang kan du logge ind med den.");
    q.delete("linked");
    q.delete("sso_error");
    const rest = q.toString();
    window.history.replaceState(window.history.state, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
    document.getElementById("forbundne-logins")?.scrollIntoView({ block: "start" });
  }, []);

  const connect = async (row: Row) => {
    setError(null);
    setMessage(null);
    setBusy(row.key);
    try {
      const r = await api.post<{ url: string }>(`/api/me/identities/${row.key}/link`);
      window.location.assign(r.url);
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  };
  const remove = async (row: Row) => {
    if (!window.confirm(`Fjern forbindelsen til ${row.name}? Du kan stadig logge ind med en kode på e-mail.`)) return;
    setError(null);
    setMessage(null);
    setBusy(row.key);
    try {
      await api.del(`/api/me/identities/${row.key}`);
      setMessage(`${row.name} er fjernet.`);
      await list.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  if (list.error) return null;
  if (!list.data) return <div className="card"><Loading /></div>;
  if (list.data.providers.length === 0) return null;   // no provider is switched on
  return (
    <div className="card" id="forbundne-logins" data-deeplink="account-linked-logins">
      <span className="eyebrow">Forbundne logins</span>
      <p className="muted small" style={{ margin: 0 }}>
        Forbind en konto, du allerede har, så du kan logge ind uden kode. Det er frivilligt. Du kan altid logge ind med en kode på e-mail.
      </p>
      <Alert kind="success">{message}</Alert>
      <Alert>{error}</Alert>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, border: "1px solid var(--hm-border)", borderRadius: 14, overflow: "hidden" }}>
        {list.data.providers.map((row, i) => (
          <li key={row.key} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14, padding: "14px 16px", borderTop: i ? "1px solid var(--hm-border)" : 0 }}>
            <span style={{ flex: "none", width: 40, height: 40, borderRadius: 10, border: "1px solid var(--hm-border)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <ProviderIcon kind={row.kind} size={18} />
            </span>
            <div style={{ flex: 1, minWidth: 200, display: "flex", flexDirection: "column" }}>
              <span style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, fontWeight: 600 }}>
                {row.name}{row.kind === "saml" ? " (SSO)" : ""}
                {row.linked && <span className="pill green">Forbundet</span>}
                {row.linked && !row.enabled && <span className="pill">Slået fra</span>}
              </span>
              <span className="small muted">
                {row.linked
                  ? `${row.linked.email ?? "Konto"} · ${row.linked.last_used_at ? `sidst brugt ${fmtDate(row.linked.last_used_at)}` : `forbundet ${fmtDate(row.linked.created_at)}`}`
                  : row.available ? "Ikke forbundet" : `Kun for e-mails fra ${row.domains.join(", ")}`}
              </span>
            </div>
            {row.linked ? (
              <button type="button" className="btn btn-outline btn-sm" style={{ color: "#b91c1c", borderColor: "#fecaca" }} disabled={busy === row.key} onClick={() => remove(row)}>Fjern</button>
            ) : row.available ? (
              <button type="button" className="btn btn-outline btn-sm" disabled={busy === row.key} onClick={() => connect(row)}>Forbind</button>
            ) : (
              <span className="small muted">Ikke relevant for dig</span>
            )}
          </li>
        ))}
      </ul>
      <div role="note" className="small" style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: "12px 14px", borderRadius: 12, border: "1px solid var(--hm-border)", background: "var(--hm-muted)" }}>
        <span>
          At forbinde eller fjerne et login kræver en kode på e-mail eller din passkey. Har du slået "Kræv passkey" til, beder vi stadig om den
          efter udbyderen. Forbindelsen kommer med i din dataeksport og slettes, hvis du sletter din konto.
        </span>
      </div>
    </div>
  );
}
