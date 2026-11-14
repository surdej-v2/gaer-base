// /admin/login-providers — Login-udbydere (server/app/login_providers.py). Each provider is off until an admin switches
// it on; the one-time code always works. A provider replaces only the code: /admin still needs a second factor and
// "Kræv passkey" still applies. Changes need a step-up (the api client opens the dialog) and are audited.
import { useEffect, useState, type CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Info, LogIn, Plus } from "lucide-react";
import { api, errorText } from "../../../api";
import { Alert, fmtDate, Loading, PageTitle, Toggle, useLoad, useToast } from "../../ui";
import {
  CertPill, fieldLabel, h2, hint, inputStyle, KIND_LABEL, ProviderMark, section, StatusPill, whoText,
  type Kind, type ProviderSummary, type Rules,
} from "./shared";

type Catalog = { kind: Kind; label: string; subtitle: string; audience: string; protocol: string; set_up: boolean };
type Event = { ts: string; action: string; row_id: string | null; detail: Record<string, unknown>; provider_name: string | null; kind: Kind | null };
type Overview = { providers: ProviderSummary[]; catalog: Catalog[]; rules: Rules; events: Event[] };

const EVENT_TEXT: Record<string, string> = {
  "login_provider.created": "sat op",
  "login_provider.updated": "ændret",
  "login_provider.enabled": "slået til",
  "login_provider.disabled": "slået fra",
  "login_provider.tested": "testlogin kørt",
  "login_provider.deleted": "slettet",
  "login_provider.metadata": "metadata hentet",
  "login_provider.rules": "regler ændret",
  "login_provider.cert_expiring": "certifikat udløber",
};
const DOT: Record<string, string> = {
  "login_provider.cert_expiring": "#f97316", "login_provider.enabled": "#16a34a", "login_provider.disabled": "#94a3b8",
};

const grid = "minmax(220px, 2fr) minmax(130px, 1fr) minmax(180px, 2fr) 70px 150px";
const cellHead: CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "#475569" };

function ProviderRow({ p, onToggle }: { p: ProviderSummary; onToggle: (p: ProviderSummary) => void }) {
  return (
    <div className="lp-row" style={{ display: "grid", gridTemplateColumns: grid, gap: 14, alignItems: "center", padding: "14px 18px", borderTop: "1px solid #e2e8f0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
        <ProviderMark kind={p.kind} />
        <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
          <Link to={`/login-providers/${p.key}`} style={{ fontWeight: 700, textDecoration: "none" }}>{p.kind === "saml" ? p.name : KIND_LABEL[p.kind]}</Link>
          <span style={{ fontSize: 13, color: "#475569" }}>{p.kind === "saml" ? "SAML 2.0" : p.subtitle}</span>
        </div>
      </div>
      <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        <StatusPill status={p.status} />
        {p.kind === "saml" && <CertPill days={p.cert_days} />}
      </span>
      <span style={{ fontSize: 13, color: "#334155" }}>{whoText(p)}</span>
      <span style={{ fontSize: 14, fontWeight: 600 }}>{p.linked || "–"}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 12, justifyContent: "flex-end" }}>
        <Link to={`/login-providers/${p.key}`} style={{ fontSize: 13, fontWeight: 600 }}>Redigér</Link>
        {p.status === "incomplete" && !p.enabled
          ? <span style={{ fontSize: 12, color: "#475569" }} title={`Mangler: ${p.missing.join(", ")}`}>Mangler opsætning</span>
          : <Toggle on={p.enabled} onChange={() => onToggle(p)} label={`${p.name} slået til`} />}
      </div>
    </div>
  );
}

