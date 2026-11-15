// /developer/actions — Action-studie: flows (a trigger and a list of steps) as artifacts with versions, RACI and
// approval, built in a vertical designer à la Logic Apps (trigger card, step cards, branches, + between them) with a
// JSON view kept in sync; the trigger page; the runs with status and input/output per step. The engine and the rules:
// server/app/devkit/flows.py and flow_runs.py; the API: server/app/routes/developer_flows.py.
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowDown, ArrowUp, Copy, Play, Plus, Trash2, X } from "lucide-react";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage, devState, useCrumb } from "./DeveloperShell";
import { ArtifactTabs, Empty, StatusDot, Tabs, type ArtifactDetail } from "./devui";

// ── Types ───────────────────────────────────────────────────────────────────

type Step = {
  id: string; action: string; name?: string; when?: string; on_error?: "stop" | "continue";
  retry?: { count: number; delay_s: number }; timeout_s?: number; with?: Record<string, unknown>;
  then?: Step[]; else?: Step[]; value?: string; cases?: Record<string, Step[]>; default?: Step[];
  items?: string; steps?: Step[]; concurrency?: number; max_items?: number;
};
type InputSpec = { type: string; required: boolean; description: string };
type Trigger = { type: string; inputs?: Record<string, InputSpec>; cron?: string; tz?: string; events?: string[]; endpoint_key?: string;
  pipeline?: number | string; on?: string[] };
type Definition = { trigger: Trigger; steps: Step[]; on_error: Step[]; settings: { timeout_s: number; concurrency: number; keep_outputs: boolean }; description?: string };
type Field = { name: string; label: string; type: string; required: boolean; help: string; options?: string[]; default?: unknown };
type ActionSpec = { key: string; label: string; group: string; description: string; output: string; fields: Field[] };
type Catalog = {
  actions: ActionSpec[]; triggers: { key: string; label: string }[]; functions: { signature: string; description: string }[];
  events: { key: string; description: string }[]; cron_presets: { cron: string; label: string }[];
  operations: { id: number; slug: string; name: string }[]; pipelines: { id: number; slug: string; name: string }[];
  flows: { id: number; slug: string; name: string }[]; sources: { key: string; name: string }[];
  endpoints: { key: string; name: string; active: boolean }[];
};
type Run = { id: number; artifact_id: number; is_test: boolean; trigger: string; triggerLabel: string; status: string; label: string; error: string | null;
  waiting: { kind: string; until: string } | null; depth: number; parent_run_id: number | null; queued_at: string; started_at: string | null;
  finished_at: string | null; steps_done: number; name?: string; slug?: string };
type RunStep = { id: number; key: string; step_id: string; action: string; actionLabel: string; attempt: number; status: string; label: string;
  input: unknown; output: unknown; error: string | null; started_at: string; finished_at: string | null };
type RunDetail = Run & { name: string; slug: string; trigger_data: Record<string, unknown>; output: unknown; definition: Definition; vars: Record<string, unknown>;
  steps: RunStep[]; approvals: { id: number; title: string; status: string; expires_at: string; decided_at: string | null; comment: string | null }[];
  children: Run[] };
type ListItem = { id: number; slug: string; name: string; status: string; statusLabel: string; version: string | null; trigger: string | null; triggerText: string;
  steps: number; draft: string | null; next: string | null; lastRun: Run | null; failed7d: number; open: number };
type Detail = { artifact: ArtifactDetail; definition: Definition; runs: Run[]; next: string | null; triggerText: string;
  subscriptions: { kind: string; key: string }[]; endpoint: { key: string; name: string; url: string; active: boolean } | null };

type Path = (string | number)[];   // into the definition: ["steps", 2, "then", 0]

const runStatus = (s: string) => (s === "succeeded" ? "ok" : s);
const CONTROL = new Set(["if", "switch", "foreach"]);

function dur(a: string | null, b: string | null): string {
  if (!a || !b) return "—";
  const ms = new Date(b).getTime() - new Date(a).getTime();
  return ms >= 60000 ? `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s` : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

function getAt(def: Definition, path: Path): Step[] {
  let node: unknown = def;
  for (const p of path) node = (node as Record<string | number, unknown>)[p];
  return node as Step[];
}

function setAt(def: Definition, path: Path, value: unknown): Definition {
  const clone = structuredClone(def);
  let node: unknown = clone;
  for (const p of path.slice(0, -1)) node = (node as Record<string | number, unknown>)[p];
  (node as Record<string | number, unknown>)[path[path.length - 1]] = value;
  return clone;
}

function allIds(steps: Step[], out: string[] = []): string[] {
  for (const s of steps) {
    out.push(s.id);
    for (const k of ["then", "else", "steps", "default"] as const) if (s[k]) allIds(s[k]!, out);
    for (const branch of Object.values(s.cases ?? {})) allIds(branch, out);
  }
  return out;
}

function freshId(def: Definition, base: string): string {
  const taken = new Set([...allIds(def.steps), ...allIds(def.on_error)]);
  const stem = base.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "s$1") || "trin";
  if (!taken.has(stem)) return stem;
  for (let i = 2; i < 1000; i++) if (!taken.has(`${stem}_${i}`)) return `${stem}_${i}`;
  return `${stem}_${Date.now()}`;
}

function newStep(def: Definition, spec: ActionSpec): Step {
  const step: Step = { id: freshId(def, spec.key), action: spec.key, with: {} };
  for (const f of spec.fields) if (f.default !== undefined) step.with![f.name] = f.default;
  if (spec.key === "if") Object.assign(step, { when: "{{ true }}", then: [], else: [] });
  if (spec.key === "switch") Object.assign(step, { value: "{{ trigger.body.type }}", cases: { a: [] }, default: [] });
  if (spec.key === "foreach") Object.assign(step, { items: "{{ trigger.body.items }}", steps: [] });
  return step;
}

function summary(step: Step): string {
  const w = step.with ?? {};
  switch (step.action) {
    case "http": return `${w.method ?? "GET"} ${w.url ?? ""}`;
    case "if": return step.when ?? "";
    case "switch": return step.value ?? "";
    case "foreach": return step.items ?? "";
    case "set": return `vars.${w.name ?? "?"}`;
    case "mail": return String(w.subject ?? "");
    case "message": return String(w.title ?? "");
    case "approval": return String(w.title ?? "");
    case "delay": return w.seconds ? `${w.seconds} s` : String(w.until ?? "");
    case "operation": case "pipeline": case "flow": return String(w[step.action] ?? "");
    case "file": return `${w.path ?? ""}${w.filename ?? ""}`;
    case "event": return String(w.name ?? "");
    case "ai": return String(w.prompt ?? "").slice(0, 60);
    default: return typeof w.value === "string" ? w.value : w.value !== undefined ? JSON.stringify(w.value) : "";
  }
}

