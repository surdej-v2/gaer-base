// /developer/forbindelser — Dataforbindelser (F-11, F-12, PL-5): databases through FDW or directly, live sources and
// the delegated extension point. Creating and changing is for Udvikler-admin; everyone with /developer can read.
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage, devState, useCrumb } from "./DeveloperShell";
import { ArtifactTabs, Empty, StatusDot, Tabs, type ArtifactDetail } from "./devui";

type Item = { id: number; key: string; name: string; kind: string; kindLabel: string; access: string; secret: string; datasets: number;
  status: string; problem: string | null; artifactId: number | null };
type Caps = { postgres_fdw: boolean; mysql_fdw: boolean; analytics_separate: boolean; ingest_table: string };

const EXAMPLE = { type: "postgres", host: "db.example.dk", port: 5432, database: "lejedata", user: "laeser", password: "data/lejedata-laeser",
  schema: "public", access: "fdw", tables: ["lejemaal", "lejere"], ingest_source: "mysql_backup_1" };

export function ConnectionList() {
  const { data, error, reload } = useLoad(() => api.get<{ items: Item[]; capabilities: Caps }>("/api/admin/dev/connections"), []);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const devAdmin = devState.me?.devAdmin;
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [def, setDef] = useState(JSON.stringify(EXAMPLE, null, 2));
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    setErr(null);
    try {
      const r = await api.post<{ id: number }>("/api/admin/dev/connections", { key, name: name || key, definition: JSON.parse(def) });
      setParams({});
      reload();
      navigate(`/developer/forbindelser/${r.id}`);
    } catch (e) {
      setErr(e instanceof SyntaxError ? "Definitionen er ikke gyldig JSON" : errorText(e));
    }
  };
  const caps = data?.capabilities;
  return (
    <DevPage title="Dataforbindelser" intro="Konfigurationen henviser kun til hemmeligheder i lageret. Databaser tilgås via foreign data wrappers; kun datasæt, kataloget bruger, oprettes som fremmede tabeller."
             actions={devAdmin ? <button className="btn btn-primary btn-sm" onClick={() => setParams({ ny: "1" })}>+ Ny forbindelse</button> : undefined}>
      {error && <Alert>{error}</Alert>}
      {caps && (
        <div className="row small muted" style={{ gap: 16 }}>
          <StatusDot status={caps.postgres_fdw ? "ok" : "failed"} label={`postgres_fdw ${caps.postgres_fdw ? "installeret" : "mangler"}`} />
          <StatusDot status={caps.mysql_fdw ? "ok" : "failed"} label={`mysql_fdw ${caps.mysql_fdw ? "installeret" : "mangler"}`} />
          <span>Snapshots: {caps.analytics_separate ? "egen analysedatabase" : "denne database (schema snapshots)"}</span>
          <span>Indlæsning: <span className="mono">{caps.ingest_table}</span></span>
        </div>
      )}
      {params.get("ny") && devAdmin && (
        <div className="dev-card">
          <strong>Ny forbindelse</strong>
          <div className="row" style={{ gap: 8 }}>
            <input className="dev-input" placeholder="nøgle (fx mysql_backup_1)" value={key} onChange={(e) => setKey(e.target.value)} aria-label="Nøgle" />
            <input className="dev-input" placeholder="Navn" value={name} onChange={(e) => setName(e.target.value)} aria-label="Navn" />
          </div>
          <textarea className="dev-textarea" style={{ minHeight: 200 }} value={def} onChange={(e) => setDef(e.target.value)} spellCheck={false} aria-label="Definition (JSON)" />
          <span className="tiny muted">type: postgres, mysql eller delegated · access: fdw eller direct · password er navnet på en hemmelighed (gruppe/navn), aldrig værdien.</span>
          {err && <Alert>{err}</Alert>}
          <div className="row" style={{ gap: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => setParams({})}>Fortryd</button>
            <button className="btn btn-primary btn-sm" onClick={create} disabled={!key}>Opret</button></div>
        </div>
      )}
      {data && data.items.length === 0 && <Empty>Ingen forbindelser endnu.</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Navn</th><th>Type</th><th>Adgang</th><th>Hemmelighed</th><th>Datasæt</th><th>Status</th></tr></thead>
          <tbody>{data.items.map((c) => (
            <tr key={c.id} className="clickable" onClick={() => navigate(c.kind === "rest" ? `/developer/studio/api?source=${c.id}` : `/developer/forbindelser/${c.id}`)}>
              <td><strong>{c.name}</strong> <span className="tiny muted mono">{c.key}</span></td><td>{c.kindLabel}</td><td>{c.access}</td>
              <td className="muted">{c.secret}</td><td className="num">{c.datasets}</td>
              <td><StatusDot status={c.status} label={c.problem ?? "OK"} /></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <div className="dev-card small">
        <strong>Delegeret adgang</strong>
        <span className="muted">Live-kilder med brugerens eget token (OAuth) er et udvidelsespunkt: en forbindelse af typen delegated findes og svarer “ikke tilgængelig endnu”, indtil login-udbyderne er bygget. Cachenøglen tager allerede brugeren med.</span>
      </div>
    </DevPage>
  );
}

type Detail = { id: number; key: string; name: string; kind: string; kindLabel: string; definition: Record<string, unknown>;
  secrets: { ref: string; ok: boolean; text: string }[]; datasets: { id: number; key: string; name: string; fields: number; refreshed_at: string | null }[];
  artifact: ArtifactDetail | null; problem: string | null; schema: string | null };

export function ConnectionPage() {
  const { id } = useParams();
  const { data, error, reload, setData } = useLoad(() => api.get<Detail>(`/api/admin/dev/connections/${id}`), [id]);
  const [tab, setTab] = useState("overview");
  const [def, setDef] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "error" | "success" | "info"; text: string } | null>(null);
  const [tables, setTables] = useState<Record<string, unknown[]> | null>(null);
  useCrumb(data?.name);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;
  const devAdmin = devState.me?.devAdmin;
  const act = async (what: "test" | "sync") => {
    setMsg(null);
    try {
      if (what === "test") {
        const r = await api.post<{ tables: Record<string, unknown[]>; via: string }>(`/api/admin/dev/connections/${id}/test`);
        setTables(r.tables);
        setMsg({ kind: "success", text: `Forbindelsen virker (${r.via === "fdw" ? "via FDW" : "direkte"}). ${Object.keys(r.tables).length} tabeller.` });
      } else {
        const r = await api.post<{ datasets: number[]; missing: string[] }>(`/api/admin/dev/connections/${id}/sync`);
        setMsg({ kind: r.missing.length ? "info" : "success", text: `${r.datasets.length} datasæt i kataloget.${r.missing.length ? ` Findes ikke: ${r.missing.join(", ")}` : ""}` });
        reload();
      }
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const save = async () => {
    try {
      const r = await api.put<{ version: { version: string; statusLabel: string } }>(`/api/admin/dev/connections/${id}`, { definition: JSON.parse(def ?? "{}") });
      setMsg({ kind: "success", text: `Version ${r.version.version}: ${r.version.statusLabel.toLowerCase()}. Kør “Opdatér katalog” for at bruge den.` });
      setDef(null);
      reload();
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof SyntaxError ? "Ikke gyldig JSON" : errorText(e) });
    }
  };
  return (
    <DevPage title={data.name} intro={<>{data.kindLabel} · <span className="mono">{data.key}</span>{data.schema && <> · fremmede tabeller i <span className="mono">{data.schema}</span></>}</>}
             actions={<StatusDot status={data.problem ? "failed" : "ok"} label={data.problem ?? "OK"} />}>
      <Tabs tabs={[["overview", "Forbindelse"], ["owners", "Versioner og ejere"]]} value={tab} onChange={setTab} />
      {tab === "overview" && (
        <div className="stack">
          {data.secrets.map((s) => <StatusDot key={s.ref} status={s.ok ? "ok" : "failed"} label={s.text} />)}
          {def === null ? <pre className="dev-code">{JSON.stringify(data.definition, null, 2)}</pre>
            : <textarea className="dev-textarea" value={def} onChange={(e) => setDef(e.target.value)} spellCheck={false} aria-label="Definition" />}
          {devAdmin && (
            <div className="row" style={{ gap: 8 }}>
              {def === null ? <button className="btn btn-outline btn-sm" onClick={() => setDef(JSON.stringify(data.definition, null, 2))}>Ret</button>
                : <><button className="btn btn-ghost btn-sm" onClick={() => setDef(null)}>Fortryd</button><button className="btn btn-primary btn-sm" onClick={save}>Gem som ny version</button></>}
              <button className="btn btn-outline btn-sm" onClick={() => act("test")}>Test forbindelsen</button>
              <button className="btn btn-primary btn-sm" onClick={() => act("sync")}>Opdatér katalog</button>
            </div>
          )}
          {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
          {tables && <div className="small muted">Tabeller: {Object.keys(tables).join(", ") || "ingen"}</div>}
          <strong>Datasæt i kataloget</strong>
          {data.datasets.length === 0 ? <span className="small muted">Ingen endnu. Vælg tabeller i definitionen og opdatér kataloget.</span> : (
            <div className="dev-wrap"><table className="dev-table">
              <thead><tr><th>Datasæt</th><th>Felter</th><th>Opdateret</th></tr></thead>
              <tbody>{data.datasets.map((d) => <tr key={d.id}><td><Link to={`/developer/katalog/${d.id}`}>{d.name}</Link></td><td className="num">{d.fields}</td><td className="small">{fmtDate(d.refreshed_at)}</td></tr>)}</tbody>
            </table></div>
          )}
        </div>
      )}
      {tab === "owners" && data.artifact && <ArtifactTabs artifact={data.artifact} onChange={(a) => setData({ ...data, artifact: a })} />}
    </DevPage>
  );
}
