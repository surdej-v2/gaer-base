// /admin/login-providers/:key — setting up one provider: Microsoft Entra ID, Google, GitHub, Amazon (OpenID Connect /
// OAuth 2.0) or a SAML 2.0 connection. Tabs: Opsætning, Koblede brugere, Log. Saving, the test login and switching on
// need a step-up (the api client opens the dialog). The client secret is write-only: the page never sees its value.
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Check, Copy, Download, ExternalLink, X } from "lucide-react";
import { api, errorText } from "../../../api";
import { Alert, copyText, fmtDate, fmtDay, Loading, useLoad, useToast } from "../../ui";
import {
  CertPill, codeBox, CONSOLE, fieldLabel, h2, hint, inputStyle, KIND_LABEL, MicrosoftMark, ProviderMark, section, StatusPill,
  type Config, type GroupMap, type ProviderDetail,
} from "./shared";

type Tab = "setup" | "users" | "log";
type LinkedUser = { id: number; email: string | null; display_name: string | null; role: string; blocked_at: string | null;
                    provider_email: string | null; linked_via: string; created_at: string; last_used_at: string | null };
type LogData = { auth: { ts: string; event: string; result: string; user_id: number | null; contact: string | null; device: string | null; reason: string | null }[];
                 changes: { ts: string; action: string; actor_user_id: number | null; before: unknown; after: unknown }[] };

const VIA: Record<string, string> = { verified_email: "Bekræftet e-mail", user: "Brugeren selv", signup: "Ny konto" };

function CopyRow({ label, value }: { label: string; value: string }) {
  const toast = useToast();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: "#475569" }}>{label}</span>
      <div style={{ display: "flex", gap: 8 }}>
        <code style={codeBox}>{value}</code>
        <button type="button" aria-label={`Kopiér ${label}`} title="Kopiér" className="btn btn-outline btn-sm" style={{ flex: "none", width: 36, padding: 0 }}
                onClick={() => { copyText(value); toast("Kopieret"); }}>
          <Copy size={14} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function Step({ n, done, title, children, id }: { n: number; done: boolean; title: string; children: ReactNode; id: string }) {
  return (
    <section aria-labelledby={id} style={{ ...section, flexDirection: "row", gap: 16 }}>
      <span style={{ flex: "none", width: 28, height: 28, borderRadius: 9999, display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 13, fontWeight: 700, background: done ? "#16a34a" : "#f1f5f9", color: done ? "#ffffff" : "#475569" }}>
        {done ? <Check size={15} aria-hidden="true" /> : n}
      </span>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 14 }}>
        <h2 id={id} style={h2}>{n}. {title}</h2>
        {children}
      </div>
    </section>
  );
}

function Domains({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const d = draft.trim().toLowerCase().replace(/^@/, "");
    if (d && !value.includes(d)) onChange([...value, d]);
    setDraft("");
  };
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", padding: 6, border: "1px solid #e2e8f0", borderRadius: 8, minHeight: 36 }}>
      {value.map((d) => (
        <span key={d} style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 4px 2px 10px", borderRadius: 9999, background: "#f1f5f9", fontSize: 13, fontFamily: "var(--hm-font-mono)" }}>
          {d}
          <button type="button" aria-label={`Fjern ${d}`} onClick={() => onChange(value.filter((x) => x !== d))}
                  style={{ border: 0, background: "transparent", cursor: "pointer", width: 22, height: 22, display: "flex", alignItems: "center", justifyContent: "center", color: "#475569" }}>
            <X size={12} aria-hidden="true" />
          </button>
        </span>
      ))}
      <input aria-label="Tilføj domæne" placeholder="Tilføj domæne" value={draft} onChange={(e) => setDraft(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }} onBlur={add}
             style={{ flex: 1, minWidth: 120, border: 0, outline: "none", font: "13px var(--hm-font-mono)", height: 26 }} />
    </div>
  );
}

function GroupMapEditor({ value, groups, onChange, claimLabel }: { value: GroupMap[]; groups: ProviderDetail["groups"]; onChange: (v: GroupMap[]) => void; claimLabel: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="spread">
        <span style={{ fontSize: 13, fontWeight: 600 }}>{claimLabel} → Gær-grupper <span style={{ fontWeight: 400, color: "#475569" }}>(valgfrit)</span></span>
        <button type="button" className="btn btn-outline btn-sm" disabled={!groups.length} onClick={() => onChange([...value, { claim: "", group_id: groups[0]?.id ?? 0 }])}>+ Tilføj</button>
      </div>
      {!groups.length && <p style={hint}>Der er ingen grupper endnu. Opret dem under <Link to="/groups">Grupper</Link>.</p>}
      {value.map((m, i) => (
        <div key={i} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <input aria-label="Gruppe hos udbyderen" placeholder="sg-salg eller gruppens id" value={m.claim}
                 onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, claim: e.target.value } : x)))}
                 style={{ ...inputStyle, flex: "1 1 160px", fontFamily: "var(--hm-font-mono)", fontSize: 13 }} />
          <span aria-hidden="true" style={{ color: "#475569" }}>→</span>
          <select aria-label="Gær-gruppe" value={m.group_id} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, group_id: Number(e.target.value) } : x)))}
                  style={{ ...inputStyle, flex: "1 1 140px" }}>
            {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
          <button type="button" aria-label="Fjern linjen" className="btn btn-ghost btn-sm" onClick={() => onChange(value.filter((_, j) => j !== i))}><X size={14} aria-hidden="true" /></button>
        </div>
      ))}
      <p style={{ margin: 0, fontSize: 12, color: "#475569" }}>Synkroniseres ved hvert login. Kun medlemskaber, som udbyderen selv har givet, fjernes igen. Admin-rollen og admin-roller kan ikke gives herfra.</p>
    </div>
  );
}

