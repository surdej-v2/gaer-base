// /developer/pipeline — Pipeline (PL-1 … PL-17): the list with status, last and next run; a new pipeline (from a
// description with AI, or a template); the editor with the visual stage list and the YAML text kept in sync, a masked
// preview after each stage and the generated SQL; the plan, the runs with time per phase, and the snapshots.
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtBytes, fmtDate, fmtNumber, useLoad } from "../ui";
import { DevPage, devState, useCrumb } from "./DeveloperShell";
import { ArtifactTabs, Cls, Empty, StatusDot, Tabs, type ArtifactDetail } from "./devui";

type Run = { id: number; artifact_id?: number; name?: string; status: string; label: string; trigger: string; queued_at: string; started_at: string | null;
  finished_at: string | null; rows: number | null; size_bytes: number | null; small_groups: number | null; error: string | null };
type ListItem = { id: number; name: string; slug: string; status: string; statusLabel: string; version: string | null; lastRun: Run | null;
  lastRunLabel: string | null; next: string | null; draft: string | null };

const runStatus = (s: string) => (s === "succeeded" ? "ok" : s === "failed" ? "failed" : s);
function dur(r: Run) {
  if (!r.started_at || !r.finished_at) return "—";
  const s = Math.round((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000);
  return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
}

export function PipelineList() {
  const { data, error, reload } = useLoad(() => api.get<{ items: ListItem[] }>("/api/admin/dev/pipelines"), []);
  const navigate = useNavigate();
  const failed = (data?.items ?? []).filter((p) => p.lastRun?.status === "failed");
  const rerun = async (id: number) => {
    await api.post(`/api/admin/dev/pipelines/${id}/run`);
    reload();
  };
  return (
    <DevPage title="Pipelines" intro="Pipelines bygger snapshots af data på forhånd, så tunge beregninger ikke køres ved hvert opslag."
             actions={<Link to="/developer/pipeline/ny" className="btn btn-primary btn-sm">+ Ny pipeline</Link>}>
      {error && <Alert>{error}</Alert>}
      {data && data.items.length === 0 && <Empty>Ingen pipelines endnu. <Link to="/developer/pipeline/ny">Byg den første</Link>.</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Pipeline</th><th>Version</th><th>Status</th><th>Sidste kørsel</th><th>Næste</th><th>Rækker</th></tr></thead>
          <tbody>{data.items.map((p) => (
            <tr key={p.id} className="clickable" onClick={() => navigate(`/developer/pipeline/${p.id}`)}>
              <td><strong className="mono">{p.slug}</strong>{p.draft && <span className="tiny muted"> · kladde</span>}</td>
              <td className="mono">{p.version ?? "—"}</td>
              <td>{p.lastRun ? <StatusDot status={runStatus(p.lastRun.status)} label={p.lastRunLabel ?? ""} /> : <span className="muted small">Ikke kørt</span>}</td>
              <td className="small">{p.lastRun ? fmtDate(p.lastRun.started_at ?? p.lastRun.queued_at) : "—"}</td>
              <td className="small">{p.next ? fmtDate(p.next) : "manuelt"}</td>
              <td className="num">{p.lastRun?.rows != null ? fmtNumber(p.lastRun.rows) : "—"}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {failed.map((p) => (
        <div key={p.id} className="dev-card" style={{ borderColor: "#fca5a5" }}>
          <div className="spread"><strong>{p.slug}: kørslen fejlede {fmtDate(p.lastRun!.finished_at)}</strong>
            <button className="btn btn-outline btn-sm" onClick={() => rerun(p.id)}>Kør igen</button></div>
          <span className="small muted">{p.lastRun!.error} Det sidste gode snapshot bevares, og Responsible har fået besked.</span>
        </div>
      ))}
    </DevPage>
  );
}

const TEMPLATE = `name: mit_snapshot
schedule: dagligt 06:00
wait_for_load: false
key: id
stages:
  - source: clips
  - project: [id, title, created_at]
`;

export function PipelineNew() {
  const navigate = useNavigate();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [yaml, setYaml] = useState(TEMPLATE);
  const [suggested, setSuggested] = useState<Record<string, unknown>[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ai = devState.me?.ai;
  const suggest = async () => {
    setErr(null);
    try {
      setSuggested((await api.post<{ stages: Record<string, unknown>[] }>("/api/admin/dev/pipelines/suggest", { description })).stages);
    } catch (e) {
      setErr(errorText(e));
    }
  };
  const accept = async () => {
    try {
      const r = await api.post<{ yaml: string }>("/api/admin/dev/pipelines/format", { definition: { name: slug || "ny", key: null, stages: suggested } });
      setYaml(r.yaml);
      setSuggested(null);
    } catch (e) {
      setErr(errorText(e));
    }
  };
  const create = async () => {
    setErr(null);
    try {
      const r = await api.post<{ id: number }>("/api/admin/dev/pipelines", { slug, name: name || slug, definition: yaml });
      navigate(`/developer/pipeline/${r.id}`);
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <DevPage title="Ny pipeline" intro="Beskriv datasættet, eller skriv pipelinen i YAML. Trinene følger MongoDBs aggregation pipeline.">
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 }}>
        {ai && (
          <div className="dev-card">
            <strong>Beskriv datasættet</strong>
            <textarea className="dev-input" style={{ height: 100, padding: 8 }} value={description} onChange={(e) => setDescription(e.target.value)}
                      placeholder="Fx: antal aktive lejemål og samlet årlig leje pr. ejendom, med adresse. Kun ejendomme med mindst 5 lejemål." />
            <span className="tiny muted">AI bruger datakataloget til forslaget</span>
            <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={suggest} disabled={description.length < 5}>Foreslå pipeline</button>
          </div>
        )}
        {suggested && (
          <div className="dev-card">
            <strong>Forslag</strong>
            {suggested.map((s, i) => <div key={i} className="dev-stage">{i + 1} {Object.keys(s)[0]}: <span className="mono tiny">{JSON.stringify(Object.values(s)[0])}</span></div>)}
            <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="btn btn-outline btn-sm" onClick={() => setSuggested(null)}>Afvis</button>
              <button className="btn btn-primary btn-sm" onClick={accept}>Tilføj til editor</button>
            </div>
          </div>
        )}
      </div>
      <div className="row" style={{ gap: 8 }}>
        <input className="dev-input" placeholder="navn (fx lejemaal_pr_ejendom)" value={slug} onChange={(e) => setSlug(e.target.value)} aria-label="Navn" />
        <input className="dev-input" placeholder="Visningsnavn" value={name} onChange={(e) => setName(e.target.value)} aria-label="Visningsnavn" />
      </div>
      <textarea className="dev-textarea" value={yaml} onChange={(e) => setYaml(e.target.value)} aria-label="Pipeline i YAML" spellCheck={false} />
      {err && <Alert>{err}</Alert>}
      <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={create} disabled={!slug}>Opret kladde</button>
    </DevPage>
  );
}

type Column = { name: string; type: string; classification: string; personal: boolean; sources: string[]; transform: string; policy?: string };
type Detail = { artifact: ArtifactDetail; yaml: string; definition: { stages: Record<string, unknown>[]; schedule: { type: string; earliest?: string; latest?: string };
  wait_for_load: boolean; max_minutes: number; paused: boolean; min_group_size: number; key: string | null };
  compiled: { columns: Column[]; warnings: string[]; stages: { index: number; kind: string; label: string }[] } | null; error: string | null;
  runs: Run[]; snapshot: { id: number; key: string; refreshed_at: string | null } | null; loadSources: string[];
  dependsOn: { id: number; name: string; slug: string }[]; dependents: { id: number; name: string }[]; next: string | null };
type Preview = { stage: number; columns: Column[]; rows: Record<string, unknown>[]; sql: string; warnings: string[]; smallGroups: number;
  minGroupSize: number; sensitive: string[] };

const STAGE_TEMPLATES: Record<string, unknown> = {
  match: { status: "aktiv" }, lookup: { from: "", local: "", foreign: "id", fields: [] }, unwind: { path: "" },
  addFields: { nyt_felt: { $multiply: ["$felt", 2] } }, project: [""], group: { by: "", antal: "count" },
  sort: { felt: -1 }, limit: 100, unionWith: { source: "" }, mask: { felt: "mask" },
};
const STAGE_DA: Record<string, string> = { source: "Kilde", match: "Filtrer ($match)", lookup: "Opslag ($lookup)", unwind: "Fold ud ($unwind)",
  addFields: "Tilføj felter", set: "Tilføj felter", project: "Projicer ($project)", group: "Grupper ($group)", sort: "Sorter",
  limit: "Begræns", unionWith: "Saml ($unionWith)", mask: "Masker" };

export function PipelineEditor() {
  const { id } = useParams();
  const { data, error, reload, setData } = useLoad(() => api.get<Detail>(`/api/admin/dev/pipelines/${id}`), [id]);
  const [tab, setTab] = useState("editor");
  const [mode, setMode] = useState<"visual" | "text">("visual");
  const [yaml, setYaml] = useState("");
  const [stages, setStages] = useState<Record<string, unknown>[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown>>({});
  const [selected, setSelected] = useState(0);
  const [stageText, setStageText] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [msg, setMsg] = useState<{ kind: "error" | "success" | "info" | "warn"; text: string } | null>(null);
  useCrumb(data?.artifact.slug);
  useEffect(() => {
    if (!data) return;
    setYaml(data.yaml);
    const { stages: st, ...rest } = data.definition;
    setStages(st);
    setMeta(rest);
  }, [data]);
  useEffect(() => setStageText(stages[selected] ? JSON.stringify(Object.values(stages[selected])[0], null, 2) : ""), [stages, selected]);
  const definition = useMemo(() => ({ ...meta, stages }), [meta, stages]);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;

  const toText = async () => {
    try {
      setYaml((await api.post<{ yaml: string }>("/api/admin/dev/pipelines/format", { definition })).yaml);
      setMode("text");
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const toVisual = async () => {
    try {
      const r = await api.post<{ definition: Detail["definition"] }>("/api/admin/dev/pipelines/format", { yaml });
      const { stages: st, ...rest } = r.definition;
      setStages(st);
      setMeta(rest);
      setMode("visual");
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const current = () => (mode === "text" ? yaml : definition);
  const applyStage = () => {
    try {
      const kind = Object.keys(stages[selected])[0];
      const value = JSON.parse(stageText);
      setStages((s) => s.map((x, i) => (i === selected ? { [kind]: value } : x)));
      setMsg(null);
    } catch {
      setMsg({ kind: "error", text: "Trinnet er ikke gyldig JSON" });
    }
  };
  const doPreview = async (stage?: number) => {
    setMsg(null);
    try {
      setPreview(await api.post<Preview>("/api/admin/dev/pipelines/preview", { definition: current(), stage: stage ?? selected }));
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const save = async () => {
    try {
      const r = await api.put<{ version: { version: string; changeType: string } }>(`/api/admin/dev/pipelines/${id}`, { definition: current() });
      setMsg({ kind: "success", text: `Kladde ${r.version.version} gemt (${r.version.changeType}).` });
      reload();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const submit = async () => {
    try {
      await api.put(`/api/admin/dev/pipelines/${id}`, { definition: current() });
      const v = await api.post<{ version: string; statusLabel: string }>(`/api/admin/dev/artifacts/${id}/submit`, {});
      setMsg({ kind: "success", text: `Version ${v.version}: ${v.statusLabel.toLowerCase()}.` });
      reload();
      devState.refresh();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const runNow = async () => {
    try {
      await api.post(`/api/admin/dev/pipelines/${id}/run`);
      setMsg({ kind: "info", text: "Kørslen er sat i gang. Se Kørsler." });
      reload();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const addStage = (kind: string) => {
    setStages((s) => [...s, { [kind]: STAGE_TEMPLATES[kind] }]);
    setSelected(stages.length);
  };
  const move = (i: number, d: number) => setStages((s) => {
    const n = [...s];
    const j = i + d;
    if (j < 1 || j >= n.length || i === 0) return s;
    [n[i], n[j]] = [n[j], n[i]];
    return n;
  });

  const s = data.definition.schedule;
  const editor = (
    <div className="stack">
      <div className="spread">
        <span className="tiny muted">Tekst og editor holdes altid synkrone</span>
        <div className="segmented" role="tablist" aria-label="Visning" style={{ width: 180 }}>
          <button type="button" role="tab" aria-selected={mode === "visual"} onClick={() => mode === "text" ? toVisual() : undefined}>Visuel</button>
          <button type="button" role="tab" aria-selected={mode === "text"} onClick={() => mode === "visual" ? toText() : undefined}>Tekst</button>
        </div>
      </div>
      {mode === "text" ? (
        <textarea className="dev-textarea" style={{ minHeight: 360 }} value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} aria-label="YAML" />
      ) : (
        <div className="grid" style={{ gridTemplateColumns: "minmax(220px, 300px) 1fr", gap: 12 }}>
          <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
            {stages.map((st, i) => {
              const kind = Object.keys(st)[0];
              return (
                <div key={i} className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                  <button type="button" className={`dev-stage${i === selected ? " on" : ""}`} style={{ flex: 1 }} onClick={() => { setSelected(i); doPreview(i); }}>
                    <strong>{STAGE_DA[kind] ?? kind}</strong><br /><span className="tiny muted mono">{JSON.stringify(Object.values(st)[0]).slice(0, 48)}</span>
                  </button>
                  {i > 0 && <span className="stack" style={{ "--gap": "2px" } as React.CSSProperties}>
                    <button className="btn btn-ghost btn-sm" aria-label="Flyt op" onClick={() => move(i, -1)}>↑</button>
                    <button className="btn btn-ghost btn-sm" aria-label="Flyt ned" onClick={() => move(i, 1)}>↓</button>
                    <button className="btn btn-ghost btn-sm" aria-label="Fjern trin" onClick={() => { setStages((x) => x.filter((_, k) => k !== i)); setSelected(0); }}>×</button>
                  </span>}
                </div>
              );
            })}
            <select className="dev-select" value="" onChange={(e) => e.target.value && addStage(e.target.value)} aria-label="Tilføj trin">
              <option value="">+ Tilføj trin</option>
              {Object.keys(STAGE_TEMPLATES).map((k) => <option key={k} value={k}>{STAGE_DA[k]}</option>)}
            </select>
          </div>
          <div className="stack">
            <strong>Trin {selected + 1}: {STAGE_DA[Object.keys(stages[selected] ?? { x: 1 })[0]] ?? ""}</strong>
            <textarea className="dev-textarea" style={{ minHeight: 120 }} value={stageText} onChange={(e) => setStageText(e.target.value)} onBlur={applyStage} spellCheck={false} aria-label="Trinnets indstillinger (JSON)" />
            {preview && preview.stage === selected && <PreviewView p={preview} />}
          </div>
        </div>
      )}
      <span className="tiny muted">
        {s.type === "daily" ? `Kører dagligt${data.definition.wait_for_load ? ", når indlæsningen er færdig" : ""}, tidligst kl. ${s.earliest}` : s.type === "hourly" ? "Kører hver time" : "Køres manuelt"}
      </span>
      <div className="row" style={{ gap: 8 }}>
        <button className="btn btn-outline btn-sm" onClick={() => doPreview()}>Forhåndsvis</button>
        <button className="btn btn-outline btn-sm" onClick={save}>Gem kladde</button>
        <button className="btn btn-outline btn-sm" onClick={runNow} disabled={!data.artifact.current}>Kør nu</button>
        <button className="btn btn-primary btn-sm" onClick={submit}>Send til godkendelse</button>
      </div>
      {msg && <Alert kind={msg.kind === "warn" ? "warn" : msg.kind}>{msg.text}</Alert>}
      {data.error && <Alert>{data.error}</Alert>}
      {data.compiled?.warnings.map((w, i) => <Alert key={i} kind="warn">{w}</Alert>)}
    </div>
  );
  const lineage = data.compiled && (
    <div className="dev-wrap"><table className="dev-table">
      <thead><tr><th>Kildefelter</th><th>Trin</th><th>Snapshot-felt</th></tr></thead>
      <tbody>{data.compiled.columns.map((c) => (
        <tr key={c.name}><td className="mono small">{c.sources.join(", ") || "—"}</td><td className="mono small">{c.transform || "kilde"}</td>
          <td><Cls c={c.classification} suffix={<span className="mono"> {c.name}{c.personal ? " · persondata" : ""}</span>} /></td></tr>
      ))}</tbody>
    </table></div>
  );
  const plan = (
    <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
      <div className="dev-card small">
        <div><span className="muted">Frekvens</span><br />{s.type === "daily" ? `Dagligt, tidligst kl. ${s.earliest}` : s.type === "hourly" ? "Hver time" : "Manuelt"}</div>
        <div><span className="muted">Vent på indlæsning</span><br />{data.definition.wait_for_load ? `Ja, ${data.loadSources.join(", ") || "ingen kilder"}` : "Nej"}</div>
        {s.type === "daily" && <div><span className="muted">Seneste start</span><br />{s.latest}, ellers status Udskudt</div>}
        <div><span className="muted">Maks. køretid</span><br />{data.definition.max_minutes} min</div>
        <div><span className="muted">Sat på pause</span><br />{data.definition.paused ? "Ja" : "Nej"}</div>
        <div><span className="muted">Næste kørsel</span><br />{data.next ? fmtDate(data.next) : "—"}</div>
        <span className="tiny muted">Planen ændres i definitionen (schedule, wait_for_load, max_minutes, paused) og er en patch.</span>
      </div>
      <div className="dev-card small">
        <strong>Afhængigheder</strong>
        {data.dependsOn.length === 0 && <span className="muted">Afhænger ikke af andre pipelines.</span>}
        {data.dependsOn.map((d) => <span key={d.id}>Starter, når <Link to={`/developer/pipeline/${d.id}`}>{d.slug}</Link> er færdig</span>)}
        {data.dependents.map((d) => <span key={d.id}>Bruges af <Link to={`/developer/pipeline/${d.id}`}>{d.name}</Link></span>)}
        <span className="tiny muted">Cirkulære afhængigheder afvises.</span>
      </div>
    </div>
  );
  const runs = <RunTable runs={data.runs} />;
  return (
    <DevPage title={<span className="mono">{data.artifact.slug}</span>} intro={<>{data.artifact.name} · v{data.artifact.version ?? "—"}{data.snapshot && <> · <Link to={`/developer/katalog/${data.snapshot.id}`}>{data.snapshot.key}</Link></>}</>}
             actions={<StatusDot status={data.artifact.status} label={data.artifact.statusLabel} />}>
      <Tabs tabs={[["editor", "Editor"], ["plan", "Plan"], ["runs", "Kørsler"], ["lineage", "Lineage"], ["owners", "Versioner og ejere"]]} value={tab} onChange={setTab} />
      {tab === "editor" && editor}
      {tab === "plan" && plan}
      {tab === "runs" && runs}
      {tab === "lineage" && lineage}
      {tab === "owners" && <ArtifactTabs artifact={data.artifact} onChange={(a) => setData({ ...data, artifact: a })} />}
    </DevPage>
  );
}

function PreviewView({ p }: { p: Preview }) {
  return (
    <div className="stack">
      <span className="small muted">Eksempelrækker efter dette trin, udsnit af data, maskeret</span>
      <div className="dev-wrap" style={{ maxHeight: 280 }}>
        <table className="dev-table">
          <thead><tr>{p.columns.map((c) => <th key={c.name}><Cls c={c.classification} suffix={<> {c.name}</>} /></th>)}</tr></thead>
          <tbody>{p.rows.map((r, i) => <tr key={i}>{p.columns.map((c) => <td key={c.name} className="small">{String(r[c.name] ?? "")}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {p.smallGroups > 0 && (
        <Alert kind="warn">{p.smallGroups} grupper har færre end {p.minGroupSize} rækker. Så små grupper kan afsløre enkeltpersoner.
          {p.sensitive.length > 0 ? ` Resultatet har persondata eller røde felter (${p.sensitive.join(", ")}), så versionen kan ikke udgives.` : " Hæv grænsen eller filtrér dem fra."}</Alert>
      )}
      <details><summary className="small">Genereret SQL</summary><pre className="dev-code">{p.sql}</pre></details>
    </div>
  );
}

function RunTable({ runs }: { runs: Run[] }) {
  const navigate = useNavigate();
  if (!runs.length) return <Empty>Ingen kørsler endnu.</Empty>;
  return (
    <div className="dev-wrap"><table className="dev-table">
      <thead><tr><th>Kørsel</th>{runs[0].name !== undefined && <th>Pipeline</th>}<th>Start</th><th>Varighed</th><th>Rækker</th><th>Status</th></tr></thead>
      <tbody>{runs.map((r) => (
        <tr key={r.id} className="clickable" onClick={() => navigate(`/developer/pipeline/koersler/${r.id}`)}>
          <td className="mono">#{r.id}</td>{r.name !== undefined && <td>{r.name}</td>}
          <td className="small">{fmtDate(r.started_at ?? r.queued_at)}</td><td className="small">{dur(r)}</td>
          <td className="num">{r.rows != null ? fmtNumber(r.rows) : "ingen"}</td>
          <td><StatusDot status={runStatus(r.status)} label={r.status === "failed" && r.error?.startsWith("Timeout") ? "Fejlet: timeout" : r.label} /></td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

export function RunsPage() {
  const { data, error } = useLoad(() => api.get<{ items: Run[] }>("/api/admin/dev/runs"), []);
  return (
    <DevPage title="Kørsler" intro="Hver kørsel logges med start, varighed, rækker, status, fejltekst og tid pr. fase.">
      {error && <Alert>{error}</Alert>}
      {data && <RunTable runs={data.items} />}
    </DevPage>
  );
}

type RunDetail = Run & { name: string; slug: string; phases: { phase: string; label: string; ms: number; detail: string }[] };

export function RunPage() {
  const { id } = useParams();
  const { data, error } = useLoad(() => api.get<RunDetail>(`/api/admin/dev/runs/${id}`), [id]);
  useCrumb(data ? `${data.slug} #${data.id}` : null);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;
  const total = Math.max(1, data.phases.reduce((a, p) => a + p.ms, 0));
  let offset = 0;
  const fmt = (ms: number) => (ms >= 60000 ? `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s` : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);
  return (
    <DevPage title={`Kørsel #${data.id}: tid pr. fase`} intro={<>{data.name} · {data.trigger === "manual" ? "manuel" : "planlagt"} · <StatusDot status={runStatus(data.status)} label={data.label} /></>}>
      {data.error && <Alert>{data.error}</Alert>}
      <div className="dev-card">
        {data.phases.map((p, i) => {
          const left = (offset / total) * 100;
          offset += p.ms;
          return (
            <div key={i} className="row" style={{ gap: 12, flexWrap: "nowrap" }}>
              <span className="small" style={{ width: 220, flex: "none" }}>{p.label} · {fmt(p.ms)}</span>
              <div style={{ flex: 1, position: "relative", height: 14 }}>
                <div className="dev-bar" style={{ position: "absolute", left: `${left}%`, width: `${Math.max(0.8, (p.ms / total) * 100)}%`, opacity: 0.5 + (i / data.phases.length) / 2 }} />
              </div>
            </div>
          );
        })}
        <span className="tiny muted">{data.rows != null ? `${fmtNumber(data.rows)} rækker` : ""}{data.size_bytes ? ` · snapshot ${fmtBytes(data.size_bytes)}` : ""} · REFRESH CONCURRENTLY, så læsere aldrig blokeres</span>
      </div>
    </DevPage>
  );
}

type Snap = { id: number; key: string; name: string; refreshed_at: string | null; version: string | null; size_bytes: number | null; rows: number | null;
  pipeline_artifact_id: number; usedBy: { id: number; type: string; name: string }[] };

export function SnapshotsPage() {
  const { data, error } = useLoad(() => api.get<{ items: Snap[] }>("/api/admin/dev/snapshots"), []);
  const age = (t: string | null) => {
    if (!t) return "—";
    const m = Math.round((Date.now() - new Date(t).getTime()) / 60000);
    return m >= 60 ? `${Math.floor(m / 60)} t ${m % 60} min` : `${m} min`;
  };
  return (
    <DevPage title="Snapshots" intro="Materialized views i schemaet snapshots. Datastudie foretrækker dem frem for tunge opslag.">
      {error && <Alert>{error}</Alert>}
      {data && data.items.length === 0 && <Empty>Ingen snapshots endnu.</Empty>}
      {data?.items.map((s) => (
        <div key={s.id} className="dev-card">
          <div className="spread"><strong className="mono">{s.key}</strong><Link to={`/developer/pipeline/${s.pipeline_artifact_id}`} className="small">Pipeline →</Link></div>
          <div className="row small" style={{ gap: 24 }}>
            <span>Sidst genopfrisket<br /><strong>{s.refreshed_at ? fmtDate(s.refreshed_at) : "—"}</strong></span>
            <span>Alder<br /><strong>{age(s.refreshed_at)}</strong></span>
            <span>Størrelse<br /><strong>{s.size_bytes ? fmtBytes(s.size_bytes) : "—"}</strong></span>
            <span>Rækker<br /><strong>{s.rows != null ? fmtNumber(s.rows) : "—"}</strong></span>
            <span>Version<br /><strong>{s.version ?? "—"}</strong></span>
          </div>
          <span className="small"><strong>Bruges af:</strong> {s.usedBy.length ? s.usedBy.map((u) => `${u.type} ${u.name}`).join(", ") : "ingen endnu"}</span>
          <span className="tiny muted">En major-ændring her gør ejerne af alt ovenfor til Consulted i godkendelsen.</span>
        </div>
      ))}
    </DevPage>
  );
}

export function PlansPage() {
  const { data } = useLoad(() => api.get<{ items: ListItem[] }>("/api/admin/dev/pipelines"), []);
  const { data: ingest } = useLoad(() => api.get<{ date: string; items: { source: string; status: string; finished_at: string | null; row_count: number | null }[] }>("/api/admin/dev/ingest"), []);
  return (
    <DevPage title="Planer" intro="Planen styrer, hvornår en pipeline kører, og hvad den venter på. Daglige pipelines starter, når nattens indlæsning er færdig, tidligst kl. 06:00. Er den ikke færdig kl. 08:00, får kørslen status Udskudt.">
      <div className="dev-card">
        <strong>Nattens indlæsning ({ingest?.date ?? "i dag"})</strong>
        {ingest && ingest.items.length === 0 && <span className="small muted">Ingen kilder har meldt status i dag.</span>}
        {ingest?.items.map((i) => <span key={i.source} className="small"><StatusDot status={i.status === "succeeded" ? "ok" : i.status === "failed" ? "failed" : "pending"} label={`${i.source}: ${i.status}`} />{i.row_count != null ? ` · ${fmtNumber(i.row_count)} rækker` : ""}</span>)}
      </div>
      <div className="dev-wrap"><table className="dev-table">
        <thead><tr><th>Pipeline</th><th>Næste kørsel</th><th>Sidste</th></tr></thead>
        <tbody>{(data?.items ?? []).map((p) => (
          <tr key={p.id}><td><Link to={`/developer/pipeline/${p.id}`} className="mono">{p.slug}</Link></td><td className="small">{p.next ? fmtDate(p.next) : "manuelt"}</td>
            <td>{p.lastRun ? <StatusDot status={runStatus(p.lastRun.status)} label={p.lastRunLabel ?? ""} /> : "—"}</td></tr>
        ))}</tbody>
      </table></div>
    </DevPage>
  );
}
