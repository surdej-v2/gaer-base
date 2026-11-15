// /developer/datastudie/multisource — multisource sources (DS-10 … DS-17): the steps as a graph level by level, each with
// its TTL and cache key, sample rows, time and calls; "Ryd cache" and "Forhåndsvis".
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, useLoad } from "../ui";
import { DevPage, useCrumb } from "./DeveloperShell";
import { ArtifactTabs, Empty, StatusDot, type ArtifactDetail } from "./devui";

type Item = { id: number; slug: string; name: string; version: string | null; status: string; statusLabel: string; steps: string[] };

const EXAMPLE = {
  description: "Lejemål med ejendom og lejer", key: "id", partial: "mark", params: [{ name: "postnr", type: "string" }],
  limits: { max_items: 200, deadline_ms: 15000, timeout_ms: 5000, retries: 2, concurrency: 10 },
  steps: [
    { id: "liste", operation: 0, args: { postnr: "$param.postnr" }, paginate: true, ttl: 900 },
    { id: "ejendom", operation: 0, after: ["liste"], join: { local: "ejendom_id", foreign: "id" }, ttl: 0 },
    { id: "lejer", operation: 0, foreach: "liste", args: { ids: "$item.lejer_id" }, batch: { param: "ids", key: "id", size: 20 }, ttl: 300 },
  ],
};

