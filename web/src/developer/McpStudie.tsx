// /developer/mcp — MCP-studie (MCP-1, MCP-9 … MCP-15): every endpoint, the test bench (direct calls as yourself or a test
// account and against the draft, AI test, saved test cases), connected clients, and monitoring with rate limits.
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage } from "./DeveloperShell";
import { Empty, StatusDot, Tabs } from "./devui";
import { useAiContext } from "./ai/context";

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
  // specs/developer/AI.md fase 3: the checks saved with an AI test case, and variants of its question.
  const [checks, setChecks] = useState<{ args: boolean; only: boolean; contains: string }>({ args: true, only: false, contains: "" });
  const [variants, setVariants] = useState<{ q: string; on: boolean }[] | null>(null);
  const [gen, setGen] = useState(0);   // bumps the proposals after a save or a run
  const [explainId, setExplainId] = useState<number | null>(null);
  useAiContext("artifact", eps?.items.find((e) => e.key === key)?.artifactId ?? null);
  const load = () => key ? api.get<Bench>(`/api/admin/dev/mcp/${key}/bench`).then((b) => { setBench(b); setGen((g) => g + 1); }).catch((e) => setErr(errorText(e))) : setBench(null);
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
  const aiExpect = () => {
    const tools = [...new Set(aiOut?.steps.map((s) => s.tool) ?? [])];
    const expect: Record<string, unknown> = { tools };
    if (checks.args && aiOut?.steps.length) expect.args = Object.fromEntries(aiOut.steps.map((s) => [s.tool, s.args]));
    if (checks.only) expect.only_tools = true;
    if (checks.contains.trim()) expect.answer_contains = checks.contains.trim();
    return expect;
  };
  const saveAi = () => wrap(async () => {
    await api.post(`/api/admin/dev/mcp/${key}/testcases`, { kind: "ai", name: question.slice(0, 120), question,
      expect: aiExpect(), as_user_id: asUser || null });
    load();
  });
  const makeVariants = () => wrap(async () => {
    const r = await api.post<{ variants: string[] }>(`/api/admin/dev/mcp/${key}/variants`, { question, n: 3 });
    setVariants(r.variants.map((q) => ({ q, on: true })));
  });
  const saveVariants = () => wrap(async () => {
    for (const v of variants?.filter((x) => x.on) ?? []) {
      await api.post(`/api/admin/dev/mcp/${key}/testcases`, { kind: "ai", name: v.q.slice(0, 120), question: v.q, expect: aiExpect(), as_user_id: asUser || null });
    }
    setVariants(null);
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
                    <span>Tools kaldt: {aiOut.toolsCalled}</span></div>
                </div>
              )}
              {aiOut && (
                <fieldset className="dev-ai-card" style={{ margin: 0 }}>
                  <legend className="dev-ai-where" style={{ padding: 0 }}>Gem som testcase med disse kontroller</legend>
                  <span className="small">AI skal vælge {[...new Set(aiOut.steps.map((s) => s.tool))].join(", ") || "intet tool"}</span>
                  <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={checks.args} onChange={(e) => setChecks({ ...checks, args: e.target.checked })} /> … med de samme argumenter</label>
                  <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={checks.only} onChange={(e) => setChecks({ ...checks, only: e.target.checked })} /> … og ingen andre tools</label>
                  <label className="row small" style={{ gap: 6 }}>Svaret skal nævne
                    <input className="dev-input" style={{ height: 30, flex: 1 }} value={checks.contains} placeholder="(valgfrit)" onChange={(e) => setChecks({ ...checks, contains: e.target.value })} /></label>
                  <div className="row" style={{ gap: 6 }}>
                    <button type="button" className="btn btn-primary btn-sm" onClick={saveAi}>Gem testcase</button>
                    <button type="button" className="btn btn-outline btn-sm" onClick={makeVariants}>Lav 3 varianter af spørgsmålet</button>
                  </div>
                  {variants && (
                    <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
                      {variants.length === 0 && <span className="small muted">AI gav ingen varianter.</span>}
                      {variants.map((v, i) => (
                        <label key={i} className="row small" style={{ gap: 6 }}>
                          <input type="checkbox" checked={v.on} onChange={(e) => setVariants(variants.map((x, k) => (k === i ? { ...x, on: e.target.checked } : x)))} /> {v.q}
                        </label>
                      ))}
                      {variants.some((v) => v.on) && <button type="button" className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={saveVariants}>
                        Gem {variants.filter((v) => v.on).length} varianter med samme kontroller</button>}
                    </div>
                  )}
                </fieldset>
              )}
              <span className="tiny muted">Gemte testcases skal bestå, før en major-version udgives.</span>
            </div>
          )}
          {tab === "cases" && (
            <div className="stack">
              <Proposals endpoint={key} gen={gen} onSaved={load} />
              {bench.testcases.length === 0 ? <Empty>Ingen testcases endnu. Gem et kald eller en AI-test.</Empty> : (
                <div className="dev-wrap"><table className="dev-table">
                  <thead><tr><th>Testcase</th><th>Type</th><th>Seneste</th><th /></tr></thead>
                  <tbody>{bench.testcases.map((c) => (
                    <tr key={c.id}><td>{c.name}</td><td>{c.kind === "ai" ? "AI" : "Direkte"}</td>
                      <td>{c.last ? <StatusDot status={c.last.passed ? "ok" : "failed"} label={`${c.last.passed ? "Bestået" : "Fejlet"} ${fmtDate(c.last.ran_at)}`} /> : "—"}</td>
                      <td><div className="row" style={{ gap: 4, flexWrap: "nowrap", justifyContent: "flex-end" }}>
                        {c.last && !c.last.passed && <button className="dev-ai-chipbtn" onClick={() => setExplainId(explainId === c.id ? null : c.id)}>Forklar</button>}
                        <button className="btn btn-ghost btn-sm" onClick={() => del(c.id)}>Slet</button></div></td></tr>
                  ))}</tbody>
                </table></div>
              )}
              {explainId && <Explanation endpoint={key} id={explainId} onClose={() => setExplainId(null)} />}
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

// ── Fase 3 of specs/developer/AI.md: proposed test cases and coverage, why a test case fails ─────────────

type Testcase = { kind: string; name: string; question?: string; tool?: string; args?: Record<string, unknown>; expect: Record<string, unknown>; as_user_id?: number };
type Proposal = { key: string; why: string; seen: number; needsArgs: boolean; testcase: Testcase };
type Suggestions = { coverage: { tool: string; testcases: number; calls30d: number; passed: boolean | null }[]; items: Proposal[]; notes: string[]; ai: boolean };

const WHY_CLASS: Record<string, string> = { "tool uden test": "blue", kanttilfælde: "amber", adgang: "red", "AI-spørgsmål": "blue" };

function expectText(e: Record<string, unknown>): string {
  const out: string[] = [];
  if (e.min_rows !== undefined) out.push(`mindst ${e.min_rows} rækker`);
  if (e.max_rows !== undefined) out.push(Number(e.max_rows) === 0 ? "tomt svar, ingen fejl" : `højst ${e.max_rows} rækker`);
  if (e.protected) out.push("ingen personfelter i klartekst");
  if (Array.isArray(e.tools) && e.tools.length) out.push(`AI vælger ${e.tools.join(", ")}`);
  if (e.answer_contains) out.push(`svaret nævner “${e.answer_contains}”`);
  return out.join(" · ") || "kaldet lykkes";
}

/** Proposed test cases from rules (and, on request, AI questions). The user picks; nothing is saved before "Tilføj". */
function Proposals({ endpoint, gen, onSaved }: { endpoint: string; gen: number; onSaved: () => void }) {
  const [data, setData] = useState<Suggestions | null>(null);
  const [on, setOn] = useState<Record<string, boolean>>({});
  const [args, setArgs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = (withAi = false) => api.get<Suggestions>(`/api/admin/dev/mcp/${endpoint}/suggestions${withAi ? "?ai=true" : ""}`)
    .then((d) => {
      setData(d);
      setOn(Object.fromEntries(d.items.map((p) => [p.key, !p.needsArgs])));
      setArgs(Object.fromEntries(d.items.map((p) => [p.key, JSON.stringify(p.testcase.args ?? {})])));
    }, (e) => setErr(errorText(e)));
  useEffect(() => { load(); }, [endpoint, gen]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return err ? <Alert>{err}</Alert> : null;
  const tested = data.coverage.filter((c) => c.testcases > 0).length;
  const chosen = data.items.filter((p) => on[p.key]);
  const add = async () => {
    setBusy(true);
    setErr(null);
    try {
      for (const p of chosen) {
        const tc = p.testcase.kind === "direct" ? { ...p.testcase, args: JSON.parse(args[p.key] || "{}") } : p.testcase;
        await api.post(`/api/admin/dev/mcp/${endpoint}/testcases`, tc);
      }
      onSaved();
    } catch (e) {
      setErr(e instanceof SyntaxError ? "Argumenterne er ikke gyldig JSON" : errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <strong className="small">Dækning: {tested} af {data.coverage.length} tools har en testcase</strong>
        {data.coverage.map((c) => (
          <span key={c.tool} className={`pill${c.testcases === 0 ? " amber" : c.passed === false ? " red" : ""}`} title={`${c.calls30d} kald de seneste 30 dage`}>
            {c.tool} · {c.testcases}
          </span>
        ))}
      </div>
      {(data.items.length > 0 || data.notes.length > 0 || data.ai) && (
        <section className="dev-ai-card" aria-labelledby="mcp-props">
          <div className="row" style={{ gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <h2 id="mcp-props" className="dev-ai-title">{data.items.length ? `${data.items.length} foreslåede testcases` : "Ingen nye forslag"}</h2>
            <span className="tiny muted">ud fra tools uden test, kanttilfælde, adgang og kaldsloggen. Kun metadata; ingen værdier fra rigtige kald</span>
          </div>
          {data.notes.map((n) => <span key={n} className="small">{n}</span>)}
          {data.items.map((p) => (
            <div key={p.key} className="row" style={{ gap: 8, alignItems: "flex-start", background: "#fff", border: "1px solid var(--hm-border)", borderRadius: 10, padding: "8px 10px" }}>
              <input type="checkbox" checked={!!on[p.key]} onChange={(e) => setOn({ ...on, [p.key]: e.target.checked })} aria-label={`Medtag ${p.testcase.name}`} style={{ marginTop: 3 }} />
              <div className="stack" style={{ "--gap": "3px", flex: 1, minWidth: 0 } as React.CSSProperties}>
                <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                  <strong className="small">{p.testcase.name}</strong>
                  <span className={`pill ${WHY_CLASS[p.why] ?? ""}`}>{p.why}</span>
                  {p.seen > 0 && <span className="tiny muted">kaldt {p.seen} gange de seneste 30 dage</span>}
                </div>
                <span className="tiny muted">Forventer: {expectText(p.testcase.expect)}{p.testcase.as_user_id ? " · som testkonto" : ""}</span>
                {p.testcase.kind === "direct" && (
                  <input className="dev-input mono" style={{ height: 28, fontSize: 12 }} value={args[p.key] ?? "{}"} aria-label={`Argumenter til ${p.testcase.name}`}
                         onChange={(e) => setArgs({ ...args, [p.key]: e.target.value })} />
                )}
                {p.needsArgs && <span className="tiny" style={{ color: "#92400e" }}>Udfyld argumenterne, før den kan tilføjes.</span>}
              </div>
            </div>
          ))}
          <div className="row" style={{ gap: 6 }}>
            {chosen.length > 0 && <button className="btn btn-primary btn-sm" onClick={add} disabled={busy}>Tilføj {chosen.length} valgte</button>}
            {data.ai && <button className="btn btn-outline btn-sm" onClick={() => load(true)} disabled={busy}>Lav spørgsmål med AI</button>}
          </div>
          {err && <span className="small" role="alert" style={{ color: "#b91c1c" }}>{err}</span>}
        </section>
      )}
    </div>
  );
}

type Explained = { passed: boolean | null; problems: string[]; changes: { tool: string; before: string | null; after: string | null }[];
  text: string; ai: boolean; endpointPath: string | null };

function Explanation({ endpoint, id, onClose }: { endpoint: string; id: number; onClose: () => void }) {
  const { data, error } = useLoad(() => api.get<Explained>(`/api/admin/dev/mcp/${endpoint}/testcases/${id}/explain`), [endpoint, id]);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <span className="small muted">Finder årsagen …</span>;
  return (
    <div className="dev-card" style={{ borderColor: "#fecaca" }}>
      <div className="row" style={{ gap: 8 }}>
        <strong className="small" style={{ flex: 1 }}>Hvorfor fejler testcasen?</strong>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Luk</button>
      </div>
      {data.ai && data.problems.map((pr) => <span key={pr} className="small">{pr}</span>)}
      <span className="small"><b className="dev-ai-where" style={{ display: "inline" }}>{data.ai ? "AI: " : ""}</b>{data.text}</span>
      {data.changes.map((c) => (
        <pre key={c.tool} className="dev-code" style={{ whiteSpace: "pre-wrap" }}>
          {`${c.tool}\n- ${c.before ?? "(fandtes ikke)"}\n+ ${c.after ?? "(fjernet i kladden)"}`}
        </pre>
      ))}
      <div className="row" style={{ gap: 6 }}>
        {data.endpointPath && <Link to={data.endpointPath} className="btn btn-primary btn-sm">Ret beskrivelsen i endpointet</Link>}
      </div>
    </div>
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