function TestResult({ test }: { test: ProviderDetail["last_test"] }) {
  if (!test) return null;
  return test.ok ? (
    <div role="status" style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 12px", borderRadius: 10, background: "rgba(22,163,74,0.08)", border: "1px solid #bbf7d0", color: "#166534", fontSize: 13 }}>
      <Check size={16} style={{ flex: "none", marginTop: 1 }} aria-hidden="true" />
      <span>
        <strong>Testlogin lykkedes</strong> {fmtDate(test.at)} · claims: <code>{test.claims.join(", ")}</code>
        {test.email_domain && <> · e-mail på {test.email_domain} {test.email_verified ? "(bekræftet)" : "(ikke bekræftet: kobles kun af brugeren selv)"}</>}
        {test.groups > 0 && <> · {test.groups} grupper</>}
      </span>
    </div>
  ) : (
    <Alert>Testlogin mislykkedes {fmtDate(test.at)}: {test.error}</Alert>
  );
}

function Aside({ p, form, setForm, onToggle }: { p: ProviderDetail; form: Config; setForm: (c: Config) => void; onToggle: () => void }) {
  return (
    <aside style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <section aria-labelledby="a1" style={section}>
        <h2 id="a1" style={h2}>Knappen på /login</h2>
        <span style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, height: 42, borderRadius: 8, border: "1px solid #e2e8f0", fontWeight: 600 }}>
          {p.kind === "entra" ? <MicrosoftMark /> : <ProviderMark kind={p.kind} size={22} />}
          {form.button_label || p.default_button}
        </span>
        <label style={fieldLabel}>Tekst
          <input value={form.button_label} maxLength={60} onChange={(e) => setForm({ ...form, button_label: e.target.value })} style={inputStyle} />
        </label>
        <label style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 13 }}>
          <input type="checkbox" checked={form.only_matching_domain} onChange={(e) => setForm({ ...form, only_matching_domain: e.target.checked })} style={{ marginTop: 2 }} />
          <span>Vis kun, når e-mailen matcher et tilladt domæne</span>
        </label>
      </section>
      <section aria-labelledby="a2" style={section}>
        <h2 id="a2" style={h2}>Drift</h2>
        <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: "8px 14px", fontSize: 13 }}>
          <dt style={{ color: "#475569" }}>Sidste login</dt><dd style={{ margin: 0 }}>{fmtDate(p.stats.last_login_at)}</dd>
          <dt style={{ color: "#475569" }}>Fejl, 24 t</dt><dd style={{ margin: 0 }}>{p.stats.errors_24h}</dd>
          <dt style={{ color: "#475569" }}>Koblede brugere</dt><dd style={{ margin: 0 }}>{p.linked}</dd>
          {p.kind === "saml"
            ? <><dt style={{ color: "#475569" }}>Metadata</dt><dd style={{ margin: 0 }}>{p.idp?.fetched_at ? `hentet ${fmtDate(p.idp.fetched_at)}` : "indtastet"}</dd></>
            : <><dt style={{ color: "#475569" }}>Hemmelighed</dt><dd style={{ margin: 0 }}>{p.client_secret_set_at ? `sat ${fmtDay(p.client_secret_set_at)}` : "ikke sat"}</dd></>}
        </dl>
      </section>
      <section aria-labelledby="a3" style={{ ...section, borderColor: p.enabled ? "#fecaca" : "#e2e8f0" }}>
        <h2 id="a3" style={h2}>{p.enabled ? `Slå ${p.name} fra` : `Slå ${p.name} til`}</h2>
        <p style={hint}>
          {p.enabled
            ? `Knappen forsvinder fra /login. De ${p.linked} koblede brugere logger ind med engangskode. Igangværende sessioner fortsætter.`
            : p.missing.length ? `Mangler: ${p.missing.join(", ")}.` : "Knappen kommer på /login for de domæner, du har valgt. Kræver anden faktor."}
        </p>
        <button type="button" className={`btn btn-sm ${p.enabled ? "btn-outline" : "btn-primary"}`} style={p.enabled ? { color: "#b91c1c", borderColor: "#fecaca" } : undefined}
                disabled={!p.enabled && p.missing.length > 0} onClick={onToggle}>
          {p.enabled ? "Slå fra" : "Slå til"}
        </button>
      </section>
    </aside>
  );
}