function NotSetUp({ c, onSetUp, busy }: { c: Catalog; onSetUp: (kind: Kind) => void; busy: boolean }) {
  return (
    <div className="lp-row" style={{ display: "grid", gridTemplateColumns: grid, gap: 14, alignItems: "center", padding: "14px 18px", borderTop: "1px solid #e2e8f0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
        <ProviderMark kind={c.kind} />
        <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
          <span style={{ fontWeight: 700 }}>{c.label}</span>
          <span style={{ fontSize: 13, color: "#475569" }}>{c.subtitle}</span>
        </div>
      </div>
      <span><span style={{ display: "inline-flex", padding: "3px 10px", borderRadius: 9999, background: "#f1f5f9", color: "#475569", fontSize: 12, fontWeight: 700 }}>Ikke sat op</span></span>
      <span style={{ fontSize: 13, color: "#475569" }}>{c.audience}</span>
      <span style={{ fontSize: 14, color: "#475569" }}>–</span>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button type="button" className="btn btn-outline btn-sm" disabled={busy} onClick={() => onSetUp(c.kind)}>Sæt op</button>
      </div>
    </div>
  );
}

function RulesCard({ rules, providers, onSaved }: { rules: Rules; providers: ProviderSummary[]; onSaved: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState<Rules>(rules);
  const [domain, setDomain] = useState("");
  const [provider, setProvider] = useState(providers[0]?.key ?? "");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setForm(rules), [rules]);
  useEffect(() => { if (!provider && providers[0]) setProvider(providers[0].key); }, [providers, provider]);
  const dirty = JSON.stringify(form) !== JSON.stringify(rules);
  const name = (key: string) => providers.find((p) => p.key === key)?.name ?? key;
  const save = async () => {
    setError(null);
    try {
      await api.put("/api/admin/login-providers/rules", form);
      toast("Reglerne er gemt");
      onSaved();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const add = () => {
    const d = domain.trim().toLowerCase().replace(/^@/, "");
    if (!d || !provider || form.required.some((r) => r.domain === d)) return;
    setForm({ ...form, required: [...form.required, { domain: d, provider }] });
    setDomain("");
  };
  return (
    <section aria-labelledby="h-regler" style={section}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h2 id="h-regler" style={h2}>Regler for alle udbydere</h2>
        <p style={hint}>Gælder for hver udbyder, der er slået til. Ændringer kræver anden faktor og logges.</p>
      </div>
      <Alert>{error}</Alert>
      <label style={fieldLabel}>Kobling til en eksisterende konto
        <select style={inputStyle} value={form.link} onChange={(e) => setForm({ ...form, link: e.target.value as Rules["link"] })}>
          <option value="verified_email">Kun på en e-mail, udbyderen har bekræftet</option>
          <option value="user_linked">Kun når brugeren selv har forbundet kontoen</option>
        </select>
      </label>
      <label style={fieldLabel}>Nye brugere fra en udbyder
        <select style={inputStyle} value={form.new_users} onChange={(e) => setForm({ ...form, new_users: e.target.value as Rules["new_users"] })}>
          <option value="signup">Følg Tilmelding (Indstillinger)</option>
          <option value="never">Aldrig – kun eksisterende brugere</option>
        </select>
      </label>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Kræv udbyder for et domæne</span>
        {form.required.map((r) => (
          <div key={r.domain} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 10, border: "1px solid #e2e8f0", background: "#f8fafc", fontSize: 13 }}>
            <span style={{ fontFamily: "var(--hm-font-mono)", fontWeight: 600 }}>{r.domain}</span>
            <span aria-hidden="true" style={{ color: "#475569" }}>→</span>
            <span style={{ flex: 1 }}>{name(r.provider)}</span>
            <button type="button" className="btn btn-ghost btn-sm" style={{ color: "#b91c1c" }}
                    onClick={() => setForm({ ...form, required: form.required.filter((x) => x.domain !== r.domain) })}>Fjern</button>
          </div>
        ))}
        {providers.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            <input aria-label="Domæne" placeholder="firma.dk" value={domain} onChange={(e) => setDomain(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
                   style={{ ...inputStyle, flex: "1 1 140px", fontFamily: "var(--hm-font-mono)" }} />
            <select aria-label="Udbyder" value={provider} onChange={(e) => setProvider(e.target.value)} style={{ ...inputStyle, flex: "1 1 160px" }}>
              {providers.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
            </select>
            <button type="button" className="btn btn-outline btn-sm" onClick={add} disabled={!domain.trim()}>Tilføj</button>
          </div>
        )}
        <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5, color: "#475569" }}>
          Brugere fra domænet sendes direkte til udbyderen og får ikke en engangskode. Super admin kan altid bruge engangskoden som nødadgang.
        </p>
      </div>
      <div className="row"><button type="button" className="btn btn-primary btn-sm" disabled={!dirty} onClick={save}>Gem regler</button></div>
    </section>
  );
}

export default function LoginProviders() {
  const toast = useToast();
  const nav = useNavigate();
  const data = useLoad(() => api.get<Overview>("/api/admin/login-providers"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async (kind: Kind, name?: string) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ key: string }>("/api/admin/login-providers", { kind, name });
      nav(`/login-providers/${r.key}`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const addSaml = () => {
    const name = window.prompt("Navn på forbindelsen (fx organisationens navn)", "");
    if (name && name.trim()) create("saml", name.trim());
  };
  const toggle = async (p: ProviderSummary) => {
    setError(null);
    try {
      await api.post(`/api/admin/login-providers/${p.key}/enabled`, { enabled: !p.enabled });
      toast(p.enabled ? `${p.name} er slået fra` : `${p.name} er slået til`);
      await data.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  if (data.error) return <Alert>{data.error}</Alert>;
  if (!data.data) return <Loading />;
  const { providers, catalog, rules, events } = data.data;
  const notSetUp = catalog.filter((c) => !c.set_up && c.kind !== "saml");
  const total = providers.length + notSetUp.length;
  const on = providers.filter((p) => p.enabled).length;

  return (
    <div className="stack" style={{ "--gap": "24px" } as CSSProperties}>
      <style>{`@media (max-width: 900px) { .lp-row { grid-template-columns: 1fr !important; } .lp-head { display: none !important; } }`}</style>
      <PageTitle title="Login-udbydere"
        aside={
          <div className="row">
            <Link to="/logs/auth" className="btn btn-outline btn-sm"><LogIn size={14} aria-hidden="true" />Auth-log</Link>
            <button type="button" className="btn btn-primary btn-sm" onClick={addSaml} disabled={busy}><Plus size={14} aria-hidden="true" />Tilføj SAML-forbindelse</button>
          </div>
        }>
        Lad brugerne logge ind med en konto, de allerede har. Hver udbyder er slået fra, til du selv slår den til. Login med e-mail og engangskode virker altid.
      </PageTitle>

      <div role="note" style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: "14px 16px", borderRadius: 12, border: "1px solid #bfdbfe", background: "#eff6ff", color: "#1e3a8a", fontSize: 14, lineHeight: 1.5 }}>
        <Info size={18} style={{ flex: "none", marginTop: 2 }} aria-hidden="true" />
        <span><strong>En udbyder erstatter kun engangskoden.</strong> /admin kræver stadig en anden faktor (passkey, sikkerhedsnøgle eller authenticator-app), og "Kræv passkey" gælder fortsat for de brugere, der har slået det til. Admin-rollen kan aldrig gives af en udbyder.</span>
      </div>
      <Alert>{error}</Alert>

      <section aria-labelledby="h-udbydere" className="stack" style={{ "--gap": "12px" } as CSSProperties}>
        <div className="spread">
          <h2 id="h-udbydere" style={{ margin: 0, fontSize: 18 }}>Udbydere</h2>
          <span className="muted small">{on} af {total} slået til</span>
        </div>
        <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 16, overflow: "hidden" }}>
          <div className="lp-head" style={{ display: "grid", gridTemplateColumns: grid, gap: 14, padding: "10px 18px", background: "#f8fafc" }}>
            <span style={cellHead}>Udbyder</span><span style={cellHead}>Status</span><span style={cellHead}>Hvem må bruge den</span>
            <span style={cellHead}>Koblet</span><span style={{ ...cellHead, textAlign: "right" }}>Slået til</span>
          </div>
          {providers.map((p) => <ProviderRow key={p.key} p={p} onToggle={toggle} />)}
          {notSetUp.map((c) => <NotSetUp key={c.kind} c={c} onSetUp={(k) => create(k)} busy={busy} />)}
          {!providers.some((p) => p.kind === "saml") && (
            <NotSetUp c={catalog.find((c) => c.kind === "saml")!} onSetUp={addSaml} busy={busy} />
          )}
        </div>
      </section>

      <div className="grid" style={{ "--min": "380px", alignItems: "start" } as CSSProperties}>
        <RulesCard rules={rules} providers={providers} onSaved={data.reload} />
        <section aria-labelledby="h-log" style={section}>
          <div className="spread">
            <h2 id="h-log" style={h2}>Seneste hændelser</h2>
            <Link to="/logs/audit" style={{ fontSize: 13, fontWeight: 600 }}>Åbn audit-log</Link>
          </div>
          {events.length === 0 && <p style={hint}>Ingen ændringer endnu.</p>}
          <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 12 }}>
            {events.map((e, i) => (
              <li key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                <span style={{ flex: "none", width: 8, height: 8, marginTop: 7, borderRadius: 9999, background: DOT[e.action] ?? "#2563eb" }} />
                <div style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 14 }}>
                  <span>
                    <strong>{e.provider_name ?? (e.action === "login_provider.rules" ? "Regler" : "Udbyder")} {EVENT_TEXT[e.action] ?? e.action}</strong>
                    {e.action === "login_provider.cert_expiring" && typeof e.detail.days === "number" ? ` · om ${e.detail.days} dage` : ""}
                  </span>
                  <span style={{ fontSize: 12, color: "#475569", fontFamily: "var(--hm-font-mono)" }}>{e.action} · {fmtDate(e.ts)}</span>
                </div>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </div>
  );
}
