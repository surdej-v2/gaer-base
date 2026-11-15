// /developer/api — API-studie (API-1 … API-12): APIs over the same operations as MCP, keys, test console, documentation
// (OpenAPI 3.1 and generated clients) and monitoring. Plus /developer/eksport: export and import between instances.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage, devState, useCrumb } from "./DeveloperShell";
import { ArtifactTabs, Cls, Empty, StatusDot, Tabs, type ArtifactDetail } from "./devui";
import { copy } from "./parts";

type ApiItem = { id: number; slug: string; name: string; version: string | null; status: string; statusLabel: string; operations: number; url: string | null };

function useApis() {
  return useLoad(() => api.get<{ items: ApiItem[] }>("/api/admin/dev/apis"), []);
}

function ApiPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data } = useApis();
  return (
    <select className="dev-select" value={value} onChange={(e) => onChange(e.target.value)} aria-label="API">
      <option value="">Vælg API</option>
      {(data?.items ?? []).map((a) => <option key={a.id} value={a.id}>{a.slug} · {a.name}</option>)}
    </select>
  );
}

const EXAMPLE = { name: "Lejedata", cors: [], limits: { per_key: 60 }, sunset_months: 6,
  operations: [{ method: "GET", path: "/lejemaal", operation: 0, level: "white", summary: "Søg lejemål" }] };