function OidcSetup({ p, form, setForm, secret, setSecret, onTest }: {
  p: ProviderDetail; form: Config; setForm: (c: Config) => void; secret: string; setSecret: (s: string) => void; onTest: () => void;
}) {
  const consoleLink = CONSOLE[p.kind];
  const connected = !!form.client_id && p.secret_set && (p.kind !== "entra" || (form.tenant_mode === "multi" ? !!form.tenants?.length : !!form.tenant_id));
  const [tenantDraft, setTenantDraft] = useState((form.tenants ?? []).join(", "));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <Step n={1} id="s1" done={!!form.client_id} title={`Registrér Gær hos ${KIND_LABEL[p.kind]}`}>
        <p style={hint}>
          {p.kind === "entra" && <>Opret en app under <em>App registrations</em> i Entra-portalen, og indsæt disse adresser. </>}
          {p.kind === "google" && <>Opret en OAuth-klient af typen <em>Web application</em>, og indsæt redirect-adressen. </>}
          {p.kind === "github" && <>Opret en OAuth App (eller GitHub App) og indsæt callback-adressen. </>}
          {p.kind === "amazon" && <>Opret en sikkerhedsprofil i Login with Amazon, og indsæt adressen under Allowed Return URLs. </>}
          De er faste for denne instans.
        </p>
        <CopyRow label={p.kind === "entra" ? "Redirect URI (Web)" : "Redirect URI"} value={p.urls.redirect_uri} />
        {p.kind === "entra" && <CopyRow label="Front-channel logout URL" value={p.urls.logout} />}
        {consoleLink && (
          <a href={consoleLink[1]} target="_blank" rel="noreferrer noopener" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 600 }}>
            {consoleLink[0]}<ExternalLink size={13} aria-hidden="true" />
          </a>
        )}
      </Step>

      <Step n={2} id="s2" done={connected} title="Forbind">
        <div className="grid" style={{ "--min": "220px", "--gap": "12px" } as CSSProperties}>
          {p.kind === "entra" && (
            <label style={fieldLabel}>Hvem kan logge ind
              <select value={form.tenant_mode} onChange={(e) => setForm({ ...form, tenant_mode: e.target.value as Config["tenant_mode"] })} style={inputStyle}>
                <option value="single">Kun jeres egen tenant (anbefalet)</option>
                <option value="multi">Flere udvalgte tenants</option>
              </select>
            </label>
          )}
          {p.kind === "entra" && form.tenant_mode !== "multi" && (
            <label style={fieldLabel}>Tenant-id
              <input value={form.tenant_id ?? ""} placeholder="GUID eller firma.onmicrosoft.com" onChange={(e) => setForm({ ...form, tenant_id: e.target.value.trim() })}
                     style={{ ...inputStyle, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} />
            </label>
          )}
          {p.kind === "entra" && form.tenant_mode === "multi" && (
            <label style={fieldLabel}>Tenant-id'er (adskilt af komma)
              <input value={tenantDraft} onChange={(e) => { setTenantDraft(e.target.value); setForm({ ...form, tenants: e.target.value.split(/[\s,;]+/).filter(Boolean) }); }}
                     style={{ ...inputStyle, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} />
            </label>
          )}
          <label style={fieldLabel}>{p.kind === "entra" ? "Klient-id (Application ID)" : "Klient-id"}
            <input value={form.client_id ?? ""} onChange={(e) => setForm({ ...form, client_id: e.target.value.trim() })}
                   style={{ ...inputStyle, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} />
          </label>
          {p.kind === "google" && (
            <label style={fieldLabel}>Workspace-domæne (valgfrit)
              <input value={form.hosted_domain ?? ""} placeholder="firma.dk" onChange={(e) => setForm({ ...form, hosted_domain: e.target.value.trim() })}
                     style={{ ...inputStyle, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} />
            </label>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 500 }}>Klienthemmelighed</span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", padding: "10px 12px", borderRadius: 10, border: "1px solid #e2e8f0", background: "#f8fafc" }}>
            <span style={{ display: "flex", flexDirection: "column", flex: "1 1 200px", minWidth: 0 }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{p.secret_set ? "Gemt krypteret på serveren" : "Ikke sat endnu"}</span>
              <span style={{ fontSize: 12, color: "#475569" }}>{p.client_secret_set_at ? `Sat ${fmtDate(p.client_secret_set_at)}` : "Kopiér værdien fra udbyderen"}</span>
            </span>
            <input type="password" autoComplete="new-password" aria-label="Ny klienthemmelighed" placeholder={p.secret_set ? "Skift: indsæt ny værdi" : "Indsæt værdien"}
                   value={secret} onChange={(e) => setSecret(e.target.value)} style={{ ...inputStyle, flex: "1 1 220px" }} />
          </div>
          <p style={{ margin: 0, fontSize: 12, color: "#475569" }}>Værdien vises aldrig her og kommer aldrig i logs eller webhooks.</p>
        </div>
      </Step>

      <Step n={3} id="s3" done={form.domains.length > 0} title="Hvem må bruge den">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 500 }}>Tilladte e-maildomæner</span>
          <Domains value={form.domains} onChange={(domains) => setForm({ ...form, domains })} />
          <span style={{ fontSize: 12, color: "#475569" }}>
            {p.kind === "entra" ? "Tom: alle e-mails i tenanten. E-mails på domænerne her regnes som bekræftede, så de kan kobles til en eksisterende konto." : "Tom: alle e-mails. Underdomæner tæller med."}
            {p.kind === "amazon" && " Amazon oplyser ikke, om e-mailen er bekræftet: brugerne forbinder selv kontoen under Konto."}
          </span>
        </div>
        <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
          <legend style={{ fontSize: 13, fontWeight: 500, paddingBottom: 6 }}>Første login fra en ny person</legend>
          {([["existing", "Kun eksisterende brugere", `Kobles på den e-mail, ${KIND_LABEL[p.kind]} har bekræftet. Andre får besked om at kontakte en admin.`],
             ["signup", "Opret en konto", "Kun for tilladte domæner, og kun hvis Tilmelding i Indstillinger tillader det. Rollen er altid bruger."]] as const).map(([v, t, d]) => {
            const on = form.new_users === v;
            return (
              <label key={v} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 12px", borderRadius: 10, cursor: "pointer", fontSize: 13,
                border: on ? "2px solid #2563eb" : "1px solid #e2e8f0", background: on ? "#eff6ff" : "#ffffff" }}>
                <input type="radio" name="ny" checked={on} onChange={() => setForm({ ...form, new_users: v })} style={{ marginTop: 2 }} />
                <span style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontWeight: 600 }}>{t}</span><span style={{ color: "#475569" }}>{d}</span></span>
              </label>
            );
          })}
        </fieldset>
        {p.can_map_groups && (
          <GroupMapEditor value={form.group_map ?? []} groups={p.groups} claimLabel="Entra-grupper" onChange={(group_map) => setForm({ ...form, group_map })} />
        )}
      </Step>

      <Step n={4} id="s4" done={p.enabled} title="Test og slå til">
        <p style={hint}>Testlogin åbner {KIND_LABEL[p.kind]} og logger ikke nogen ind. Gem, test og slå til kræver anden faktor.</p>
        <TestResult test={p.last_test} />
        <div className="row">
          <button type="button" className="btn btn-outline btn-sm" disabled={p.missing.length > 0} onClick={onTest}>Kør testlogin</button>
        </div>
        <p style={{ margin: 0, fontSize: 12, color: "#475569" }}>Logges i audit-loggen og sender webhook <code>auth.provider.updated</code> (kun id'er).</p>
      </Step>
    </div>
  );
}

