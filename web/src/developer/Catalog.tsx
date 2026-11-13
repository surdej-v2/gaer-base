// /developer/katalog — Datakatalog (F-5, F-7 … F-9): datasets, every field with classification, personal data and
// masking; changes are new versions that follow the approval flow; lineage for computed fields.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage, devState, useCrumb } from "./DeveloperShell";
import { ArtifactTabs, Cls, Empty, MASK_DA, StatusDot, type ArtifactDetail } from "./devui";

type Row = { id: number; key: string; name: string; description: string; kind: string; kindLabel: string; source_name: string; fields: number;
  personal_fields: number; max_rank: number | null; artifact_status: string | null; refreshed_at: string | null };
const RANK_CLS = ["white", "green", "yellow", "red"];

export function CatalogList() {
  const { data, error } = useLoad(() => api.get<{ items: Row[] }>("/api/admin/dev/catalog"), []);
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const items = (data?.items ?? []).filter((d) => !q || `${d.name} ${d.key} ${d.source_name}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <DevPage title="Datasæt" intro="Alle datakilder, datasæt og felter med klassifikation og persondata. Datasæt tilføjes fra forbindelser, API-kilder og pipelines."
             actions={<input className="dev-input" placeholder="Filtrér" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filtrér datasæt" />}>
      {error && <Alert>{error}</Alert>}
      {data && items.length === 0 && <Empty>Kataloget er tomt. Tilføj tabeller i /admin/data, en forbindelse eller en pipeline.</Empty>}
      {items.length > 0 && (
        <div className="dev-wrap">
          <table className="dev-table">
            <thead><tr><th>Datasæt</th><th>Kilde</th><th>Type</th><th>Felter</th><th>Strengeste</th><th>Persondata</th><th>Status</th></tr></thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id} className="clickable" onClick={() => navigate(`/developer/katalog/${d.id}`)}>
                  <td><strong>{d.name}</strong> <span className="tiny muted mono">{d.key}</span></td>
                  <td>{d.source_name}</td>
                  <td>{d.kindLabel}</td>
                  <td className="num">{d.fields}</td>
                  <td><Cls c={d.max_rank == null ? null : RANK_CLS[d.max_rank]} /></td>
                  <td>{d.personal_fields ? `Ja (${d.personal_fields})` : "Nej"}</td>
                  <td>{d.artifact_status ? <StatusDot status={d.artifact_status} label={d.artifact_status === "review_required" ? "Kræver review" : "Versioneret"} /> : <span className="tiny muted">Ikke versioneret endnu</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </DevPage>
  );
}

type Field = { id: number; name: string; label: string; description: string; data_type: string; classification: string; personal_data: boolean;
  sensitive: boolean; mask: string | null; inherited: boolean };
type Detail = { dataset: Row & { refreshed_at: string | null; generation: number }; fields: Field[];
  lineage: { field: string; classification: string; source_label: string; step: string; transform: string; source_classification: string | null }[];
  artifact: ArtifactDetail | null };
type Suggestion = { field: string; classification: string; personal_data: boolean; why: string; ai?: boolean };

const MASKS = ["", "show", "round", "interval", "aggregate", "partial", "hash", "mask", "remove"];

export function CatalogDataset() {
  const { id } = useParams();
  const { data, error, reload, setData } = useLoad(() => api.get<Detail>(`/api/admin/dev/catalog/datasets/${id}`), [id]);
  const [edit, setEdit] = useState<Field[] | null>(null);
  const [description, setDescription] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<{ kind: "error" | "success" | "info"; text: string } | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  useCrumb(data?.dataset.name);
  useEffect(() => {
    if (data) {
      setEdit(data.fields.map((f) => ({ ...f })));
      setDescription(data.dataset.description);
    }
  }, [data]);
  if (error) return <Alert>{error}</Alert>;
  if (!data || !edit) return null;
  const set = (name: string, patch: Partial<Field>) => setEdit((fs) => fs!.map((f) => (f.name === name ? { ...f, ...patch } : f)));
  const changed = edit.filter((f) => { const o = data.fields.find((x) => x.name === f.name)!; return JSON.stringify(o) !== JSON.stringify(f); });
  const save = async () => {
    setMsg(null);
    try {
      const r = await api.put<{ version: { version: string; status: string; statusLabel: string; changeType: string } }>(
        `/api/admin/dev/catalog/datasets/${id}`,
        { description, reason, fields: changed.map((f) => ({ name: f.name, label: f.label, description: f.description, classification: f.classification,
          personal_data: f.personal_data, sensitive: f.sensitive, mask: f.mask ?? "" })) });
      setMsg({ kind: r.version.status === "published" ? "success" : "info",
               text: `Version ${r.version.version} (${r.version.changeType}): ${r.version.statusLabel.toLowerCase()}.` });
      setReason("");
      await reload();
      devState.refresh();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const suggest = async () => {
    try {
      setSuggestions((await api.post<{ suggestions: Suggestion[] }>(`/api/admin/dev/catalog/datasets/${id}/suggest`)).suggestions);
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  };
  const d = data.dataset;
  const table = (
    <div className="stack">
      <textarea className="dev-input" style={{ height: 60, padding: 8 }} value={description} onChange={(e) => setDescription(e.target.value)} aria-label="Beskrivelse" placeholder="Beskrivelse af datasættet" />
      <div className="dev-wrap">
        <table className="dev-table">
          <thead><tr><th>Felt</th><th>Type</th><th>Klassifikation</th><th>Persondata</th><th>Følsom</th><th>Maskering</th><th>Beskrivelse</th></tr></thead>
          <tbody>
            {edit.map((f) => (
              <tr key={f.name}>
                <td className="mono">{f.name}{f.inherited && <span className="tiny muted"> (arvet)</span>}</td>
                <td className="muted">{f.data_type}</td>
                <td>
                  <select className="dev-select" value={f.classification} onChange={(e) => set(f.name, { classification: e.target.value })} aria-label={`Klassifikation for ${f.name}`}>
                    {["white", "green", "yellow", "red"].map((c) => <option key={c} value={c}>{({ white: "Hvid", green: "Grøn", yellow: "Gul", red: "Rød" } as Record<string, string>)[c]}</option>)}
                  </select>
                </td>
                <td><input type="checkbox" checked={f.personal_data} onChange={(e) => set(f.name, { personal_data: e.target.checked })} aria-label={`Persondata ${f.name}`} /></td>
                <td><input type="checkbox" checked={f.sensitive} onChange={(e) => set(f.name, { sensitive: e.target.checked })} aria-label={`Følsom ${f.name}`} /></td>
                <td>
                  <select className="dev-select" value={f.mask ?? ""} onChange={(e) => set(f.name, { mask: e.target.value || null })} aria-label={`Maskering ${f.name}`}>
                    {MASKS.map((m) => <option key={m} value={m}>{m ? MASK_DA[m] : "Standard"}</option>)}
                  </select>
                </td>
                <td><input className="dev-input" style={{ width: "100%" }} value={f.description} onChange={(e) => set(f.name, { description: e.target.value })} aria-label={`Beskrivelse ${f.name}`} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {suggestions.length > 0 && (
        <div className="dev-card" style={{ background: "#eff6ff", borderColor: "#bfdbfe" }}>
          <strong>{suggestions.some((s) => s.ai) ? "AI-forslag til klassifikation" : "Forslag til klassifikation"}</strong>
          {suggestions.map((s) => (
            <div key={s.field} className="spread small">
              <span><span className="mono">{s.field}</span>: foreslået <Cls c={s.classification} />{s.personal_data ? " · persondata" : ""} <span className="tiny muted">{s.why}</span></span>
              <button className="btn btn-outline btn-sm" onClick={() => { set(s.field, { classification: s.classification, personal_data: s.personal_data }); setSuggestions((x) => x.filter((y) => y.field !== s.field)); }}>Acceptér</button>
            </div>
          ))}
        </div>
      )}
      <div className="row" style={{ gap: 8 }}>
        <input className="dev-input" style={{ flex: 1, minWidth: 220 }} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Begrundelse (kræves ved nedklassificering)" aria-label="Begrundelse" />
        <button className="btn btn-outline btn-sm" onClick={suggest}>Foreslå klassifikation</button>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={!changed.length && description === d.description}>Gem som ny version</button>
      </div>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
    </div>
  );
  const lineage = (
    <div className="stack">
      {data.lineage.length === 0 && <Empty>Ingen lineage. Felter i snapshots fra Pipeline får lineage automatisk.</Empty>}
      {data.lineage.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Kildefelt</th><th>Trin</th><th>Snapshot-felt</th></tr></thead>
          <tbody>{data.lineage.map((l, i) => (
            <tr key={i}><td><Cls c={l.source_classification} suffix={<span className="mono"> {l.source_label}</span>} /></td>
              <td className="mono small">{l.transform || l.step}</td><td><Cls c={l.classification} suffix={<span className="mono"> {l.field}</span>} /></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
  return (
    <DevPage title={d.name} intro={<>Kilde: {d.source_name} · {d.kindLabel} · <span className="mono">{d.key}</span>{d.refreshed_at ? ` · opdateret ${fmtDate(d.refreshed_at)}` : ""}</>}>
      {data.artifact
        ? <ArtifactTabs artifact={data.artifact} onChange={(a) => setData({ ...data, artifact: a })} extra={[["fields", "Felter", table], ["lineage", "Lineage", lineage]]} />
        : <>{table}{lineage}</>}
    </DevPage>
  );
}

export function CatalogLineage() {
  const { data } = useLoad(() => api.get<{ items: Row[] }>("/api/admin/dev/catalog"), []);
  const snaps = (data?.items ?? []).filter((d) => d.kind === "snapshot");
  return (
    <DevPage title="Lineage" intro="Snapshot-felter arver den strengeste klassifikation fra de felter, de er beregnet ud fra (F-9). Vælg et datasæt for at se felternes kilder.">
      {snaps.length === 0 && <Empty>Ingen snapshots endnu. Byg et i <Link to="/developer/pipeline">Pipeline</Link>.</Empty>}
      <div className="dev-cards">
        {snaps.map((d) => (
          <Link key={d.id} to={`/developer/katalog/${d.id}`} className="dev-card" style={{ textDecoration: "none", color: "inherit" }}>
            <strong>{d.name}</strong><span className="tiny muted mono">{d.key}</span><span className="small">{d.fields} felter</span>
          </Link>
        ))}
      </div>
    </DevPage>
  );
}