export function ApiList() {
  const { data, error, reload } = useApis();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [slug, setSlug] = useState("");
  const [def, setDef] = useState(JSON.stringify(EXAMPLE, null, 2));
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    try {
      const r = await api.post<{ id: number }>("/api/admin/dev/apis", { slug, name: slug, definition: JSON.parse(def) });
      reload();
      navigate(`/developer/api/${r.id}`);
    } catch (e) {
      setErr(e instanceof SyntaxError ? "Definitionen er ikke gyldig JSON" : errorText(e));
    }
  };
  return (
    <DevPage title="API'er" intro="REST-API'er på /gaer/v<major>/<slug> over de samme operationer som MCP-tools. OpenAPI 3.1 genereres automatisk."
             actions={<button className="btn btn-primary btn-sm" onClick={() => setOpen(true)}>+ Nyt API</button>}>
      {error && <Alert>{error}</Alert>}
      {open && (
        <div className="dev-card">
          <input className="dev-input" placeholder="slug, fx lejedata" value={slug} onChange={(e) => setSlug(e.target.value)} aria-label="Slug" />
          <textarea className="dev-textarea" value={def} onChange={(e) => setDef(e.target.value)} spellCheck={false} aria-label="Definition" />
          <span className="tiny muted">operation er id'et på en operation eller multisource-kilde (se Datastudie → Operationer). Stier som /lejemaal/:id.</span>
          {err && <Alert>{err}</Alert>}
          <div className="row" style={{ gap: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>Fortryd</button>
            <button className="btn btn-primary btn-sm" onClick={create} disabled={slug.length < 2}>Opret</button></div>
        </div>
      )}
      {data && data.items.length === 0 && !open && <Empty>Ingen API'er endnu.</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>API</th><th>Basis-URL</th><th>Operationer</th><th>Version</th><th>Status</th></tr></thead>
          <tbody>{data.items.map((a) => (
            <tr key={a.id} className="clickable" onClick={() => navigate(`/developer/api/${a.id}`)}>
              <td><strong>{a.slug}</strong> <span className="tiny muted">{a.name}</span></td><td className="mono small">{a.url ?? "—"}</td>
              <td className="num">{a.operations}</td><td className="mono">{a.version ?? "—"}</td><td><StatusDot status={a.status} label={a.statusLabel} /></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </DevPage>
  );
}

type Op = { method: string; path: string; operation: number; operation_slug: string; kind: string; level: string; summary: string };
type Detail = { artifact: ArtifactDetail; operations: Op[]; definition: Record<string, unknown>;
  majors: { major: number; version: string | null; publishedAt: string; sunsetAt: string | null; url: string }[];
  keys: { id: number; key_id: string; name: string; statusLabel: string; expires_at: string; scopes: string[] }[]; openapi: Record<string, unknown> | null };

export function ApiPage() {
  const { id } = useParams();
  const { data, error, setData, reload } = useLoad(() => api.get<Detail>(`/api/admin/dev/apis/${id}`), [id]);
  const [tab, setTab] = useState("ops");
  const [copied, setCopied] = useState(false);
  const [code, setCode] = useState<string | null>(null);
  const [def, setDef] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useCrumb(data?.artifact.slug);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;
  const a = data.artifact;
  const base = data.majors[0]?.url ?? "";
  const gen = async (lang: string) => setCode((await api.get<{ code: string }>(`/api/admin/dev/apis/${id}/client?lang=${lang}`)).code);
  const save = async (submit: boolean) => {
    try {
      const r = await api.put<{ version: { version: string; statusLabel: string; changeType: string } }>(`/api/admin/dev/apis/${id}`, { definition: JSON.parse(def ?? "{}"), submit });
      setMsg(`Version ${r.version.version} (${r.version.changeType}): ${r.version.statusLabel.toLowerCase()}.`);
      setDef(null);
      reload();
    } catch (e) {
      setMsg(e instanceof SyntaxError ? "Ikke gyldig JSON" : errorText(e));
    }
  };
  const extend = async (major: number) => {
    try {
      await api.post(`/api/admin/dev/apis/${id}/majors/${major}/extend`, { months: 3 });
      reload();
    } catch (e) {
      setMsg(errorText(e));
    }
  };
  return (
    <DevPage title={<>{a.slug} <span className="pill mono">v{data.majors[0]?.major ?? "—"}</span></>} intro={a.name}
             actions={<StatusDot status={a.status} label={a.statusLabel} />}>
      <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
        <span className="small muted">Basis-URL</span>
        <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
          <input className="dev-input mono" style={{ flex: 1 }} readOnly value={base} aria-label="Basis-URL" />
          <button className="btn btn-primary btn-sm" onClick={async () => setCopied(await copy(base))}>{copied ? "Kopieret" : "Kopiér"}</button>
        </div>
      </div>
      <Tabs tabs={[["ops", "Operationer"], ["openapi", "OpenAPI 3.1"], ["majors", "Versioner i drift"], ["edit", "Definition"], ["owners", "Ejere og historik"]]} value={tab} onChange={setTab} />
      {tab === "ops" && (
        <div className="stack">
          <div className="dev-wrap"><table className="dev-table">
            <thead><tr><th>Metode</th><th>Sti</th><th>Bygger på</th><th>Niveau</th></tr></thead>
            <tbody>{data.operations.map((o) => (
              <tr key={`${o.method} ${o.path}`}><td><span className="pill blue mono">{o.method}</span></td><td className="mono">{o.path}</td>
                <td><Link to={o.kind === "multisource" ? `/developer/datastudie/multisource/${o.operation}` : `/developer/datastudie/operationer/${o.operation}`}>{o.operation_slug}</Link></td>
                <td><Cls c={o.level} /></td></tr>
            ))}</tbody>
          </table></div>
          <div className="row" style={{ gap: 8 }}><Link to={`/developer/api/konsol?api=${a.id}`} className="btn btn-primary btn-sm">Åbn testkonsol</Link>
            <Link to={`/developer/api/noegler?api=${a.id}`} className="btn btn-outline btn-sm">Nøgler</Link></div>
        </div>
      )}
      {tab === "openapi" && (
        <div className="stack">
          <div className="row" style={{ gap: 8 }}>
            <a className="btn btn-outline btn-sm" href={`${base}/openapi.json`} target="_blank" rel="noreferrer">openapi.json</a>
            <button className="btn btn-outline btn-sm" onClick={() => gen("typescript")}>Generér klientkode (TypeScript)</button>
            <button className="btn btn-outline btn-sm" onClick={() => gen("python")}>Generér klientkode (Python)</button>
          </div>
          {code && <pre className="dev-code" style={{ maxHeight: 360 }}>{code}</pre>}
          <pre className="dev-code" style={{ maxHeight: 420 }}>{JSON.stringify(data.openapi, null, 2)}</pre>
        </div>
      )}
      {tab === "majors" && (
        <div className="stack">
          {data.majors.map((m) => (
            <div key={m.major} className="dev-card small">
              <div className="spread"><strong className="mono">v{m.major} ({m.version})</strong>
                {m.sunsetAt ? <StatusDot status="pending" label={`Udfases ${fmtDate(m.sunsetAt)}`} /> : <StatusDot status="ok" label="Aktiv" />}</div>
              <span className="mono tiny">{m.url}</span>
              {m.sunsetAt && <button className="btn btn-outline btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => extend(m.major)}>Forlæng 3 måneder (Accountable)</button>}
            </div>
          ))}
          <span className="tiny muted">En gammel major kører mindst 6 måneder efter, at den nye er udgivet. Nøgleejere får besked ved udgivelsen og 90, 30 og 7 dage før lukning.</span>
        </div>
      )}
      {tab === "edit" && (
        <div className="stack">
          {def === null ? <><pre className="dev-code">{JSON.stringify({ ...data.definition, operations: data.operations.map(({ operation_slug: _s, kind: _k, ...o }) => o) }, null, 2)}</pre>
            <button className="btn btn-outline btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setDef(JSON.stringify({ ...data.definition, operations: data.operations.map(({ operation_slug: _s, kind: _k, ...o }) => o) }, null, 2))}>Ret</button></>
            : <><textarea className="dev-textarea" value={def} onChange={(e) => setDef(e.target.value)} spellCheck={false} aria-label="Definition" />
              <div className="row" style={{ gap: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => setDef(null)}>Fortryd</button>
                <button className="btn btn-outline btn-sm" onClick={() => save(false)}>Gem kladde</button><button className="btn btn-primary btn-sm" onClick={() => save(true)}>Send til godkendelse</button></div></>}
          {msg && <Alert kind="info">{msg}</Alert>}
        </div>
      )}
      {tab === "owners" && <ArtifactTabs artifact={a} onChange={(x) => setData({ ...data, artifact: x })} />}
    </DevPage>
  );
}

type Key = { id: number; key_id: string; name: string; kind: string; api: string | null; scopes: string[]; ip_allow: string[]; expires_at: string;
  status: string; statusLabel: string; last_used_at: string | null };

export function KeysPage() {
  const [params] = useSearchParams();
  const { data, reload } = useLoad(() => api.get<{ items: Key[] }>(`/api/admin/dev/api-keys${params.get("api") ? `?api=${params.get("api")}` : ""}`), [params.get("api")]);
  const devAdmin = devState.me?.devAdmin;
  const [form, setForm] = useState({ name: "", kind: "api_key", api_id: params.get("api") ?? "", scopes: "GET *", expires_days: 365, ip: "" });
  const [shown, setShown] = useState<{ keyId: string; secret: string; clientId: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    setErr(null);
    try {
      setShown(await api.post("/api/admin/dev/api-keys", { name: form.name, kind: form.kind, api_id: form.api_id ? Number(form.api_id) : null,
        scopes: form.scopes.split(",").map((s) => s.trim()).filter(Boolean), expires_days: form.expires_days,
        ip_allow: form.ip.split(",").map((s) => s.trim()).filter(Boolean) }));
      reload();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  const act = async (k: Key, what: "rotate" | "revoke") => {
    if (what === "revoke" && !confirm(`Tilbagekald ${k.name}? Systemer, der bruger den, mister adgangen med det samme.`)) return;
    try {
      const r = await api.post<{ keyId: string; secret: string }>(`/api/admin/dev/api-keys/${k.id}/${what}`, what === "rotate" ? { overlap_days: 7 } : {});
      if (what === "rotate") setShown({ keyId: r.keyId, secret: r.secret, clientId: null });
      reload();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <DevPage title="API-nøgler" intro="Nøgler har scopes pr. operation, udløbsdato, ejere efter RACI og eventuel IP-begrænsning. De gemmes kun som hash og vises én gang.">
      {data && data.items.length === 0 && <Empty>Ingen nøgler endnu.</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Navn</th><th>Type</th><th>Scopes</th><th>Udløber</th><th>Status</th><th>Sidst brugt</th>{devAdmin && <th />}</tr></thead>
          <tbody>{data.items.map((k) => (
            <tr key={k.id}><td><strong>{k.name}</strong> <span className="tiny muted mono">{k.key_id}</span></td><td>{k.kind === "client_credentials" ? "Client credentials" : "API-nøgle"}</td>
              <td className="mono small">{k.scopes.join(", ")}</td><td className="small">{fmtDate(k.expires_at)}</td>
              <td><StatusDot status={k.status === "active" ? "ok" : k.status === "rotating" ? "pending" : "failed"} label={k.statusLabel} /></td>
              <td className="small">{k.last_used_at ? fmtDate(k.last_used_at) : "—"}</td>
              {devAdmin && <td className="row" style={{ gap: 4 }}>{k.status !== "revoked" && <><button className="btn btn-ghost btn-sm" onClick={() => act(k, "rotate")}>Rotér</button>
                <button className="btn btn-ghost btn-sm" onClick={() => act(k, "revoke")}>Tilbagekald</button></>}</td>}</tr>
          ))}</tbody>
        </table></div>
      )}
      {devAdmin ? (
        <div className="dev-card">
          <strong>Ny API-nøgle (kun admin)</strong>
          <div className="row" style={{ gap: 8 }}>
            <input className="dev-input" placeholder="Navn, fx BI-integration" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} aria-label="Navn" />
            <select className="dev-select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} aria-label="Type">
              <option value="api_key">API-nøgle</option><option value="client_credentials">Client credentials</option></select>
            <ApiPicker value={form.api_id} onChange={(v) => setForm({ ...form, api_id: v })} />
          </div>
          <div className="row" style={{ gap: 8 }}>
            <input className="dev-input mono" style={{ flex: 1 }} placeholder="Scopes, fx GET /lejemaal*" value={form.scopes} onChange={(e) => setForm({ ...form, scopes: e.target.value })} aria-label="Scopes" />
            <input className="dev-input" type="number" min={1} max={1095} value={form.expires_days} onChange={(e) => setForm({ ...form, expires_days: Number(e.target.value) })} aria-label="Udløb i dage" />
            <input className="dev-input" placeholder="IP-begrænsning (valgfri), fx 10.0.0.0/24" value={form.ip} onChange={(e) => setForm({ ...form, ip: e.target.value })} aria-label="IP-begrænsning" />
          </div>
          <span className="tiny muted">Gule og røde operationer kræver, at nøglen er godkendt til netop den operation (fx “POST /lejemaal/soeg”), ikke et wildcard.</span>
          <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={create} disabled={!form.name}>Opret nøgle</button>
          {err && <Alert>{err}</Alert>}
        </div>
      ) : <span className="small muted">Kun en Udvikler-admin kan oprette, rotere og tilbagekalde nøgler.</span>}
      {shown && (
        <Alert kind="warn">Gem nøglen nu. Den vises kun denne ene gang:<br />
          <span className="mono">{shown.clientId ? `client_id ${shown.clientId} · client_secret ${shown.secret}` : shown.secret}</span></Alert>
      )}
    </DevPage>
  );
}

type ConsoleOut = { status: number; ms: number; body: unknown; curl: string; headers: Record<string, string>; as: string | null };

export function ConsolePage() {
  const [params, setParams] = useSearchParams();
  const apiId = params.get("api") ?? "";
  const [detail, setDetail] = useState<Detail | null>(null);
  const [method, setMethod] = useState("GET");
  const [path, setPath] = useState("/");
  const [query, setQuery] = useState("{}");
  const [asUser, setAsUser] = useState<number | "">("");
  const [accounts, setAccounts] = useState<{ id: number; display_name: string; audience: string }[]>([]);
  const [out, setOut] = useState<ConsoleOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (apiId) api.get<Detail>(`/api/admin/dev/apis/${apiId}`).then((d) => { setDetail(d); setPath(d.operations[0]?.path ?? "/"); });
    api.get<{ users: { id: number; display_name: string; audience?: string }[] }>("/api/impersonation/users").then((r) => setAccounts(r.users.map((u) => ({ ...u, audience: u.audience ?? "" })))).catch(() => {});
  }, [apiId]);
  const send = async () => {
    setErr(null);
    try {
      const q = JSON.parse(query);
      setOut(await api.post<ConsoleOut>(`/api/admin/dev/apis/${apiId}/console`, { method, path, query: method === "GET" ? q : {}, body: method === "POST" ? q : {}, as_user_id: asUser || null }));
    } catch (e) {
      setErr(e instanceof SyntaxError ? "Parametrene er ikke gyldig JSON" : errorText(e));
    }
  };
  return (
    <DevPage title="Testkonsol" intro="Kald operationer som dig selv eller en testkonto, se forespørgsel og svar, og kopiér kaldet som curl."
             actions={<ApiPicker value={apiId} onChange={(v) => setParams(v ? { api: v } : {})} />}>
      {!apiId && <Empty>Vælg et API.</Empty>}
      {detail && (
        <>
          <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
            <select className="dev-select" value={method} onChange={(e) => setMethod(e.target.value)} aria-label="Metode"><option>GET</option><option>POST</option></select>
            <input className="dev-input mono" style={{ flex: 1 }} value={path} onChange={(e) => setPath(e.target.value)} aria-label="Sti" list="api-paths" />
            <datalist id="api-paths">{detail.operations.map((o) => <option key={`${o.method}${o.path}`} value={o.path} />)}</datalist>
            <input className="dev-input mono" style={{ width: 220 }} value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Parametre (JSON)" />
            <button className="btn btn-primary btn-sm" onClick={send}>Send</button>
          </div>
          <select className="dev-select" value={asUser} onChange={(e) => setAsUser(e.target.value ? Number(e.target.value) : "")} aria-label="Godkendelse" style={{ alignSelf: "flex-start" }}>
            <option value="">Godkendelse: mig selv</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>Godkendelse: testkonto {a.display_name}</option>)}
          </select>
          {out && (
            <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", gap: 12 }}>
              <div className="dev-card"><strong>Svar · {out.status} · {out.ms} ms</strong>
                <pre className="dev-code" style={{ maxHeight: 320 }}>{JSON.stringify(out.body, null, 2)}</pre>
                <span className="tiny muted">Headere: {Object.keys(out.headers).join(", ")}</span></div>
              <div className="dev-card"><strong>curl</strong><pre className="dev-code">{out.curl}</pre>
                <button className="btn btn-outline btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => copy(out.curl)}>Kopiér</button></div>
            </div>
          )}
          {err && <Alert>{err}</Alert>}
        </>
      )}
    </DevPage>
  );
}

type Spec = { info: { title: string; version: string }; servers: { url: string }[];
  paths: Record<string, Record<string, { summary: string; parameters: { name: string; in: string; required: boolean; description?: string }[]; "x-gaer-level": string;
    responses: { "200": { content: { "application/json": { schema: { properties: { data: { items: { properties: Record<string, { description: string; "x-gaer-classification": string }> } } } } } } } } }>> };

export function DocsPage() {
  const [params, setParams] = useSearchParams();
  const apiId = params.get("api") ?? "";
  const [spec, setSpec] = useState<Spec | null>(null);
  useEffect(() => {
    if (apiId) api.get<Detail>(`/api/admin/dev/apis/${apiId}`).then((d) => setSpec(d.openapi as unknown as Spec)); else setSpec(null);
  }, [apiId]);
  return (
    <DevPage title="Dokumentation" intro="Genereret fra OpenAPI 3.1 med feltbeskrivelser, klassifikation og maskerede felter."
             actions={<ApiPicker value={apiId} onChange={(v) => setParams(v ? { api: v } : {})} />}>
      {!apiId && <Empty>Vælg et API.</Empty>}
      {spec && (
        <div className="stack">
          <span className="mono small">{spec.servers[0].url} · v{spec.info.version}</span>
          {Object.entries(spec.paths).map(([path, ops]) => Object.entries(ops).map(([m, op]) => (
            <div key={`${m}${path}`} className="dev-card">
              <div className="row" style={{ gap: 8 }}><span className="pill blue mono">{m.toUpperCase()}</span><strong className="mono">{path}</strong><Cls c={op["x-gaer-level"]} /></div>
              <span className="small">{op.summary}</span>
              {op.parameters.length > 0 && <span className="small">Parametre: {op.parameters.map((p) => `${p.name}${p.required ? " (påkrævet)" : ""}`).join(", ")}</span>}
              <div className="dev-wrap"><table className="dev-table"><thead><tr><th>Felt</th><th>Beskrivelse</th></tr></thead><tbody>
                {Object.entries(op.responses["200"].content["application/json"].schema.properties.data.items.properties).map(([f, s]) => (
                  <tr key={f}><td><Cls c={s["x-gaer-classification"]} suffix={<span className="mono"> {f}</span>} /></td><td className="small">{s.description}</td></tr>))}
              </tbody></table></div>
            </div>
          )))}
        </div>
      )}
    </DevPage>
  );
}

type ApiMon = { callsToday: number; errorRate: number; p95: number | null; cacheHitRate: number | null; perKey: { key_id: string; name: string | null; calls: number; errors: number; last_at: string }[] };

export function ApiMonitorPage() {
  const [params, setParams] = useSearchParams();
  const apiId = params.get("api") ?? "";
  const [m, setM] = useState<ApiMon | null>(null);
  useEffect(() => { if (apiId) api.get<ApiMon>(`/api/admin/dev/apis/${apiId}/monitor`).then(setM); else setM(null); }, [apiId]);
  return (
    <DevPage title="Overvågning" intro="Log, dashboard og alarmer som i MCP-studiet, men pr. nøgle. Rate limits pr. nøgle og operation svarer 429 med Retry-After."
             actions={<ApiPicker value={apiId} onChange={(v) => setParams(v ? { api: v } : {})} />}>
      {!apiId && <Empty>Vælg et API.</Empty>}
      {m && (
        <>
          <div className="dev-cards">
            <div className="dev-card"><span className="small muted">Kald i dag</span><strong style={{ fontSize: 22 }}>{m.callsToday}</strong></div>
            <div className="dev-card"><span className="small muted">Fejlrate</span><strong style={{ fontSize: 22 }}>{m.errorRate} %</strong></div>
            <div className="dev-card"><span className="small muted">Svartid p95</span><strong style={{ fontSize: 22 }}>{m.p95 != null ? `${m.p95} ms` : "—"}</strong></div>
            <div className="dev-card"><span className="small muted">Cache hit rate</span><strong style={{ fontSize: 22 }}>{m.cacheHitRate != null ? `${m.cacheHitRate} %` : "—"}</strong></div>
          </div>
          <div className="dev-wrap"><table className="dev-table">
            <thead><tr><th>Nøgle</th><th>Kald i dag</th><th>Fejl</th><th>Sidst</th></tr></thead>
            <tbody>{m.perKey.map((k) => <tr key={k.key_id}><td>{k.name ?? k.key_id} <span className="tiny mono muted">{k.key_id}</span></td><td className="num">{k.calls}</td><td className="num">{k.errors}</td><td className="small">{fmtDate(k.last_at)}</td></tr>)}</tbody>
          </table></div>
        </>
      )}
    </DevPage>
  );
}

// ── Eksport og import ───────────────────────────────────────────────────────

type ArtifactRow = { id: number; type: string; typeLabel: string; slug: string; name: string; version: string | null };
type Check = { signer: string | null; ok: boolean; problems: string[]; actions: { typeLabel: string; slug: string; version: string | null; action: string; problems: string[] }[] };

export function ExportPage() {
  const { data } = useLoad(() => api.get<{ items: ArtifactRow[] }>("/api/admin/dev/artifacts"), []);
  const { data: keys } = useLoad(() => api.get<{ keys: { id: string; use: string; fingerprint: string; public_key: string }[]; url: string }>("/api/admin/dev/instance-keys"), []);
  const { data: known, reload: reloadKnown } = useLoad(() => api.get<{ items: { id: number; name: string; fingerprint: string }[] }>("/api/admin/dev/instances"), []);
  const [chosen, setChosen] = useState<number[]>([]);
  const [file, setFile] = useState<Record<string, unknown> | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [inst, setInst] = useState({ name: "", sign_key: "", fingerprint: "" });
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const devAdmin = devState.me?.devAdmin;
  const items = (data?.items ?? []).filter((a) => a.version && a.type !== "api_key");
  const exportFile = async () => {
    try {
      const doc = await api.post("/api/admin/dev/export", { artifact_ids: chosen, include_catalog: true });
      const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "gaer-eksport.json";
      a.click();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const onFile = async (f: File | undefined) => {
    if (!f) return;
    try {
      const doc = JSON.parse(await f.text());
      setFile(doc);
      setCheck(await api.post<Check>("/api/admin/dev/import/check", { file: doc }));
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof SyntaxError ? "Filen er ikke JSON" : errorText(e) });
    }
  };
  const runImport = async () => {
    try {
      const r = await api.post<{ imported: unknown[] }>("/api/admin/dev/import", { file });
      setMsg({ kind: "success", text: `${r.imported.length} artefakter importeret som nye versioner.` });
      setCheck(null);
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const addInstance = async () => {
    try {
      await api.post("/api/admin/dev/instances", inst);
      setInst({ name: "", sign_key: "", fingerprint: "" });
      reloadKnown();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  return (
    <DevPage title="Eksport og import" intro="Én signeret fil med manifest, definitioner og katalogposter. Hemmeligheder eksporteres kun som referencer; tokens og snapshot-data aldrig.">
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 }}>
        <div className="dev-card">
          <strong>Eksport</strong>
          <div style={{ maxHeight: 260, overflow: "auto" }} className="stack">
            {items.map((a) => (
              <label key={a.id} className="row small"><input type="checkbox" checked={chosen.includes(a.id)}
                onChange={() => setChosen((c) => c.includes(a.id) ? c.filter((x) => x !== a.id) : [...c, a.id])} /> {a.typeLabel}: {a.name} <span className="muted mono">v{a.version}</span></label>
            ))}
          </div>
          <label className="row small muted"><input type="checkbox" disabled /> Hemmeligheder, krypteret til modtager (ikke bygget)</label>
          <span className="tiny muted">Standard: kun referencer til hemmeligheder</span>
          <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={exportFile} disabled={!chosen.length}>Eksportér fil</button>
        </div>
        <div className="dev-card">
          <strong>Import{file ? `: ${String((file as { manifest?: unknown[] }).manifest?.length ?? 0)} artefakter` : ""}</strong>
          <input type="file" accept="application/json" onChange={(e) => onFile(e.target.files?.[0])} aria-label="Eksportfil" />
          {check && (
            <>
              <span className="small">Afsender: {check.signer ?? "ukendt"}</span>
              {check.actions.map((a, i) => (
                <StatusDot key={i} status={a.problems.length ? "failed" : a.action === "create" ? "ok" : "pending"}
                           label={`${a.action === "create" ? "Opretter" : "Opdaterer"} ${a.typeLabel.toLowerCase()} ${a.slug}${a.version ? ` v${a.version}` : ""}`} />
              ))}
              {check.problems.map((p, i) => <StatusDot key={`p${i}`} status="failed" label={p} />)}
              <span className="tiny muted">Importen oprettes som nye versioner, der går gennem godkendelsesflowet. Kræver Udvikler-admin.</span>
              <div className="row" style={{ gap: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => { setCheck(null); setFile(null); }}>Annullér</button>
                <button className="btn btn-primary btn-sm" onClick={runImport} disabled={!check.ok || !devAdmin}>Importér</button></div>
            </>
          )}
        </div>
      </div>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 }}>
        <div className="dev-card small">
          <strong>Denne instans' nøgler</strong>
          {keys?.keys.map((k) => <span key={k.id}>{k.use === "sign" ? "Signering (Ed25519)" : "Kryptering (X25519)"} · <span className="mono">{k.fingerprint}</span></span>)}
          <span className="tiny muted">Offentlige nøgler: <span className="mono">{keys?.url}</span>. De private nøgler kan ikke eksporteres.</span>
        </div>
        <div className="dev-card small">
          <strong>Kendte instanser</strong>
          {known?.items.map((k) => <span key={k.id}>{k.name} · <span className="mono">{k.fingerprint}</span></span>)}
          {devAdmin && (
            <>
              <input className="dev-input" placeholder="Navn" value={inst.name} onChange={(e) => setInst({ ...inst, name: e.target.value })} aria-label="Instansnavn" />
              <input className="dev-input mono" placeholder="Offentlig signeringsnøgle" value={inst.sign_key} onChange={(e) => setInst({ ...inst, sign_key: e.target.value })} aria-label="Signeringsnøgle" />
              <input className="dev-input mono" placeholder="Fingeraftryk (bekræftet ad en anden kanal)" value={inst.fingerprint} onChange={(e) => setInst({ ...inst, fingerprint: e.target.value })} aria-label="Fingeraftryk" />
              <button className="btn btn-outline btn-sm" style={{ alignSelf: "flex-start" }} onClick={addInstance}>Tilføj</button>
            </>
          )}
        </div>
      </div>
    </DevPage>
  );
}