function SamlSetup({ p, form, setForm, idp, setIdp, onReload }: {
  p: ProviderDetail; form: Config; setForm: (c: Config) => void;
  idp: { entity_id: string; sso_url: string; cert: string }; setIdp: (v: { entity_id: string; sso_url: string; cert: string }) => void; onReload: () => void;
}) {
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const source = form.metadata_source ?? "url";
  const fetchNow = async (xml?: string) => {
    setError(null);
    try {
      if (!xml && form.metadata_url !== (p.config.metadata_url ?? "")) {
        await api.put(`/api/admin/login-providers/${p.key}`, { name: p.name, config: form });
      }
      await api.post(`/api/admin/login-providers/${p.key}/metadata`, { xml: xml ?? null });
      toast("Metadata er hentet");
      onReload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const upload = async (file: File | undefined) => {
    if (file) await fetchNow(await file.text());
  };
  const attr = (key: "attr_email" | "attr_name" | "attr_groups", label: string, required = false) => (
    <div style={{ display: "grid", gridTemplateColumns: "110px minmax(0, 1fr)", gap: 10, alignItems: "center", padding: "8px 12px", borderTop: "1px solid #e2e8f0", fontSize: 13 }}>
      <span style={{ fontWeight: 600 }}>{label}{required ? " *" : ""}</span>
      <input aria-label={`Attribut for ${label.toLowerCase()}`} value={form[key] ?? ""} onChange={(e) => setForm({ ...form, [key]: e.target.value })}
             style={{ ...inputStyle, height: 32, fontFamily: "var(--hm-font-mono)", fontSize: 12 }} />
    </div>
  );
  return (
    <div className="grid" style={{ "--min": "440px", alignItems: "start" } as CSSProperties}>
      <section aria-labelledby="sp" style={section}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h2 id="sp" style={h2}>Gærs oplysninger til jeres IdP</h2>
          <p style={hint}>Giv dem til den, der driver ADFS, Entra, Okta eller anden IdP.</p>
        </div>
        <CopyRow label="Entity ID" value={p.urls.entity_id} />
        <CopyRow label="Assertion Consumer Service (HTTP-POST)" value={p.urls.acs} />
        <CopyRow label="Single Logout (HTTP-Redirect)" value={p.urls.slo} />
        <div className="row" style={{ "--gap": "8px" } as CSSProperties}>
          <a className="btn btn-outline btn-sm" href={p.urls.metadata} target="_blank" rel="noreferrer"><Download size={14} aria-hidden="true" />Hent SP-metadata (XML)</a>
          <a className="btn btn-outline btn-sm" href={p.urls.cert}>Hent SP-certifikat</a>
        </div>
      </section>

      <section aria-labelledby="idp" style={section}>
        <h2 id="idp" style={h2}>Jeres IdP</h2>
        <div role="tablist" aria-label="Kilde til IdP-metadata" style={{ display: "inline-flex", alignSelf: "flex-start", padding: 3, borderRadius: 9, background: "#f1f5f9", gap: 2 }}>
          {([["url", "Metadata-URL"], ["xml", "Upload XML"], ["manual", "Manuelt"]] as const).map(([v, t]) => (
            <button key={v} type="button" role="tab" aria-selected={source === v} onClick={() => setForm({ ...form, metadata_source: v })}
                    style={{ height: 30, padding: "0 12px", border: 0, borderRadius: 7, cursor: "pointer", font: `${source === v ? 600 : 500} 13px var(--hm-font-sans)`,
                      background: source === v ? "#ffffff" : "transparent", color: source === v ? "#020817" : "#475569", boxShadow: source === v ? "0 1px 2px rgba(0,0,0,0.06)" : "none" }}>
              {t}
            </button>
          ))}
        </div>
        <Alert>{error}</Alert>
        {source === "url" && (
          <label style={fieldLabel}>Metadata-URL
            <span style={{ display: "flex", gap: 8 }}>
              <input value={form.metadata_url ?? ""} onChange={(e) => setForm({ ...form, metadata_url: e.target.value.trim() })} placeholder="https://adfs.firma.dk/FederationMetadata/2007-06/FederationMetadata.xml"
                     style={{ ...inputStyle, flex: 1, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} />
              <button type="button" className="btn btn-outline btn-sm" disabled={!form.metadata_url} onClick={() => fetchNow()}>Hent</button>
            </span>
          </label>
        )}
        {source === "xml" && (
          <label style={fieldLabel}>Metadata-fil (XML)
            <input type="file" accept=".xml,application/xml,text/xml,application/samlmetadata+xml" onChange={(e) => upload(e.target.files?.[0])} />
          </label>
        )}
        {source === "manual" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label style={fieldLabel}>IdP Entity ID<input value={idp.entity_id} onChange={(e) => setIdp({ ...idp, entity_id: e.target.value })} style={{ ...inputStyle, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} /></label>
            <label style={fieldLabel}>SSO-adresse (HTTP-Redirect)<input value={idp.sso_url} onChange={(e) => setIdp({ ...idp, sso_url: e.target.value })} style={{ ...inputStyle, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} /></label>
            <label style={fieldLabel}>Signeringscertifikat (PEM)
              <textarea value={idp.cert} rows={4} onChange={(e) => setIdp({ ...idp, cert: e.target.value })} placeholder="-----BEGIN CERTIFICATE-----"
                        style={{ ...inputStyle, height: "auto", padding: 8, fontFamily: "var(--hm-font-mono)", fontSize: 12 }} />
            </label>
            <span style={{ fontSize: 12, color: "#475569" }}>Gemmes med "Gem ændringer".</span>
          </div>
        )}
        {p.idp && (p.idp.entity_id || p.idp.sso_url) && (
          <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: "8px 14px", padding: "12px 14px", borderRadius: 10, background: "#f8fafc", border: "1px solid #e2e8f0", fontSize: 13 }}>
            <dt style={{ color: "#475569" }}>IdP Entity ID</dt><dd style={{ margin: 0, fontFamily: "var(--hm-font-mono)", overflowWrap: "anywhere" }}>{p.idp.entity_id}</dd>
            <dt style={{ color: "#475569" }}>SSO-adresse</dt><dd style={{ margin: 0, fontFamily: "var(--hm-font-mono)", overflowWrap: "anywhere" }}>{p.idp.sso_url}</dd>
            {p.idp.certs.map((c) => (
              <span key={c.sha256} style={{ display: "contents" }}>
                <dt style={{ color: "#475569" }}>Certifikat</dt>
                <dd style={{ margin: 0, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                  <span style={{ fontFamily: "var(--hm-font-mono)" }}>SHA-256 {c.sha256.slice(0, 5)}…{c.sha256.slice(-2)}</span>
                  <span className="muted tiny">udløber {fmtDay(c.not_after)}</span>
                </dd>
              </span>
            ))}
          </dl>
        )}
        {p.idp?.error && <Alert kind="warn">{p.idp.error}</Alert>}
        {source === "url" && (
          <label style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 13 }}>
            <input type="checkbox" checked={form.refresh ?? true} onChange={(e) => setForm({ ...form, refresh: e.target.checked })} style={{ marginTop: 2 }} />
            <span>Hent metadata hver nat (job <code>auth.saml_metadata</code>), og advar 30 dage før et certifikat udløber</span>
          </label>
        )}
      </section>

      <section aria-labelledby="attr" style={section}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h2 id="attr" style={h2}>Attributter</h2>
          <p style={hint}>Hvilke SAML-attributter Gær læser. E-mail er påkrævet.</p>
        </div>
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 10, overflow: "hidden" }}>
          <div style={{ display: "grid", gridTemplateColumns: "110px minmax(0, 1fr)", gap: 10, padding: "8px 12px", background: "#f8fafc", fontSize: 11, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "#475569" }}>
            <span>Gær-felt</span><span>SAML-attribut</span>
          </div>
          {attr("attr_email", "E-mail", true)}
          {attr("attr_name", "Navn")}
          {attr("attr_groups", "Grupper")}
        </div>
        <label style={fieldLabel}>NameID-format
          <select value={form.nameid_format} onChange={(e) => setForm({ ...form, nameid_format: e.target.value as Config["nameid_format"] })} style={inputStyle}>
            <option value="persistent">Persistent (anbefalet)</option>
            <option value="email">E-mailadresse</option>
          </select>
        </label>
        <GroupMapEditor value={form.group_map ?? []} groups={p.groups} claimLabel="IdP-grupper" onChange={(group_map) => setForm({ ...form, group_map })} />
      </section>

      <section aria-labelledby="sec" style={{ ...section, gap: 12 }}>
        <h2 id="sec" style={h2}>Sikkerhed</h2>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13 }}>
          <input type="checkbox" checked disabled style={{ marginTop: 2 }} />
          <span style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontWeight: 600 }}>Kræv signerede assertions</span><span style={{ color: "#475569" }}>Altid til. Kan ikke slås fra.</span></span>
        </label>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13 }}>
          <input type="checkbox" checked={!!form.want_encrypted} onChange={(e) => setForm({ ...form, want_encrypted: e.target.checked })} style={{ marginTop: 2 }} />
          <span style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontWeight: 600 }}>Kræv krypterede assertions</span><span style={{ color: "#475569" }}>IdP'en krypterer med Gærs SP-certifikat.</span></span>
        </label>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13 }}>
          <input type="checkbox" checked={!!form.allow_idp_initiated} onChange={(e) => setForm({ ...form, allow_idp_initiated: e.target.checked })} style={{ marginTop: 2 }} />
          <span style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontWeight: 600 }}>Tillad IdP-startet login</span><span style={{ color: "#475569" }}>Fra (anbefalet). Uden en forespørgsel fra Gær kan et login ikke bindes til browseren.</span></span>
        </label>
        <div style={{ height: 1, background: "#e2e8f0" }} />
        <label style={fieldLabel}>Knappens tekst på /login
          <input value={form.button_label} maxLength={60} onChange={(e) => setForm({ ...form, button_label: e.target.value })} style={inputStyle} />
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 500 }}>Send disse domæner hertil</span>
          <Domains value={form.domains} onChange={(domains) => setForm({ ...form, domains })} />
        </div>
        <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5, color: "#475569" }}>Når en bruger skriver en e-mail fra domænet på /login, vises knappen for denne forbindelse. E-mails på domænerne regnes som bekræftede.</p>
        <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
          <legend style={{ fontSize: 13, fontWeight: 500, paddingBottom: 6 }}>Første login fra en ny person</legend>
          <label style={{ display: "flex", gap: 8, fontSize: 13 }}><input type="radio" name="ny" checked={form.new_users === "existing"} onChange={() => setForm({ ...form, new_users: "existing" })} />Kun eksisterende brugere</label>
          <label style={{ display: "flex", gap: 8, fontSize: 13 }}><input type="radio" name="ny" checked={form.new_users === "signup"} onChange={() => setForm({ ...form, new_users: "signup" })} />Opret en konto (rollen er altid bruger)</label>
        </fieldset>
      </section>
    </div>
  );
}