const s = {
  card: { background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 12, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4, cursor: "pointer", textAlign: "left" } as CSSProperties,
  label: { display: "flex", flexDirection: "column", gap: 4, fontSize: 13, fontWeight: 500 } as CSSProperties,
  input: { height: 34, padding: "0 10px", border: "1px solid #e2e8f0", borderRadius: 8, font: "13px var(--hm-font-mono)", background: "#ffffff" } as CSSProperties,
  area: { minHeight: 72, padding: 8, border: "1px solid #e2e8f0", borderRadius: 8, font: "13px var(--hm-font-mono)", background: "#ffffff", resize: "vertical" } as CSSProperties,
  branch: { flex: 1, minWidth: 180, display: "flex", flexDirection: "column", gap: 6, padding: 8, borderRadius: 10, background: "#f8fafc", border: "1px dashed #cbd5e1" } as CSSProperties,
};

// ── The list ────────────────────────────────────────────────────────────────

export function FlowList() {
  const { data, error } = useLoad(() => api.get<{ items: ListItem[] }>("/api/admin/dev/flows"), []);
  const navigate = useNavigate();
  return (
    <DevPage title="Flows" intro="Et flow starter på en udløser og kører sine handlinger ét trin ad gangen: kald et API, send en mail, vent på en godkendelse. Som Logic Apps og Power Automate — men i Gær, med versioner, ejere og audit."
             actions={<Link to="/developer/actions/ny" className="btn btn-primary btn-sm">+ Nyt flow</Link>}>
      {error && <Alert>{error}</Alert>}
      {data && data.items.length === 0 && <Empty>Ingen flows endnu. <Link to="/developer/actions/ny">Byg det første</Link>.</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Flow</th><th>Udløser</th><th>Trin</th><th>Version</th><th>Sidste kørsel</th><th>Næste</th><th>Fejl, 7 dage</th></tr></thead>
          <tbody>{data.items.map((f) => (
            <tr key={f.id} className="clickable" onClick={() => navigate(`/developer/actions/${f.id}`)}>
              <td><strong>{f.name}</strong> <span className="tiny muted mono">{f.slug}</span>{f.draft && <span className="tiny muted"> · {f.draft === "pending" ? "afventer" : "kladde"}</span>}
                {f.status === "disabled" && <span className="pill" style={{ marginLeft: 6 }}>Slået fra</span>}</td>
              <td className="small">{f.triggerText}</td>
              <td className="num">{f.steps}</td>
              <td className="mono">{f.version ?? "—"}</td>
              <td className="small">{f.lastRun ? <StatusDot status={runStatus(f.lastRun.status)} label={`${f.lastRun.label} · ${fmtDate(f.lastRun.started_at ?? f.lastRun.queued_at)}`} /> : <span className="muted">Ikke kørt</span>}</td>
              <td className="small">{f.next ? fmtDate(f.next) : f.trigger === "manual" ? "manuelt" : "—"}</td>
              <td className="num">{f.failed7d || ""}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </DevPage>
  );
}

// ── A new flow ──────────────────────────────────────────────────────────────

export function FlowNew() {
  const navigate = useNavigate();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState("manual");
  const [err, setErr] = useState<string | null>(null);
  const { data: cat } = useLoad(() => api.get<Catalog>("/api/admin/dev/flows/catalog"), []);
  const create = async () => {
    setErr(null);
    try {
      const r = await api.post<{ id: number }>("/api/admin/dev/flows", { slug, name: name || slug, trigger });
      devState.refresh();
      navigate(`/developer/actions/${r.id}`);
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <DevPage title="Nyt flow" intro="Giv flowet et navn, og vælg, hvad der starter det. Trinene bygger du bagefter i designeren.">
      <div className="dev-card" style={{ maxWidth: 560 }}>
        <label style={s.label}>Navn (id)<input className="dev-input" placeholder="fx velkomst_til_nye_brugere" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "_"))} /></label>
        <label style={s.label}>Visningsnavn<input className="dev-input" placeholder="Velkomst til nye brugere" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
          <legend style={{ fontSize: 13, fontWeight: 500, paddingBottom: 6 }}>Udløser</legend>
          {(cat?.triggers ?? []).map((t) => (
            <label key={t.key} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
              <input type="radio" name="trigger" checked={trigger === t.key} onChange={() => setTrigger(t.key)} />{t.label}
            </label>
          ))}
        </fieldset>
        {err && <Alert>{err}</Alert>}
        <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={create} disabled={!slug}>Opret kladde</button>
      </div>
    </DevPage>
  );
}

// ── The editor ──────────────────────────────────────────────────────────────

export function FlowEditor() {
  const { id } = useParams();
  const { data, error, reload, setData } = useLoad(() => api.get<Detail>(`/api/admin/dev/flows/${id}`), [id]);
  const { data: cat } = useLoad(() => api.get<Catalog>("/api/admin/dev/flows/catalog"), []);
  const [def, setDef] = useState<Definition | null>(null);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState("designer");
  const [mode, setMode] = useState<"visual" | "json">("visual");
  const [json, setJson] = useState("");
  const [selected, setSelected] = useState<Path | null>(null);
  const [adding, setAdding] = useState<{ path: Path; index: number } | null>(null);
  const [msg, setMsg] = useState<{ kind: "error" | "success" | "info" | "warn"; text: string } | null>(null);
  const [testInput, setTestInput] = useState("{}");
  useCrumb(data?.artifact.slug);
  useEffect(() => {
    if (data) {
      setDef(data.definition);
      setJson(JSON.stringify(data.definition, null, 2));
      setDirty(false);
    }
  }, [data]);
  if (error) return <Alert>{error}</Alert>;
  if (!data || !def || !cat) return null;

  const update = (next: Definition) => {
    setDef(next);
    setJson(JSON.stringify(next, null, 2));
    setDirty(true);
  };
  const applyJson = () => {
    try {
      update(JSON.parse(json) as Definition);
      setMode("visual");
      setMsg(null);
    } catch {
      setMsg({ kind: "error", text: "Definitionen er ikke gyldig JSON" });
    }
  };
  const save = async (): Promise<boolean> => {
    setMsg(null);
    try {
      const r = await api.put<{ version: { version: string; changeType: string }; definition: Definition }>(`/api/admin/dev/flows/${id}`, { definition: mode === "json" ? JSON.parse(json) : def });
      setMsg({ kind: "success", text: `Kladde ${r.version.version} gemt (${r.version.changeType}).` });
      setDirty(false);
      await reload();
      return true;
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
      return false;
    }
  };
  const submit = async () => {
    if (dirty && !(await save())) return;
    try {
      const v = await api.post<{ version: string; statusLabel: string }>(`/api/admin/dev/artifacts/${id}/submit`, {});
      setMsg({ kind: "success", text: `Version ${v.version}: ${v.statusLabel.toLowerCase()}.` });
      reload();
      devState.refresh();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const run = async (draft: boolean) => {
    if (draft && dirty && !(await save())) return;
    let input: unknown = {};
    try {
      input = testInput.trim() ? JSON.parse(testInput) : {};
    } catch {
      setMsg({ kind: "error", text: "Input skal være JSON, fx {\"navn\": \"Anna\"}" });
      return;
    }
    try {
      const r = await api.post<{ run_id: number }>(`/api/admin/dev/flows/${id}/run`, { input, draft });
      setMsg({ kind: "info", text: `${draft ? "Testkørsel" : "Kørsel"} #${r.run_id} er sat i gang.` });
      setTab("runs");
      reload();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const toggle = async () => {
    try {
      await api.patch(`/api/admin/dev/artifacts/${id}`, { enabled: data.artifact.status === "disabled" });
      reload();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };

  const selectedStep = selected ? (getAt(def, selected) as unknown as Step) : null;
  const addStep = (spec: ActionSpec) => {
    if (!adding) return;
    const list = [...getAt(def, adding.path)];
    const step = newStep(def, spec);
    list.splice(adding.index, 0, step);
    update(setAt(def, adding.path, list));
    setSelected([...adding.path, adding.index]);
    setAdding(null);
  };
  const removeStep = (path: Path) => {
    const list = [...getAt(def, path.slice(0, -1))];
    list.splice(path[path.length - 1] as number, 1);
    update(setAt(def, path.slice(0, -1), list));
    setSelected(null);
  };
  const moveStep = (path: Path, d: number) => {
    const list = [...getAt(def, path.slice(0, -1))];
    const i = path[path.length - 1] as number;
    const j = i + d;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    update(setAt(def, path.slice(0, -1), list));
    setSelected([...path.slice(0, -1), j]);
  };
  const changeStep = (path: Path, step: Step) => update(setAt(def, path, step));

  const published = !!data.artifact.current;
  const designer = (
    <div className="stack" style={{ "--gap": "12px" } as CSSProperties}>
      <div className="spread">
        <span className="tiny muted">{dirty ? "Ændringer er ikke gemt" : "Designer og JSON holdes synkrone"}</span>
        <div className="segmented" role="tablist" aria-label="Visning" style={{ width: 180 }}>
          <button type="button" role="tab" aria-selected={mode === "visual"} onClick={() => (mode === "json" ? applyJson() : undefined)}>Designer</button>
          <button type="button" role="tab" aria-selected={mode === "json"} onClick={() => setMode("json")}>JSON</button>
        </div>
      </div>
      {mode === "json" ? (
        <textarea className="dev-textarea" style={{ minHeight: 420 }} value={json} onChange={(e) => { setJson(e.target.value); setDirty(true); }} spellCheck={false} aria-label="Definition i JSON" />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(300px, 380px)", gap: 16, alignItems: "start" }} className="flow-grid">
          <style>{`@media (max-width: 1000px) { .flow-grid { grid-template-columns: 1fr !important; } }`}</style>
          <div className="stack" style={{ "--gap": "0px" } as CSSProperties}>
            <button type="button" style={{ ...s.card, borderColor: selected?.[0] === "trigger" ? "#2563eb" : "#e2e8f0", background: "#eff6ff" }} onClick={() => setSelected(["trigger"])}>
              <span className="eyebrow">Udløser</span>
              <strong>{cat.triggers.find((t) => t.key === def.trigger.type)?.label ?? def.trigger.type}</strong>
              <span className="tiny muted">{data.triggerText}</span>
            </button>
            <StepList def={def} path={["steps"]} cat={cat} selected={selected} onSelect={setSelected} onAdd={(path, index) => setAdding({ path, index })} />
            {def.on_error.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <span className="eyebrow">Ved fejl</span>
                <StepList def={def} path={["on_error"]} cat={cat} selected={selected} onSelect={setSelected} onAdd={(path, index) => setAdding({ path, index })} />
              </div>
            )}
            {def.on_error.length === 0 && (
              <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start", marginTop: 12 }} onClick={() => setAdding({ path: ["on_error"], index: 0 })}>+ Trin, der kører ved fejl</button>
            )}
          </div>
          <aside className="stack" style={{ "--gap": "12px", position: "sticky", top: 12 } as CSSProperties}>
            {adding ? (
              <ActionPicker cat={cat} onPick={addStep} onClose={() => setAdding(null)} />
            ) : selected?.[0] === "trigger" ? (
              <TriggerPanel def={def} cat={cat} detail={data} flowId={Number(id)} onChange={update} onMessage={setMsg} />
            ) : selectedStep ? (
              <StepPanel key={selected!.join("/")} step={selectedStep} path={selected!} cat={cat} def={def} onChange={(st) => changeStep(selected!, st)}
                         onRemove={() => removeStep(selected!)} onMove={(d) => moveStep(selected!, d)} />
            ) : (
              <div className="dev-card small">
                <strong>Vælg et trin</strong>
                <span className="muted">Klik på udløseren eller et trin for at redigere det, eller på + for at tilføje et trin. Værdier kan bruge udtryk som <code>{"{{ trigger.body.navn }}"}</code>.</span>
                <ExprHelp cat={cat} lastRun={data.runs[0]?.id} />
              </div>
            )}
          </aside>
        </div>
      )}
      <div className="row" style={{ gap: 8, alignItems: "center" }}>
        <button className="btn btn-outline btn-sm" onClick={save} disabled={!dirty}>Gem kladde</button>
        <button className="btn btn-outline btn-sm" onClick={() => run(true)} disabled={!data.artifact.draft && !dirty} title="Kører kladden som en test">Testkør kladde</button>
        <button className="btn btn-outline btn-sm" onClick={() => run(false)} disabled={!published || data.artifact.status === "disabled"}><Play size={13} aria-hidden="true" /> Kør nu</button>
        <input className="dev-input" aria-label="Input til kørslen (JSON)" placeholder='Input, fx {"navn": "Anna"}' value={testInput} onChange={(e) => setTestInput(e.target.value)} style={{ flex: "1 1 200px", fontFamily: "var(--hm-font-mono)", fontSize: 12 }} />
        <button className="btn btn-primary btn-sm" onClick={submit}>Send til godkendelse</button>
      </div>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
    </div>
  );

  return (
    <DevPage title={data.artifact.name} intro={<><span className="mono">{data.artifact.slug}</span> · v{data.artifact.version ?? "—"} · {data.triggerText}{data.next && <> · næste kørsel {fmtDate(data.next)}</>}</>}
             actions={<div className="row" style={{ gap: 8 }}><StatusDot status={data.artifact.status} label={data.artifact.statusLabel} />
               <button className="btn btn-ghost btn-sm" onClick={toggle}>{data.artifact.status === "disabled" ? "Slå til" : "Slå fra"}</button></div>}>
      <Tabs tabs={[["designer", "Designer"], ["runs", `Kørsler · ${data.runs.length}`], ["settings", "Indstillinger"], ["owners", "Versioner og ejere"]]} value={tab} onChange={setTab} />
      {tab === "designer" && designer}
      {tab === "runs" && <RunTable runs={data.runs} />}
      {tab === "settings" && <SettingsPanel def={def} onChange={update} dirty={dirty} onSave={save} subscriptions={data.subscriptions} />}
      {tab === "owners" && <ArtifactTabs artifact={data.artifact} onChange={(a) => setData({ ...data, artifact: a })} />}
    </DevPage>
  );
}

function StepList({ def, path, cat, selected, onSelect, onAdd }: {
  def: Definition; path: Path; cat: Catalog; selected: Path | null; onSelect: (p: Path) => void; onAdd: (path: Path, index: number) => void;
}) {
  const steps = getAt(def, path);
  const plus = (index: number) => (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
      <span style={{ width: 2, height: 10, background: "#cbd5e1" }} />
      <button type="button" className="btn btn-ghost btn-sm" aria-label="Tilføj trin her" title="Tilføj trin" onClick={() => onAdd(path, index)}
              style={{ width: 26, height: 26, padding: 0, borderRadius: 9999, border: "1px solid #cbd5e1", background: "#ffffff" }}><Plus size={14} aria-hidden="true" /></button>
      <span style={{ width: 2, height: 10, background: "#cbd5e1" }} />
    </div>
  );
  return (
    <div className="stack" style={{ "--gap": "0px" } as CSSProperties}>
      {steps.map((step, i) => {
        const p = [...path, i];
        const on = selected?.join("/") === p.join("/");
        const spec = cat.actions.find((a) => a.key === step.action);
        return (
          <div key={step.id}>
            {plus(i)}
            <button type="button" style={{ ...s.card, borderColor: on ? "#2563eb" : "#e2e8f0", width: "100%", boxShadow: on ? "0 0 0 2px rgba(37,99,235,0.15)" : undefined }} onClick={() => onSelect(p)}>
              <span className="spread" style={{ gap: 8 }}>
                <span><strong>{step.name || spec?.label || step.action}</strong> <span className="tiny muted mono">{step.id}</span></span>
                <span className="tiny muted">{spec?.group}</span>
              </span>
              {summary(step) && <span className="tiny muted mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{summary(step)}</span>}
              {step.when && !CONTROL.has(step.action) && <span className="tiny" style={{ color: "#92400e" }}>kun hvis {step.when}</span>}
            </button>
            {step.action === "if" && (
              <div className="row" style={{ gap: 8, alignItems: "stretch", flexWrap: "wrap", marginTop: 6 }}>
                {(["then", "else"] as const).map((branch) => (
                  <div key={branch} style={s.branch}>
                    <span className="tiny muted">{branch === "then" ? "Hvis sandt" : "Ellers"}</span>
                    <StepList def={def} path={[...p, branch]} cat={cat} selected={selected} onSelect={onSelect} onAdd={onAdd} />
                  </div>
                ))}
              </div>
            )}
            {step.action === "switch" && (
              <div className="row" style={{ gap: 8, alignItems: "stretch", flexWrap: "wrap", marginTop: 6 }}>
                {Object.keys(step.cases ?? {}).map((c) => (
                  <div key={c} style={s.branch}>
                    <span className="tiny muted">= {c}</span>
                    <StepList def={def} path={[...p, "cases", c]} cat={cat} selected={selected} onSelect={onSelect} onAdd={onAdd} />
                  </div>
                ))}
                <div style={s.branch}>
                  <span className="tiny muted">Ellers</span>
                  <StepList def={def} path={[...p, "default"]} cat={cat} selected={selected} onSelect={onSelect} onAdd={onAdd} />
                </div>
              </div>
            )}
            {step.action === "foreach" && (
              <div style={{ ...s.branch, marginTop: 6, marginLeft: 24 }}>
                <span className="tiny muted">For hvert element (item, index)</span>
                <StepList def={def} path={[...p, "steps"]} cat={cat} selected={selected} onSelect={onSelect} onAdd={onAdd} />
              </div>
            )}
          </div>
        );
      })}
      {plus(steps.length)}
    </div>
  );
}

function ActionPicker({ cat, onPick, onClose }: { cat: Catalog; onPick: (a: ActionSpec) => void; onClose: () => void }) {
  const groups = [...new Set(cat.actions.map((a) => a.group))];
  return (
    <div className="dev-card" style={{ gap: 10 }}>
      <div className="spread"><strong>Tilføj trin</strong><button type="button" className="btn btn-ghost btn-sm" aria-label="Luk" onClick={onClose}><X size={14} aria-hidden="true" /></button></div>
      {groups.map((g) => (
        <div key={g} className="stack" style={{ "--gap": "4px" } as CSSProperties}>
          <span className="eyebrow">{g}</span>
          {cat.actions.filter((a) => a.group === g).map((a) => (
            <button key={a.key} type="button" style={{ ...s.card, padding: "8px 10px" }} onClick={() => onPick(a)}>
              <strong style={{ fontSize: 13 }}>{a.label}</strong><span className="tiny muted">{a.description}</span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

function FieldInput({ f, value, cat, onChange }: { f: Field; value: unknown; cat: Catalog; onChange: (v: unknown) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const text = (v: unknown) => (v === undefined || v === null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2));
  if (f.type === "bool") return <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />{f.label}</label>;
  if (f.type === "select") return <label style={s.label}>{f.label}<select style={s.input} value={String(value ?? f.default ?? "")} onChange={(e) => onChange(e.target.value)}>{(f.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}</select></label>;
  if (f.type === "multiselect") {
    const list = Array.isArray(value) ? (value as string[]) : [];
    return <div style={s.label}>{f.label}<div className="row" style={{ gap: 10 }}>{(f.options ?? []).map((o) => (
      <label key={o} style={{ display: "flex", gap: 4, alignItems: "center", fontWeight: 400 }}><input type="checkbox" checked={list.includes(o)} onChange={(e) => onChange(e.target.checked ? [...list, o] : list.filter((x) => x !== o))} />{o}</label>))}</div></div>;
  }
  if (f.type === "number") return <label style={s.label}>{f.label}<input type="number" style={s.input} value={value === undefined || value === null ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} /></label>;
  if (f.type === "operation" || f.type === "pipeline" || f.type === "flow" || f.type === "source") {
    const options = f.type === "source" ? cat.sources.map((x) => ({ value: x.key, label: `${x.name} (${x.key})` }))
      : (cat[f.type === "operation" ? "operations" : f.type === "pipeline" ? "pipelines" : "flows"]).map((x) => ({ value: x.slug, label: `${x.name} (${x.slug})` }));
    return <label style={s.label}>{f.label}<select style={s.input} value={String(value ?? "")} onChange={(e) => onChange(e.target.value || undefined)}>
      <option value="">{f.required ? "Vælg …" : "Ingen"}</option>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
      {f.help && <span className="tiny muted" style={{ fontWeight: 400 }}>{f.help}</span>}</label>;
  }
  if (f.type === "json") {
    const shown = draft ?? text(value);
    const commit = () => {
      if (draft === null) return;
      const t = draft.trim();
      if (!t) onChange(undefined);
      else if (t.includes("{{") && !t.startsWith("{") && !t.startsWith("[")) onChange(t);
      else { try { onChange(JSON.parse(t)); } catch { onChange(t); } }
      setDraft(null);
    };
    return <label style={s.label}>{f.label}<textarea style={s.area} value={shown} onChange={(e) => setDraft(e.target.value)} onBlur={commit} spellCheck={false} placeholder='JSON eller et udtryk, fx {"id": "{{ item.id }}"}' />
      {f.help && <span className="tiny muted" style={{ fontWeight: 400 }}>{f.help}</span>}</label>;
  }
  if (f.type === "text") return <label style={s.label}>{f.label}<textarea style={s.area} value={text(value)} onChange={(e) => onChange(e.target.value)} spellCheck={false} />{f.help && <span className="tiny muted" style={{ fontWeight: 400 }}>{f.help}</span>}</label>;
  return <label style={s.label}>{f.label}{f.required && <span className="tiny muted" style={{ fontWeight: 400 }}> (påkrævet)</span>}
    <input style={s.input} value={text(value)} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
    {f.help && <span className="tiny muted" style={{ fontWeight: 400 }}>{f.help}</span>}</label>;
}

function StepPanel({ step, path, cat, def, onChange, onRemove, onMove }: {
  step: Step; path: Path; cat: Catalog; def: Definition; onChange: (s: Step) => void; onRemove: () => void; onMove: (d: number) => void;
}) {
  const spec = cat.actions.find((a) => a.key === step.action);
  const [advanced, setAdvanced] = useState(false);
  const [caseDraft, setCaseDraft] = useState("");
  const set = (patch: Partial<Step>) => onChange({ ...step, ...patch });
  const setWith = (name: string, v: unknown) => {
    const w = { ...(step.with ?? {}) };
    if (v === undefined) delete w[name]; else w[name] = v;
    onChange({ ...step, with: w });
  };
  const idOk = /^[a-z][a-z0-9_]{0,39}$/.test(step.id);
  return (
    <div className="dev-card" style={{ gap: 10 }}>
      <div className="spread">
        <strong>{spec?.label ?? step.action}</strong>
        <span className="row" style={{ gap: 2 }}>
          <button type="button" className="btn btn-ghost btn-sm" aria-label="Flyt op" onClick={() => onMove(-1)}><ArrowUp size={14} aria-hidden="true" /></button>
          <button type="button" className="btn btn-ghost btn-sm" aria-label="Flyt ned" onClick={() => onMove(1)}><ArrowDown size={14} aria-hidden="true" /></button>
          <button type="button" className="btn btn-ghost btn-sm" aria-label="Slet trin" style={{ color: "#b91c1c" }} onClick={onRemove}><Trash2 size={14} aria-hidden="true" /></button>
        </span>
      </div>
      <span className="tiny muted">{spec?.description} Output: <code>{spec?.output}</code> — brug det som <code>{`{{ steps.${step.id}.… }}`}</code></span>
      <div className="row" style={{ gap: 8 }}>
        <label style={{ ...s.label, flex: 1 }}>Id<input style={{ ...s.input, borderColor: idOk ? undefined : "#dc2626" }} value={step.id} onChange={(e) => set({ id: e.target.value })} /></label>
        <label style={{ ...s.label, flex: 2 }}>Navn<input style={{ ...s.input, fontFamily: "var(--hm-font-sans)" }} value={step.name ?? ""} onChange={(e) => set({ name: e.target.value })} placeholder={spec?.label} /></label>
      </div>
      {step.action === "if" && <label style={s.label}>Betingelse<input style={s.input} value={step.when ?? ""} onChange={(e) => set({ when: e.target.value })} placeholder="{{ steps.hent.body.count > 0 }}" /></label>}
      {step.action === "switch" && (
        <>
          <label style={s.label}>Værdi<input style={s.input} value={step.value ?? ""} onChange={(e) => set({ value: e.target.value })} /></label>
          <div style={s.label}>Grene
            <div className="row" style={{ gap: 6 }}>
              {Object.keys(step.cases ?? {}).map((c) => (
                <span key={c} className="pill mono">{c}<button type="button" aria-label={`Fjern grenen ${c}`} className="btn btn-ghost btn-sm" style={{ padding: 0, width: 18, height: 18 }}
                  onClick={() => { const cases = { ...step.cases }; delete cases[c]; set({ cases }); }}><X size={10} aria-hidden="true" /></button></span>
              ))}
              <input style={{ ...s.input, height: 28, width: 120 }} placeholder="ny gren" value={caseDraft} onChange={(e) => setCaseDraft(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter" && caseDraft.trim()) { set({ cases: { ...step.cases, [caseDraft.trim()]: [] } }); setCaseDraft(""); } }} />
            </div>
          </div>
        </>
      )}
      {step.action === "foreach" && (
        <>
          <label style={s.label}>Liste<input style={s.input} value={step.items ?? ""} onChange={(e) => set({ items: e.target.value })} placeholder="{{ steps.hent.body.items }}" /></label>
          <label style={s.label}>Højst elementer<input type="number" style={s.input} value={step.max_items ?? 1000} onChange={(e) => set({ max_items: Number(e.target.value) })} /></label>
        </>
      )}
      {(spec?.fields ?? []).map((f) => <FieldInput key={f.name} f={f} value={step.with?.[f.name]} cat={cat} onChange={(v) => setWith(f.name, v)} />)}
      {!CONTROL.has(step.action) && (
        <label style={s.label}>Kør kun hvis (valgfrit)<input style={s.input} value={step.when ?? ""} onChange={(e) => set({ when: e.target.value || undefined })} placeholder="{{ vars.antal > 0 }}" /></label>
      )}
      <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setAdvanced(!advanced)}>{advanced ? "Skjul" : "Vis"} fejl og genforsøg</button>
      {advanced && (
        <div className="row" style={{ gap: 8 }}>
          <label style={{ ...s.label, flex: 1 }}>Ved fejl<select style={s.input} value={step.on_error ?? "stop"} onChange={(e) => set({ on_error: e.target.value as Step["on_error"] })}>
            <option value="stop">Stop flowet</option><option value="continue">Fortsæt (fejlen bliver trinnets output)</option></select></label>
          <label style={{ ...s.label, flex: 1 }}>Genforsøg<input type="number" min={0} max={10} style={s.input} value={step.retry?.count ?? 0} onChange={(e) => set({ retry: { count: Number(e.target.value), delay_s: step.retry?.delay_s ?? 10 } })} /></label>
          <label style={{ ...s.label, flex: 1 }}>Pause (s)<input type="number" min={0} style={s.input} value={step.retry?.delay_s ?? 10} onChange={(e) => set({ retry: { count: step.retry?.count ?? 0, delay_s: Number(e.target.value) } })} /></label>
          <label style={{ ...s.label, flex: 1 }}>Timeout (s)<input type="number" min={1} max={600} style={s.input} value={step.timeout_s ?? 30} onChange={(e) => set({ timeout_s: Number(e.target.value) })} /></label>
        </div>
      )}
      <ExprHelp cat={cat} compact ids={allIds(def.steps).filter((x) => x !== step.id)} />
      <span className="tiny muted">Sti: {path.join(" / ")}</span>
    </div>
  );
}

function ExprHelp({ cat, compact, ids, lastRun }: { cat: Catalog; compact?: boolean; ids?: string[]; lastRun?: number }) {
  const [open, setOpen] = useState(!compact);
  const [expression, setExpression] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const tryIt = async () => {
    try {
      const r = await api.post<{ ok: boolean; value?: unknown; type?: string; error?: string }>("/api/admin/dev/flows/expr", { expression, run_id: lastRun ?? null });
      setResult(r.ok ? `${r.type}: ${JSON.stringify(r.value)}` : `Fejl: ${r.error}`);
    } catch (e) {
      setResult(errorText(e));
    }
  };
  return (
    <div className="stack" style={{ "--gap": "6px" } as CSSProperties}>
      <button type="button" className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setOpen(!open)}>{open ? "Skjul" : "Vis"} udtryk og funktioner</button>
      {open && (
        <div className="stack small" style={{ "--gap": "6px" } as CSSProperties}>
          <span className="muted">Skriv <code>{"{{ … }}"}</code> i en værdi. Du kan nå <code>trigger.body</code>, <code>trigger.data</code>, <code>vars.x</code>, <code>run.id</code>, i en løkke <code>item</code> og <code>index</code>, og andre trin som <code>steps.&lt;id&gt;</code>{ids && ids.length > 0 && <>: {ids.map((i) => <code key={i} style={{ marginRight: 4 }}>{i}</code>)}</>}.</span>
          <div className="row" style={{ gap: 6 }}>
            <input style={{ ...s.input, flex: 1 }} placeholder="{{ upper('hej') }}" value={expression} onChange={(e) => setExpression(e.target.value)} onKeyDown={(e) => e.key === "Enter" && tryIt()} />
            <button type="button" className="btn btn-outline btn-sm" onClick={tryIt} disabled={!expression}>Prøv{lastRun ? ` på #${lastRun}` : ""}</button>
          </div>
          {result && <code className="tiny" style={{ wordBreak: "break-all" }}>{result}</code>}
          <details><summary className="tiny">Alle funktioner</summary>
            <table className="dev-table" style={{ fontSize: 12 }}><tbody>{cat.functions.map((f) => <tr key={f.signature}><td className="mono">{f.signature}</td><td>{f.description}</td></tr>)}</tbody></table>
          </details>
        </div>
      )}
    </div>
  );
}

function TriggerPanel({ def, cat, detail, flowId, onChange, onMessage }: {
  def: Definition; cat: Catalog; detail: Detail; flowId: number; onChange: (d: Definition) => void; onMessage: (m: { kind: "error" | "success" | "info" | "warn"; text: string } | null) => void;
}) {
  const t = def.trigger;
  const [secret, setSecret] = useState<{ url: string; secret: string } | null>(null);
  const [eventDraft, setEventDraft] = useState("");
  const [inputDraft, setInputDraft] = useState("");
  const setT = (patch: Partial<Trigger>) => onChange({ ...def, trigger: { ...t, ...patch } });
  const changeType = (type: string) => {
    const next: Trigger = { type, inputs: t.inputs ?? {} };
    if (type === "schedule") Object.assign(next, { cron: "0 7 * * 1-5", tz: "Europe/Copenhagen" });
    if (type === "event") next.events = ["user.created"];
    if (type === "pipeline") Object.assign(next, { pipeline: cat.pipelines[0]?.slug, on: ["succeeded"] });
    if (type === "webhook") next.endpoint_key = detail.endpoint?.key;
    onChange({ ...def, trigger: next });
  };
  const makeEndpoint = async () => {
    try {
      const r = await api.post<{ key: string; url: string; secret: string }>(`/api/admin/dev/flows/${flowId}/webhook-endpoint`);
      setSecret(r);
      setT({ endpoint_key: r.key });
      onMessage({ kind: "info", text: "Endpointet er oprettet. Gem kladden, så det følger med udløseren." });
    } catch (e) {
      onMessage({ kind: "error", text: errorText(e) });
    }
  };
  const inputs = t.inputs ?? {};
  return (
    <div className="dev-card" style={{ gap: 10 }}>
      <strong>Udløser</strong>
      <label style={s.label}>Hvad starter flowet<select style={s.input} value={t.type} onChange={(e) => changeType(e.target.value)}>{cat.triggers.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</select></label>
      {t.type === "schedule" && (
        <>
          <label style={s.label}>Tidsplan<select style={s.input} value={cat.cron_presets.some((p) => p.cron === t.cron) ? t.cron : "custom"} onChange={(e) => e.target.value !== "custom" && setT({ cron: e.target.value })}>
            {cat.cron_presets.map((p) => <option key={p.cron} value={p.cron}>{p.label}</option>)}<option value="custom">Egen cron …</option></select></label>
          <label style={s.label}>Cron (minut time dag måned ugedag)<input style={s.input} value={t.cron ?? ""} onChange={(e) => setT({ cron: e.target.value })} /></label>
          <label style={s.label}>Tidszone<input style={s.input} value={t.tz ?? "Europe/Copenhagen"} onChange={(e) => setT({ tz: e.target.value })} /></label>
          {detail.next && <span className="tiny muted">Næste kørsel efter den udgivne version: {fmtDate(detail.next)}</span>}
        </>
      )}
      {t.type === "event" && (
        <div style={s.label}>Hændelser
          <div className="row" style={{ gap: 6 }}>{(t.events ?? []).map((ev) => <span key={ev} className="pill mono">{ev}<button type="button" className="btn btn-ghost btn-sm" aria-label={`Fjern ${ev}`} style={{ padding: 0, width: 18, height: 18 }} onClick={() => setT({ events: (t.events ?? []).filter((x) => x !== ev) })}><X size={10} aria-hidden="true" /></button></span>)}</div>
          <div className="row" style={{ gap: 6 }}>
            <select style={{ ...s.input, flex: 1 }} value={eventDraft} onChange={(e) => setEventDraft(e.target.value)}>
              <option value="">Vælg en hændelse …</option>{cat.events.filter((e) => !(t.events ?? []).includes(e.key)).map((e) => <option key={e.key} value={e.key} title={e.description}>{e.key} — {e.description.slice(0, 70)}</option>)}
            </select>
            <button type="button" className="btn btn-outline btn-sm" disabled={!eventDraft} onClick={() => { setT({ events: [...(t.events ?? []), eventDraft] }); setEventDraft(""); }}>Tilføj</button>
          </div>
          <span className="tiny muted" style={{ fontWeight: 400 }}>Webhook-hændelser (fx user.created) starter flowet i samme transaktion som ændringen; tællende hændelser (fx files.upload) skrives i feltet som deres kind. Data ligger i <code>trigger.data</code>.</span>
        </div>
      )}
      {t.type === "pipeline" && (
        <>
          <label style={s.label}>Pipeline<select style={s.input} value={String(t.pipeline ?? "")} onChange={(e) => setT({ pipeline: e.target.value })}>{cat.pipelines.map((p) => <option key={p.id} value={p.slug}>{p.name}</option>)}</select></label>
          <div style={s.label}>Når kørslen er<div className="row" style={{ gap: 10 }}>{(["succeeded", "failed"] as const).map((o) => (
            <label key={o} style={{ display: "flex", gap: 4, alignItems: "center", fontWeight: 400 }}><input type="checkbox" checked={(t.on ?? ["succeeded"]).includes(o)}
              onChange={(e) => setT({ on: e.target.checked ? [...(t.on ?? []), o] : (t.on ?? []).filter((x) => x !== o) })} />{o === "succeeded" ? "lykkedes" : "fejlede"}</label>))}</div></div>
        </>
      )}
      {t.type === "webhook" && (
        <div className="stack small" style={{ "--gap": "6px" } as CSSProperties}>
          {t.endpoint_key ? (
            <>
              <span>Endpoint: <code>{t.endpoint_key}</code>{detail.endpoint && <> · {detail.endpoint.active ? "aktivt" : "slået fra"}</>}</span>
              {detail.endpoint && <CopyRow label="Adresse (POST)" value={detail.endpoint.url} />}
              <span className="muted">Afsenderen signerer med X-Gaer-Signature (hemmeligheden blev vist, da endpointet blev oprettet). Body og headers ligger i <code>trigger.body</code> og <code>trigger.headers</code>. Indstillinger og log: /admin/webhooks → Indgående.</span>
            </>
          ) : (
            <>
              <span className="muted">Flowet får sit eget indgående endpoint, verificeret med Gærs egen signatur. Du kan også vælge et eksisterende med handler »flow«.</span>
              <div className="row" style={{ gap: 6 }}>
                <button type="button" className="btn btn-primary btn-sm" onClick={makeEndpoint}>Opret endpoint</button>
                {cat.endpoints.length > 0 && <select style={{ ...s.input, flex: 1 }} value="" onChange={(e) => e.target.value && setT({ endpoint_key: e.target.value })}>
                  <option value="">Brug eksisterende …</option>{cat.endpoints.map((e) => <option key={e.key} value={e.key}>{e.name}</option>)}</select>}
              </div>
            </>
          )}
          {secret && (
            <div className="alert warn small" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <strong>Hemmeligheden vises kun nu</strong>
              <CopyRow label="Adresse" value={secret.url} />
              <CopyRow label="Hemmelighed" value={secret.secret} />
            </div>
          )}
        </div>
      )}
      {(t.type === "manual" || t.type === "webhook") && (
        <div style={s.label}>Input, flowet forventer
          {Object.entries(inputs).map(([name, spec]) => (
            <div key={name} className="row" style={{ gap: 6, fontWeight: 400 }}>
              <code style={{ flex: 1 }}>{name}</code>
              <select style={{ ...s.input, height: 28 }} value={spec.type} onChange={(e) => setT({ inputs: { ...inputs, [name]: { ...spec, type: e.target.value } } })}>
                {["string", "number", "boolean", "object", "list"].map((x) => <option key={x}>{x}</option>)}</select>
              <label style={{ display: "flex", gap: 4, alignItems: "center" }}><input type="checkbox" checked={spec.required} onChange={(e) => setT({ inputs: { ...inputs, [name]: { ...spec, required: e.target.checked } } })} />påkrævet</label>
              <button type="button" className="btn btn-ghost btn-sm" aria-label={`Fjern ${name}`} onClick={() => { const next = { ...inputs }; delete next[name]; setT({ inputs: next }); }}><X size={12} aria-hidden="true" /></button>
            </div>
          ))}
          <div className="row" style={{ gap: 6 }}>
            <input style={{ ...s.input, height: 28, flex: 1 }} placeholder="nyt input, fx navn" value={inputDraft} onChange={(e) => setInputDraft(e.target.value)} />
            <button type="button" className="btn btn-outline btn-sm" disabled={!/^[a-z][a-z0-9_]{0,39}$/.test(inputDraft)} onClick={() => { setT({ inputs: { ...inputs, [inputDraft]: { type: "string", required: false, description: "" } } }); setInputDraft(""); }}>Tilføj</button>
          </div>
          <span className="tiny muted" style={{ fontWeight: 400 }}>Nås som <code>trigger.body.&lt;navn&gt;</code>. Et nyt påkrævet input er en major, et valgfrit en minor.</span>
        </div>
      )}
    </div>
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="row" style={{ gap: 6, alignItems: "center" }}>
      <span className="tiny muted" style={{ width: 90, flex: "none" }}>{label}</span>
      <code className="tiny" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value}</code>
      <button type="button" className="btn btn-ghost btn-sm" aria-label={`Kopiér ${label}`} onClick={() => { navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
        {copied ? "Kopieret" : <Copy size={13} aria-hidden="true" />}</button>
    </div>
  );
}

function SettingsPanel({ def, onChange, dirty, onSave, subscriptions }: { def: Definition; onChange: (d: Definition) => void; dirty: boolean; onSave: () => void; subscriptions: { kind: string; key: string }[] }) {
  const st = def.settings;
  const set = (patch: Partial<Definition["settings"]>) => onChange({ ...def, settings: { ...st, ...patch } });
  return (
    <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
      <div className="dev-card">
        <strong>Kørsel</strong>
        <label style={s.label}>Maks. køretid (sekunder)<input type="number" min={10} max={86400} style={s.input} value={st.timeout_s} onChange={(e) => set({ timeout_s: Number(e.target.value) })} /></label>
        <label style={s.label}>Samtidige kørsler<input type="number" min={1} max={20} style={s.input} value={st.concurrency} onChange={(e) => set({ concurrency: Number(e.target.value) })} /></label>
        <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={st.keep_outputs} onChange={(e) => set({ keep_outputs: e.target.checked })} />Gem trinnenes output i kørselsloggen</label>
        <label style={s.label}>Beskrivelse<textarea style={s.area} value={def.description ?? ""} onChange={(e) => onChange({ ...def, description: e.target.value })} /></label>
        <span className="tiny muted">Indstillinger er en patch. Kørsler slettes efter sletningspolitikken »Flow-kørsler« (/admin/retention).</span>
        <button className="btn btn-outline btn-sm" style={{ alignSelf: "flex-start" }} onClick={onSave} disabled={!dirty}>Gem kladde</button>
      </div>
      <div className="dev-card small">
        <strong>Udløseren lytter på (udgivet version)</strong>
        {subscriptions.length === 0 && <span className="muted">Ingen abonnementer: flowet startes manuelt eller efter sin tidsplan.</span>}
        {subscriptions.map((x) => <span key={x.kind + x.key}><code>{x.kind}</code> · <code>{x.key}</code></span>)}
      </div>
    </div>
  );
}

// ── Runs ────────────────────────────────────────────────────────────────────

function RunTable({ runs }: { runs: Run[] }) {
  const navigate = useNavigate();
  if (!runs.length) return <Empty>Ingen kørsler endnu.</Empty>;
  const withName = runs[0].name !== undefined;
  return (
    <div className="dev-wrap"><table className="dev-table">
      <thead><tr><th>Kørsel</th>{withName && <th>Flow</th>}<th>Udløser</th><th>Start</th><th>Varighed</th><th>Trin</th><th>Status</th></tr></thead>
      <tbody>{runs.map((r) => (
        <tr key={r.id} className="clickable" onClick={() => navigate(`/developer/actions/koersler/${r.id}`)}>
          <td className="mono">#{r.id}{r.is_test && <span className="tiny muted"> test</span>}</td>
          {withName && <td>{r.name}</td>}
          <td className="small">{r.triggerLabel}</td>
          <td className="small">{fmtDate(r.started_at ?? r.queued_at)}</td>
          <td className="small">{dur(r.started_at, r.finished_at)}</td>
          <td className="num">{r.steps_done}</td>
          <td><StatusDot status={runStatus(r.status)} label={r.status === "waiting" && r.waiting ? `Venter (${r.waiting.kind})` : r.label} /></td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

export function FlowRunsPage() {
  const { data, error } = useLoad(() => api.get<{ items: Run[] }>("/api/admin/dev/flows/runs"), []);
  return (
    <DevPage title="Kørsler" intro="Hver kørsel logges med udløser, status, varighed og input/output pr. trin. En kørsel, der venter, fylder ingenting: den vækkes af et job, af den, der godkender, eller af det flow, den kaldte.">
      {error && <Alert>{error}</Alert>}
      {data && <RunTable runs={data.items} />}
    </DevPage>
  );
}

function Json({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="tiny muted">—</span>;
  return <pre className="dev-code" style={{ maxHeight: 260, overflow: "auto", margin: 0 }}>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

export function FlowRunPage() {
  const { id } = useParams();
  const { data, error, reload } = useLoad(() => api.get<RunDetail>(`/api/admin/dev/flows/runs/${id}`), [id]);
  const [msg, setMsg] = useState<string | null>(null);
  const navigate = useNavigate();
  useCrumb(data ? `${data.slug} #${data.id}` : null);
  useEffect(() => {
    if (!data || !["queued", "running", "waiting"].includes(data.status)) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [data, reload]);
  const open = useMemo(() => data ? data.steps.filter((x) => x.status !== "skipped").length : 0, [data]);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;
  const act = async (what: "cancel" | "rerun") => {
    setMsg(null);
    try {
      const r = await api.post<{ run_id?: number }>(`/api/admin/dev/flows/runs/${id}/${what}`);
      if (what === "rerun" && r.run_id) navigate(`/developer/actions/koersler/${r.run_id}`);
      else reload();
    } catch (e) {
      setMsg(errorText(e));
    }
  };
  return (
    <DevPage title={`Kørsel #${data.id}`}
             intro={<><Link to={`/developer/actions/${data.artifact_id}`}>{data.name}</Link> · {data.triggerLabel}{data.is_test && " · test af kladden"} · <StatusDot status={runStatus(data.status)} label={data.label} />
               {data.parent_run_id && <> · kaldt af <Link to={`/developer/actions/koersler/${data.parent_run_id}`}>#{data.parent_run_id}</Link></>}</>}
             actions={<div className="row" style={{ gap: 8 }}>
               {["queued", "running", "waiting"].includes(data.status) && <button className="btn btn-outline btn-sm" onClick={() => act("cancel")}>Afbryd</button>}
               <button className="btn btn-outline btn-sm" onClick={() => act("rerun")}>Kør igen</button></div>}>
      {msg && <Alert>{msg}</Alert>}
      {data.error && <Alert>{data.error}</Alert>}
      {data.waiting && data.status === "waiting" && <Alert kind="info">Venter ({data.waiting.kind}) — senest til {fmtDate(data.waiting.until)}.</Alert>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
        <div className="dev-card small"><span className="muted">Start</span>{fmtDate(data.started_at ?? data.queued_at)}<span className="muted">Varighed</span>{dur(data.started_at, data.finished_at)}<span className="muted">Trin kørt</span>{open}</div>
        <div className="dev-card small"><span className="muted">Udløserens data</span><Json value={data.trigger_data} /></div>
        <div className="dev-card small"><span className="muted">Output</span><Json value={data.output} />{Object.keys(data.vars).length > 0 && <><span className="muted">Variabler</span><Json value={data.vars} /></>}</div>
      </div>
      {data.approvals.length > 0 && (
        <div className="dev-card small"><strong>Godkendelser</strong>
          {data.approvals.map((a) => <span key={a.id}>{a.title} · <StatusDot status={a.status === "approved" ? "ok" : a.status === "pending" ? "pending" : a.status === "expired" ? "disabled" : "failed"} label={{ pending: "afventer", approved: "godkendt", rejected: "afvist", expired: "udløbet" }[a.status] ?? a.status} />
            {a.comment && <> · „{a.comment}“</>}{a.status === "pending" && <> · udløber {fmtDate(a.expires_at)}</>}</span>)}
        </div>
      )}
      <div className="dev-wrap"><table className="dev-table">
        <thead><tr><th>Trin</th><th>Handling</th><th>Forsøg</th><th>Start</th><th>Varighed</th><th>Status</th></tr></thead>
        <tbody>{data.steps.map((st) => (
          <StepRow key={st.id} step={st} />
        ))}</tbody>
      </table></div>
      {data.children.length > 0 && <><strong>Kaldte flows</strong><RunTable runs={data.children} /></>}
    </DevPage>
  );
}

function StepRow({ step }: { step: RunStep }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="clickable" onClick={() => setOpen(!open)}>
        <td><span className="mono">{step.step_id}</span> <span className="tiny muted">{step.key}</span></td>
        <td className="small">{step.actionLabel}</td>
        <td className="num">{step.attempt}</td>
        <td className="small">{fmtDate(step.started_at)}</td>
        <td className="small">{dur(step.started_at, step.finished_at)}</td>
        <td><StatusDot status={runStatus(step.status)} label={step.label} />{step.error && <span className="tiny" style={{ color: "#b91c1c", marginLeft: 6 }}>{step.error}</span>}</td>
      </tr>
      {open && (
        <tr><td colSpan={6}>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
            <div><span className="tiny muted">Input</span><Json value={step.input} /></div>
            <div><span className="tiny muted">Output</span><Json value={step.output} /></div>
          </div>
        </td></tr>
      )}
    </>
  );
}