export function MultisourceList() {
  const { data, error, reload } = useLoad(() => api.get<{ items: Item[] }>("/api/admin/dev/multisource"), []);
  const navigate = useNavigate();
  const [slug, setSlug] = useState("");
  const [def, setDef] = useState(JSON.stringify(EXAMPLE, null, 2));
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    setErr(null);
    try {
      const r = await api.post<{ id: number }>("/api/admin/dev/multisource", { slug, name: slug, definition: JSON.parse(def) });
      reload();
      navigate(`/developer/datastudie/multisource/${r.id}`);
    } catch (e) {
      setErr(e instanceof SyntaxError ? "Definitionen er ikke gyldig JSON" : errorText(e));
    }
  };
  return (
    <DevPage title="Multisource-kilder" intro="Kilder, der henter fra flere kilder live: trin uden afhængigheder køres parallelt og flettes efter en fælles nøgle, med paginering, foreach, batch-kald og cache pr. trin."
             actions={<button className="btn btn-primary btn-sm" onClick={() => setOpen(true)}>+ Ny kilde</button>}>
      {error && <Alert>{error}</Alert>}
      {open && (
        <div className="dev-card">
          <input className="dev-input" placeholder="navn, fx lejemaal_detaljer" value={slug} onChange={(e) => setSlug(e.target.value)} aria-label="Navn" />
          <textarea className="dev-textarea" value={def} onChange={(e) => setDef(e.target.value)} spellCheck={false} aria-label="Definition (JSON)" />
          <span className="tiny muted">operation er id'et på en operation; http: {"{"}source, dataset{"}"} er en API-kilde; delegated: true er udvidelsespunktet for brugerens eget token.</span>
          {err && <Alert>{err}</Alert>}
          <div className="row" style={{ gap: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>Fortryd</button>
            <button className="btn btn-primary btn-sm" onClick={create} disabled={!slug}>Opret</button></div>
        </div>
      )}
      {data && data.items.length === 0 && !open && <Empty>Ingen multisource-kilder endnu.</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Kilde</th><th>Trin</th><th>Version</th><th>Status</th></tr></thead>
          <tbody>{data.items.map((m) => (
            <tr key={m.id} className="clickable" onClick={() => navigate(`/developer/datastudie/multisource/${m.id}`)}>
              <td className="mono">{m.slug}</td><td className="small">{m.steps.join(" → ")}</td><td className="mono">{m.version ?? "—"}</td>
              <td><StatusDot status={m.status} label={m.statusLabel} /></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </DevPage>
  );
}

type Step = { id: string; operation?: number; http?: { source: string; dataset: string }; after: string[]; foreach: string | null; ttl: number;
  delegated: boolean; batch: { size?: number } | null; concurrency?: number; paginate: unknown };
type Detail = { artifact: ArtifactDetail; levels: string[][] };
type RunStep = { id: string; rows: number; ms: number; calls: number; cache: string | null; errors: string[]; throttled: number; sample: Record<string, unknown>[] };
type Run = { rows: Record<string, unknown>[]; count: number; partial: boolean; steps: RunStep[]; ms: number };

export function MultisourcePage() {
  const { id } = useParams();
  const { data, error, reload, setData } = useLoad(() => api.get<Detail>(`/api/admin/dev/multisource/${id}`), [id]);
  const [args, setArgs] = useState("{}");
  const [run, setRun] = useState<Run | null>(null);
  const [def, setDef] = useState("");
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  useCrumb(data?.artifact.slug);
  const working = data ? (data.artifact.draft ?? data.artifact.current) : null;
  useEffect(() => {
    if (working) setDef(JSON.stringify(Object.fromEntries(Object.entries(working.definition).filter(([k]) => !k.startsWith("_"))), (k, v) => (k === "_output" ? undefined : v), 2));
  }, [working]);
  if (error) return <Alert>{error}</Alert>;
  if (!data || !working) return null;
  const steps = (working.definition.steps as Step[]) ?? [];
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  const stats = Object.fromEntries((run?.steps ?? []).map((s) => [s.id, s]));
  const tryIt = async (fresh = false) => {
    setMsg(null);
    try {
      setRun(await api.post<Run>(`/api/admin/dev/multisource/${id}/try`, { args: JSON.parse(args), cache: !fresh }));
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof SyntaxError ? "Parametrene er ikke gyldig JSON" : errorText(e) });
    }
  };
  const save = async (submit: boolean) => {
    try {
      const r = await api.put<{ version: { version: string; statusLabel: string } }>(`/api/admin/dev/multisource/${id}`, { definition: JSON.parse(def), submit });
      setMsg({ kind: "success", text: `Version ${r.version.version}: ${r.version.statusLabel.toLowerCase()}.` });
      reload();
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof SyntaxError ? "Definitionen er ikke gyldig JSON" : errorText(e) });
    }
  };
  const label = (s: Step) => s.http ? `${s.http.source}.${s.http.dataset}` : s.delegated ? "delegeret" : `operation ${s.operation}`;
  const graph = (
    <div className="stack">
      <div className="row" style={{ gap: 16, alignItems: "stretch", flexWrap: "wrap" }}>
        {data.levels.map((lvl, i) => (
          <div key={i} className="stack" style={{ "--gap": "8px", minWidth: 200 } as React.CSSProperties}>
            <span className="tiny muted">{i === 0 ? "Først" : `Niveau ${i + 1}`}{lvl.length > 1 ? " · parallelt" : ""}</span>
            {lvl.map((sid) => {
              const s = byId[sid];
              const st = stats[sid];
              return (
                <div key={sid} className={`dev-stage${s.foreach ? " on" : ""}`}>
                  <strong>{s.foreach ? `Foreach: ${sid}` : s.after.length ? `Opslag: ${sid}` : `Liste: ${sid}`}</strong><br />
                  <span className="tiny muted">{label(s)}{s.paginate ? " · paginering" : ""}{s.batch ? ` · batch ${s.batch.size ?? 20}` : ""}
                    {s.foreach ? ` · maks. ${s.concurrency ?? 10} samtidige` : ""} · TTL {s.ttl ? `${Math.round(s.ttl / 60)} min` : "ingen"}</span>
                  {st && <><br /><span className="tiny">{st.rows} rækker · {st.ms} ms · {st.calls} kald · cache {st.cache ?? "—"}{st.throttled ? ` · 429 × ${st.throttled}` : ""}</span>
                    {st.errors.length > 0 && <><br /><span className="tiny" style={{ color: "#b91c1c" }}>{st.errors[0]}</span></>}</>}
                </div>
              );
            })}
          </div>
        ))}
        <div className="stack" style={{ "--gap": "8px", minWidth: 160 } as React.CSSProperties}>
          <span className="tiny muted">Til sidst</span>
          <div className="dev-stage"><strong>Flet resultat</strong><br /><span className="tiny muted">Maskering anvendes i hvert trin</span></div>
        </div>
      </div>
      <div className="dev-wrap"><table className="dev-table">
        <thead><tr><th>Trin</th><th>TTL</th><th>Cache-nøgle</th></tr></thead>
        <tbody>{steps.map((s) => <tr key={s.id}><td>{s.id}</td><td>{s.ttl ? `${Math.round(s.ttl / 60)} min` : "ingen"}</td><td>{s.delegated ? "Pr. bruger (delegeret)" : "Delt"}</td></tr>)}</tbody>
      </table></div>
      <div className="row" style={{ gap: 8 }}>
        <input className="dev-input mono" style={{ flex: 1 }} value={args} onChange={(e) => setArgs(e.target.value)} aria-label="Parametre (JSON)" />
        <button className="btn btn-outline btn-sm" onClick={() => tryIt(true)}>Ryd cache</button>
        <button className="btn btn-primary btn-sm" onClick={() => tryIt()}>Forhåndsvis</button>
      </div>
      {run && (
        <>
          <span className="small">{run.count} rækker · {run.ms} ms{run.partial ? " · delvist resultat (se _fejl)" : ""}</span>
          <pre className="dev-code" style={{ maxHeight: 320 }}>{JSON.stringify(run.rows.slice(0, 10), null, 2)}</pre>
        </>
      )}
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
    </div>
  );
  const editor = (
    <div className="stack">
      <textarea className="dev-textarea" style={{ minHeight: 320 }} value={def} onChange={(e) => setDef(e.target.value)} spellCheck={false} aria-label="Definition" />
      <div className="row" style={{ gap: 8 }}><button className="btn btn-outline btn-sm" onClick={() => save(false)}>Gem kladde</button>
        <button className="btn btn-primary btn-sm" onClick={() => save(true)}>Send til godkendelse</button></div>
    </div>
  );
  return (
    <DevPage title={<span className="mono">{data.artifact.slug}</span>} intro={`${data.artifact.name} · v${data.artifact.version ?? "—"}`}
             actions={<StatusDot status={data.artifact.status} label={data.artifact.statusLabel} />}>
      <ArtifactTabs artifact={data.artifact} onChange={(a) => setData({ ...data, artifact: a })} extra={[["graph", "Trin og cache", graph], ["edit", "Definition", editor]]} />
    </DevPage>
  );
}