function LinkedUsers({ p }: { p: ProviderDetail }) {
  const toast = useToast();
  const users = useLoad(() => api.get<{ users: LinkedUser[] }>(`/api/admin/login-providers/${p.key}/users`), [p.key]);
  const [error, setError] = useState<string | null>(null);
  const unlink = async (u: LinkedUser) => {
    if (!window.confirm(`Fjern koblingen for ${u.email ?? u.display_name}? Brugeren logger ind med engangskode, til kontoen kobles igen.`)) return;
    setError(null);
    try {
      await api.del(`/api/admin/login-providers/${p.key}/users/${u.id}`);
      toast("Koblingen er fjernet");
      await users.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  if (users.error) return <Alert>{users.error}</Alert>;
  if (!users.data) return <Loading />;
  return (
    <div className="stack">
      <Alert>{error}</Alert>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Bruger</th><th>Konto hos udbyderen</th><th>Koblet via</th><th>Koblet</th><th>Sidst brugt</th><th /></tr></thead>
          <tbody>
            {users.data.users.length === 0 && <tr><td colSpan={6} className="muted">Ingen brugere er koblet endnu.</td></tr>}
            {users.data.users.map((u) => (
              <tr key={u.id}>
                <td><Link to={`/users?user=${u.id}`}><strong>{u.display_name || u.email || `#${u.id}`}</strong></Link>
                  {u.role === "admin" && <span className="pill blue" style={{ marginLeft: 6 }}>admin</span>}
                  {u.blocked_at && <span className="pill red" style={{ marginLeft: 6 }}>spærret</span>}
                  <div className="small muted">{u.email}</div></td>
                <td className="mono small">{u.provider_email ?? "—"}</td>
                <td>{VIA[u.linked_via] ?? u.linked_via}</td>
                <td>{fmtDay(u.created_at)}</td>
                <td>{fmtDate(u.last_used_at)}</td>
                <td style={{ textAlign: "right" }}><button type="button" className="btn btn-ghost btn-sm" style={{ color: "#b91c1c" }} onClick={() => unlink(u)}>Fjern kobling</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ProviderLog({ p }: { p: ProviderDetail }) {
  const log = useLoad(() => api.get<LogData>(`/api/admin/login-providers/${p.key}/log`), [p.key]);
  if (log.error) return <Alert>{log.error}</Alert>;
  if (!log.data) return <Loading />;
  return (
    <div className="grid" style={{ "--min": "420px", alignItems: "start" } as CSSProperties}>
      <section style={section}>
        <h2 style={h2}>Logins</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Tid</th><th>Hændelse</th><th>Resultat</th><th>Kontakt</th></tr></thead>
            <tbody>
              {log.data.auth.length === 0 && <tr><td colSpan={4} className="muted">Ingen logins endnu.</td></tr>}
              {log.data.auth.map((a, i) => (
                <tr key={i}>
                  <td className="small">{fmtDate(a.ts)}</td>
                  <td className="mono small">{a.event}</td>
                  <td>{a.result === "ok" ? <span className="pill green">ok</span> : <span className="pill red">{a.reason ?? "fejl"}</span>}</td>
                  <td className="small">{a.contact ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section style={section}>
        <div className="spread"><h2 style={h2}>Ændringer</h2><Link to="/logs/audit" style={{ fontSize: 13, fontWeight: 600 }}>Audit-log</Link></div>
        <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          {log.data.changes.map((c, i) => (
            <li key={i} style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 13 }}>
              <span className="mono">{c.action}</span><span className="muted tiny">{fmtDate(c.ts)}{c.actor_user_id ? ` · bruger #${c.actor_user_id}` : " · systemet"}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

export default function LoginProviderSetup() {
  const { key = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const data = useLoad(() => api.get<ProviderDetail>(`/api/admin/login-providers/${key}`), [key]);
  const [tab, setTab] = useState<Tab>("setup");
  const [form, setForm] = useState<Config | null>(null);
  const [name, setName] = useState("");
  const [secret, setSecret] = useState("");
  const [idp, setIdp] = useState({ entity_id: "", sso_url: "", cert: "" });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!data.data) return;
    setForm(data.data.config);
    setName(data.data.name);
    setIdp({ entity_id: data.data.idp?.entity_id ?? "", sso_url: data.data.idp?.sso_url ?? "", cert: "" });
  }, [data.data]);
  // Back from a test login: say how it went (the result itself is on the provider).
  useEffect(() => {
    const t = params.get("test");
    if (!t) return;
    toast(t === "ok" ? "Testlogin lykkedes" : "Testlogin mislykkedes");
    params.delete("test");
    setParams(params, { replace: true });
  }, [params, setParams, toast]);

  if (data.error) return <Alert>{data.error}</Alert>;
  if (!data.data || !form) return <Loading />;
  const p = data.data;
  const dirty = JSON.stringify(form) !== JSON.stringify(p.config) || name !== p.name || !!secret.trim()
    || (form.metadata_source === "manual" && (idp.entity_id !== (p.idp?.entity_id ?? "") || idp.sso_url !== (p.idp?.sso_url ?? "") || !!idp.cert.trim()));

  const save = async (): Promise<boolean> => {
    setError(null);
    setSaving(true);
    try {
      const body: Record<string, unknown> = { name, config: form, client_secret: secret.trim() || null };
      if (p.kind === "saml" && form.metadata_source === "manual") Object.assign(body, { idp_entity_id: idp.entity_id, idp_sso_url: idp.sso_url, idp_cert: idp.cert });
      const fresh = await api.put<ProviderDetail>(`/api/admin/login-providers/${key}`, body);
      data.setData(fresh);
      setSecret("");
      toast("Ændringerne er gemt");
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setSaving(false);
    }
  };
  const test = async () => {
    if (dirty && !(await save())) return;
    setError(null);
    try {
      const r = await api.post<{ url: string }>(`/api/admin/login-providers/${key}/test`);
      window.location.assign(r.url);
    } catch (e) {
      setError(errorText(e));
    }
  };
  const toggle = async () => {
    setError(null);
    try {
      if (!p.enabled && dirty && !(await save())) return;
      await api.post(`/api/admin/login-providers/${key}/enabled`, { enabled: !p.enabled });
      toast(p.enabled ? `${p.name} er slået fra` : `${p.name} er slået til`);
      await data.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const remove = async () => {
    if (!window.confirm(`Slet ${p.name}? De ${p.linked} koblinger forsvinder.`)) return;
    try {
      await api.del(`/api/admin/login-providers/${key}`);
      window.location.assign("/admin/login-providers");
    } catch (e) {
      setError(errorText(e));
    }
  };

  const tabs: [Tab, string][] = [["setup", "Opsætning"], ["users", `Koblede brugere · ${p.linked}`], ["log", "Log"]];
  return (
    <div className="stack" style={{ "--gap": "20px" } as CSSProperties}>
      <Link to="/login-providers" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 600, color: "#475569", textDecoration: "none", alignSelf: "flex-start" }}>
        <ArrowLeft size={14} aria-hidden="true" />Login-udbydere
      </Link>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <ProviderMark kind={p.kind} size={48} />
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 240 }}>
          {p.kind === "saml" && <span className="eyebrow">SAML 2.0-forbindelse</span>}
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10 }}>
            {p.kind === "saml"
              ? <input aria-label="Forbindelsens navn" value={name} maxLength={80} onChange={(e) => setName(e.target.value)}
                       style={{ border: 0, padding: 0, font: "700 28px var(--hm-font-sans)", letterSpacing: "-0.02em", minWidth: 0, width: `${Math.max(name.length, 6) + 1}ch`, background: "transparent" }} />
              : <h1 style={{ margin: 0, fontSize: 28, lineHeight: 1.15, fontWeight: 700, letterSpacing: "-0.02em" }}>{KIND_LABEL[p.kind]}</h1>}
            <StatusPill status={p.status} />
            {p.kind === "saml" && <CertPill days={p.cert_days} />}
          </div>
          {p.protocol !== "saml" && (
            <p style={{ margin: 0, color: "#475569" }}>
              {p.protocol === "oidc" ? "OpenID Connect med authorization code + PKCE." : "OAuth 2.0 med authorization code + PKCE."} Gær beder kun om{" "}
              <code>{p.kind === "github" ? "read:user user:email" : p.kind === "amazon" ? "profile" : "openid email profile"}</code>.
            </p>
          )}
        </div>
        <div className="row" style={{ "--gap": "8px" } as CSSProperties}>
          <button type="button" className="btn btn-outline btn-sm" disabled={p.missing.length > 0 && !dirty} onClick={test}>Kør testlogin</button>
          <button type="button" className="btn btn-primary btn-sm" disabled={!dirty || saving} onClick={save}>Gem ændringer</button>
        </div>
      </div>

      {p.kind === "saml" && p.cert_days !== null && p.cert_days <= 30 && (
        <div role="alert" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", padding: "14px 16px", borderRadius: 12, border: "1px solid #fde68a", background: "#fffbeb", color: "#92400e", fontSize: 14, lineHeight: 1.5 }}>
          <AlertTriangle size={18} style={{ flex: "none" }} aria-hidden="true" />
          <span style={{ flex: 1, minWidth: 240 }}>
            <strong>IdP'ens signeringscertifikat udløber om {p.cert_days} dage.</strong>{" "}
            {form.metadata_source === "url" && form.refresh ? `Metadata hentes hver nat; når ${p.name} udgiver et nyt certifikat, skiftes det automatisk.` : "Hent eller indtast det nye certifikat, før det gamle udløber."}
          </span>
        </div>
      )}
      <Alert>{error}</Alert>

      <div role="tablist" aria-label="Udbyder" style={{ display: "flex", gap: 4, borderBottom: "1px solid #e2e8f0" }}>
        {tabs.map(([t, label]) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                  style={{ height: 40, padding: "0 14px", border: 0, background: "transparent", cursor: "pointer", font: `${tab === t ? 600 : 500} 14px var(--hm-font-sans)`,
                    color: tab === t ? "#020817" : "#475569", borderBottom: tab === t ? "2px solid #2563eb" : "2px solid transparent", marginBottom: -1 }}>
            {label}
          </button>
        ))}
      </div>

      {tab === "setup" && p.kind !== "saml" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 16, alignItems: "start" }} className="lp-setup">
          <style>{`@media (min-width: 1100px) { .lp-setup { grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr) !important; } }`}</style>
          <OidcSetup p={p} form={form} setForm={setForm} secret={secret} setSecret={setSecret} onTest={test} />
          <Aside p={p} form={form} setForm={setForm} onToggle={toggle} />
        </div>
      )}
      {tab === "setup" && p.kind === "saml" && (
        <div className="stack" style={{ "--gap": "16px" } as CSSProperties}>
          <SamlSetup p={p} form={form} setForm={setForm} idp={idp} setIdp={setIdp} onReload={data.reload} />
          <div className="grid" style={{ "--min": "320px", alignItems: "start" } as CSSProperties}>
            <section style={section}>
              <h2 style={h2}>Test</h2>
              <p style={hint}>Testlogin åbner jeres IdP og logger ikke nogen ind.</p>
              <TestResult test={p.last_test} />
            </section>
            <Aside p={p} form={form} setForm={setForm} onToggle={toggle} />
          </div>
        </div>
      )}
      {tab === "users" && <LinkedUsers p={p} />}
      {tab === "log" && <ProviderLog p={p} />}

      {tab === "setup" && !p.enabled && (
        <div className="row"><button type="button" className="btn btn-ghost btn-sm" style={{ color: "#b91c1c" }} onClick={remove}>Slet {p.kind === "saml" ? "forbindelsen" : "udbyderen"}</button></div>
      )}
    </div>
  );
}
