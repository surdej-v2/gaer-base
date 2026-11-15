// /developer/mcp — MCP-studie (MCP-1, MCP-9 … MCP-15): every endpoint, the test bench (direct calls as yourself or a test
// account and against the draft, AI test, saved test cases), connected clients, and monitoring with rate limits.
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage } from "./DeveloperShell";
import { Empty, StatusDot, Tabs } from "./devui";

type Ep = { key: string; path: string; name: string; audience: string; version: string | null; owners: string[]; status: string;
  statusLabel: string; origin: string; artifactId: number | null; tools: number };

function useEndpoints() {
  return useLoad(() => api.get<{ items: Ep[] }>("/api/admin/dev/mcp/endpoints"), []);
}

function EndpointPicker({ value, onChange, items }: { value: string; onChange: (k: string) => void; items: Ep[] }) {
  return (
    <select className="dev-select" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Endpoint">
      <option value="">Vælg endpoint</option>
      {items.map((e) => <option key={e.key} value={e.key}>{e.path} · {e.name}</option>)}
    </select>
  );
}

export function McpOverview() {
  const { data, error } = useEndpoints();
  const navigate = useNavigate();
  return (
    <DevPage title="MCP-endpoints" intro="Alle endpoints, uanset om de er lavet i Datastudie, i MCP Studio eller er indbyggede."
             actions={<><Link to="/developer/datastudie/ny" className="btn btn-primary btn-sm">+ Nyt i Datastudie</Link>
               <Link to="/developer/studio/mcp?new=1" className="btn btn-outline btn-sm">+ Nyt i kode</Link></>}>
      {error && <Alert>{error}</Alert>}
      {data && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Slug</th><th>Navn</th><th>Målgruppe</th><th>Version</th><th>Ejere</th><th>Lavet i</th><th>Tools</th><th>Status</th></tr></thead>
          <tbody>{data.items.map((e) => (
            <tr key={e.key} className="clickable" onClick={() => navigate(e.artifactId ? `/developer/datastudie/endpoints/${e.artifactId}` : `/developer/studio/mcp?endpoint=${e.key}`)}>
              <td className="mono">{e.path}</td><td>{e.name}</td><td>{e.audience}</td><td className="mono">{e.version ?? "—"}</td>
              <td className="small">{e.owners.join(", ") || "—"}</td><td className="small">{e.origin}</td><td className="num">{e.tools}</td>
              <td><StatusDot status={e.status} label={e.statusLabel} /></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </DevPage>
  );
}

type Tool = { name: string; description: string; classification: string; inputSchema: { properties: Record<string, { type: string; description?: string }> } };
type Case = { id: number; kind: string; name: string; question: string; tool: string | null; args: Record<string, unknown>;
  expect: Record<string, unknown>; last: { passed: boolean; ran_at: string; detail: { problems: string[] } } | null };
type Bench = { tools: Tool[]; accounts: { id: number; display_name: string; audience: string }[]; testcases: Case[]; ai: boolean;
  draft: { version: string } | null };
type CallOut = { result: Record<string, unknown>; isError: boolean; ms: number; cache?: string | null; masked?: string[] | null; draft: boolean };
type AiOut = { steps: { tool: string; args: Record<string, unknown>; isError: boolean; ms: number; cache: string | null; masked: string[] | null }[];
  answer: string; ms: number; toolsCalled: number };

export function TestBench() {
  const [params, setParams] = useSearchParams();
  const key = params.get("endpoint") ?? "";
  const { data: eps } = useEndpoints();
  const [bench, setBench] = useState<Bench | null>(null);
  const [tab, setTab] = useState("direct");
  const [tool, setTool] = useState("");
  const [args, setArgs] = useState("{}");
  const [asUser, setAsUser] = useState<number | "">("");
  const [draft, setDraft] = useState(false);
  const [out, setOut] = useState<CallOut | null>(null);
  const [question, setQuestion] = useState("");
  const [aiOut, setAiOut] = useState<AiOut | null>(null);
  const [runs, setRuns] = useState<{ name: string; passed: boolean; problems: string[] }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => key ? api.get<Bench>(`/api/admin/dev/mcp/${key}/bench`).then(setBench).catch((e) => setErr(errorText(e))) : setBench(null);
  useEffect(() => { load(); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const wrap = async (fn: () => Promise<void>) => {
    setErr(null);
    try { await fn(); } catch (e) { setErr(e instanceof SyntaxError ? "Argumenterne er ikke gyldig JSON" : errorText(e)); }
  };
  const call = () => wrap(async () => setOut(await api.post<CallOut>(`/api/admin/dev/mcp/${key}/call`, { tool, args: JSON.parse(args), as_user_id: asUser || null, draft })));
  const runAi = () => wrap(async () => setAiOut(await api.post<AiOut>(`/api/admin/dev/mcp/${key}/ai`, { question, as_user_id: asUser || null })));
  const saveDirect = () => wrap(async () => {
    const count = Number(out?.result?.count ?? 0);
    await api.post(`/api/admin/dev/mcp/${key}/testcases`, { kind: "direct", name: `${tool} ${args}`.slice(0, 120), tool, args: JSON.parse(args),
      expect: { min_rows: count ? 1 : 0 }, as_user_id: asUser || null });
    load();
  });
  const saveAi = () => wrap(async () => {
    await api.post(`/api/admin/dev/mcp/${key}/testcases`, { kind: "ai", name: question.slice(0, 120), question,
      expect: { tools: aiOut?.steps.map((s) => s.tool) ?? [] }, as_user_id: asUser || null });
    load();
  });
  const runAll = (onDraft: boolean) => wrap(async () => {
    const r = await api.post<{ results: { name: string; passed: boolean; problems: string[] }[] }>(`/api/admin/dev/mcp/${key}/testcases/run`, { draft: onDraft });
    setRuns(r.results);
    load();
  });
  const del = (id: number) => wrap(async () => { await api.del(`/api/admin/dev/mcp/${key}/testcases/${id}`); load(); });
  const selected = bench?.tools.find((t) => t.name === tool);
  const tabs: [string, string][] = [["direct", "Direkte kald"], ...(bench?.ai ? [["ai", "AI-test"] as [string, string]] : []), ["cases", "Gemte testcases"]];
  return (
    <DevPage title="Testbænk" intro="Kald tools direkte, også som en testkonto og mod en kladdeversion, og se hvilke tools en AI-klient vælger."
             actions={<EndpointPicker value={key} onChange={(k) => setParams(k ? { endpoint: k } : {})} items={eps?.items ?? []} />}>
      {!key && <Empty>Vælg et endpoint.</Empty>}
      {bench && (
        <>
          <div className="row" style={{ gap: 8 }}>
            <select className="dev-select" value={asUser} onChange={(e) => setAsUser(e.target.value ? Number(e.target.value) : "")} aria-label="Kør som">
              <option value="">Som mig selv</option>
              {bench.accounts.map((a) => <option key={a.id} value={a.id}>Testkonto: {a.display_name} ({a.audience === "internal" ? "intern" : "ekstern"})</option>)}
            </select>
            {bench.accounts.length === 0 && <a className="small" href="/admin/impersonation">Opret testkonti i /admin</a>}
          </div>
          <Tabs tabs={tabs} value={tab} onChange={setTab} />
          {tab === "direct" && (
            <div className="stack">
              <div className="row" style={{ gap: 8 }}>
                <select className="dev-select" value={tool} onChange={(e) => { setTool(e.target.value); setArgs("{}"); }} aria-label="Tool">
                  <option value="">Vælg tool</option>
                  {bench.tools.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                </select>
                <input className="dev-input mono" style={{ flex: 1, minWidth: 200 }} value={args} onChange={(e) => setArgs(e.target.value)} aria-label="Argumenter (JSON)" />
                {bench.draft && <label className="row small"><input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} /> Mod kladde {bench.draft.version}</label>}
                <button className="btn btn-primary btn-sm" onClick={call} disabled={!tool}>Kør</button>
              </div>
              {selected && <span className="tiny muted">{selected.description} · parametre: {Object.keys(selected.inputSchema.properties).join(", ") || "ingen"}</span>}
              {out && (
                <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(200px, 1fr)", gap: 12 }}>
                  <pre className="dev-code" style={{ maxHeight: 360 }}>{JSON.stringify(out.result, null, 2)}</pre>
                  <div className="dev-card small">
                    <strong>Detaljer</strong>
                    <span>Varighed: {out.ms} ms</span><span>Cache: {out.cache ?? "—"}</span>
                    <span>Maskering: {out.masked?.length ? out.masked.join(", ") : "ingen felter"}</span>
                    <span>{out.draft ? "Mod kladden" : "Mod udgivet version"}</span>
                    <StatusDot status={out.isError ? "failed" : "ok"} label={out.isError ? "Fejl" : "OK"} />
                    <button className="btn btn-outline btn-sm" onClick={saveDirect}>Gem som testcase</button>
                  </div>
                </div>
              )}
            </div>
          )}
          {tab === "ai" && (
            <div className="stack">
              <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                <input className="dev-input" style={{ flex: 1 }} placeholder="Fx: Hvad er den samlede årlige leje for ejendommene i 8000?" value={question}
                       onChange={(e) => setQuestion(e.target.value)} aria-label="Spørgsmål" />
                <button className="btn btn-primary btn-sm" onClick={runAi} disabled={question.length < 3}>Kør</button>
              </div>
              {aiOut && (
                <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(200px, 1fr)", gap: 12 }}>
                  <div className="stack">
                    {aiOut.steps.map((s, i) => <div key={i} className="dev-stage"><strong>{i + 1} AI valgte {s.tool}</strong><br /><span className="tiny mono">{JSON.stringify(s.args)}</span></div>)}
                    <div className="dev-stage on"><strong>{aiOut.steps.length + 1} Svar til brugeren</strong><br /><span className="small">{aiOut.answer}</span></div>
                  </div>
                  <div className="dev-card small"><strong>Detaljer</strong><span>Varighed: {aiOut.ms} ms</span>
                    <span>Fra cache: {aiOut.steps.filter((s) => s.cache === "hit").map((_, i) => `trin ${i + 1}`).join(", ") || "intet"}</span>
                    <span>Maskering: {[...new Set(aiOut.steps.flatMap((s) => s.masked ?? []))].join(", ") || "ingen felter"}</span>
                    <span>Tools kaldt: {aiOut.toolsCalled}</span>
                    <button className="btn btn-outline btn-sm" onClick={saveAi}>Gem som testcase</button></div>
                </div>
              )}
              <span className="tiny muted">Gemte testcases skal bestå, før en major-version udgives.</span>
            </div>
          )}
          {tab === "cases" && (
            <div className="stack">
              {bench.testcases.length === 0 ? <Empty>Ingen testcases endnu. Gem et kald eller en AI-test.</Empty> : (
                <div className="dev-wrap"><table className="dev-table">
                  <thead><tr><th>Testcase</th><th>Type</th><th>Seneste</th><th /></tr></thead>
                  <tbody>{bench.testcases.map((c) => (
                    <tr key={c.id}><td>{c.name}</td><td>{c.kind === "ai" ? "AI" : "Direkte"}</td>
                      <td>{c.last ? <StatusDot status={c.last.passed ? "ok" : "failed"} label={`${c.last.passed ? "Bestået" : "Fejlet"} ${fmtDate(c.last.ran_at)}`} /> : "—"}</td>
                      <td><button className="btn btn-ghost btn-sm" onClick={() => del(c.id)}>Slet</button></td></tr>
                  ))}</tbody>
                </table></div>
              )}
              <div className="row" style={{ gap: 8 }}>
                <button className="btn btn-outline btn-sm" onClick={() => runAll(false)}>Kør mod udgivet version</button>
                {bench.draft && <button className="btn btn-primary btn-sm" onClick={() => runAll(true)}>Kør mod kladde {bench.draft.version}</button>}
              </div>
              {runs?.map((r, i) => <Alert key={i} kind={r.passed ? "success" : "error"}>{r.name}: {r.passed ? "bestået" : r.problems.join("; ")}</Alert>)}
            </div>
          )}
        </>
      )}
      {err && <Alert>{err}</Alert>}
    </DevPage>
  );
}

type Clients = { clients: { client_id: string; client_name: string | null; user_id: number; user_name: string | null; last_used: string; calls_today: number; sessions: number }[];
  requests: { id: number; status: string; level: string; tool_name: string | null; client_name: string; user_name: string; created_at: string }[] };

export function ClientsPage() {
  const [params, setParams] = useSearchParams();
  const key = params.get("endpoint") ?? "";
  const { data: eps } = useEndpoints();
  const [data, setData] = useState<Clients | null>(null);
  const load = () => key ? api.get<Clients>(`/api/admin/dev/mcp/${key}/clients`).then(setData) : setData(null);
  useEffect(() => { load(); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const revoke = async (c: Clients["clients"][number]) => {
    if (!confirm(`Tilbagekald ${c.client_name ?? c.client_id} for ${c.user_name}? Klienten mister adgangen til alle endpoints med samme scope.`)) return;
    await api.post(`/api/admin/dev/mcp/${key}/clients/revoke`, { client_id: c.client_id, user_id: c.user_id });
    load();
  };
  return (
    <DevPage title="Forbundne klienter" intro="Klienter logger ind via OAuth mod Gær base; brugerens rettigheder gælder. Adgangsanmodninger til gule og røde tools behandles her og i /admin/access."
             actions={<EndpointPicker value={key} onChange={(k) => setParams(k ? { endpoint: k } : {})} items={eps?.items ?? []} />}>
      {!key && <Empty>Vælg et endpoint.</Empty>}
      {data && (data.clients.length === 0 ? <Empty>Ingen klienter har brugt endpointet endnu.</Empty> : (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Klient</th><th>Bruger</th><th>Sidst brugt</th><th>Kald i dag</th><th /></tr></thead>
          <tbody>{data.clients.map((c) => (
            <tr key={`${c.client_id}-${c.user_id}`}><td><strong>{c.client_name ?? c.client_id}</strong></td><td>{c.user_name}</td>
              <td className="small">{fmtDate(c.last_used)}</td><td className="num">{c.calls_today}</td>
              <td>{c.sessions > 0 ? <button className="btn btn-outline btn-sm" onClick={() => revoke(c)}>Tilbagekald</button> : <span className="tiny muted">tilbagekaldt</span>}</td></tr>
          ))}</tbody>
        </table></div>
      ))}
      {data && data.requests.length > 0 && (
        <div className="dev-card">
          <strong>Adgangsanmodninger</strong>
          {data.requests.map((r) => <span key={r.id} className="small">{r.user_name} · {r.client_name} · {r.tool_name ?? r.level} · <StatusDot status={r.status === "approved" ? "ok" : r.status.startsWith("awaiting") ? "pending" : "disabled"} label={r.status} /></span>)}
          <a href="/admin/access" className="small">Behandl i /admin/access →</a>
        </div>
      )}
    </DevPage>
  );
}

type Monitor = { callsToday: number; errorRate: number; p50: number | null; p95: number | null; cacheHitRate: number | null;
  series: { minute: string; calls: number; errors: number }[]; alerts: { kind: string; raised_at: string; detail: string }[];
  limits: { per_user?: number; per_client?: number; per_endpoint?: number; error_alarm_pct?: number } };

export function MonitorPage() {
  const [params, setParams] = useSearchParams();
  const key = params.get("endpoint") ?? "";
  const { data: eps } = useEndpoints();
  const [m, setM] = useState<Monitor | null>(null);
  const [limits, setLimits] = useState<Monitor["limits"]>({});
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (!key) return setM(null);
    api.get<Monitor>(`/api/admin/dev/mcp/${key}/monitor`).then((r) => { setM(r); setLimits(r.limits ?? {}); });
  }, [key]);
  const save = async () => {
    try {
      await api.put(`/api/admin/dev/mcp/${key}/limits`, limits);
      setMsg("Gemt");
    } catch (e) {
      setMsg(errorText(e));
    }
  };
  const max = Math.max(1, ...(m?.series ?? []).map((s) => s.calls));
  const num = (k: keyof Monitor["limits"]) => (
    <input className="dev-input" type="number" min={0} value={limits[k] ?? ""} aria-label={k} style={{ width: 120 }}
           onChange={(e) => setLimits({ ...limits, [k]: e.target.value ? Number(e.target.value) : undefined })} />
  );
  return (
    <DevPage title="Overvågning" intro="Kald, fejlrate, svartider og cache hit rate pr. endpoint. Responsible får alarm ved høj fejlrate eller usædvanlig brug."
             actions={<EndpointPicker value={key} onChange={(k) => setParams(k ? { endpoint: k } : {})} items={eps?.items ?? []} />}>
      {!key && <Empty>Vælg et endpoint.</Empty>}
      {m && (
        <>
          <div className="dev-cards">
            <div className="dev-card"><span className="small muted">Kald i dag</span><strong style={{ fontSize: 22 }}>{m.callsToday}</strong></div>
            <div className="dev-card"><span className="small muted">Fejlrate</span><strong style={{ fontSize: 22 }}>{m.errorRate.toLocaleString("da-DK")} %</strong></div>
            <div className="dev-card"><span className="small muted">Svartid p95</span><strong style={{ fontSize: 22 }}>{m.p95 != null ? `${m.p95} ms` : "—"}</strong><span className="tiny muted">p50 {m.p50 ?? "—"} ms</span></div>
            <div className="dev-card"><span className="small muted">Cache hit rate</span><strong style={{ fontSize: 22 }}>{m.cacheHitRate != null ? `${m.cacheHitRate} %` : "—"}</strong></div>
          </div>
          <div className="dev-card">
            <strong>Sidste time</strong>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 60 }} aria-label="Kald pr. minut">
              {m.series.map((s) => <div key={s.minute} title={`${fmtDate(s.minute)}: ${s.calls} kald, ${s.errors} fejl`}
                                         style={{ flex: 1, height: `${(s.calls / max) * 100}%`, background: s.errors ? "#f59e0b" : "#93c5fd", borderRadius: 2 }} />)}
            </div>
          </div>
          <div className="dev-card">
            <strong>Rate limits og alarm</strong>
            <div className="row small" style={{ gap: 12 }}>
              <label>Pr. bruger/min {num("per_user")}</label><label>Pr. klient/min {num("per_client")}</label>
              <label>Pr. endpoint/min {num("per_endpoint")}</label><label>Alarm ved fejlrate over % {num("error_alarm_pct")}</label>
            </div>
            <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={save}>Gem</button>
            {msg && <span className="small">{msg}</span>}
          </div>
          {m.alerts.length > 0 && <div className="dev-card"><strong>Alarmer</strong>{m.alerts.map((a, i) => <span key={i} className="small">{fmtDate(a.raised_at)} · {a.detail}</span>)}</div>}
        </>
      )}
    </DevPage>
  );
}
