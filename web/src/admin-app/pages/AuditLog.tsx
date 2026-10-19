import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, query } from "../../api";
import { Alert, download, fmtDate, Json, Loading, PageHeader, Pager, useLoad } from "../../ui";

type Row = { id: number; ts: string; actor_user_id: number | null; action: string; table_name: string | null; row_id: string | null; before: object | null; after: object | null; ip: string | null; detail: object };

const LIMIT = 100;
const FIELDS = ["actor_id", "action", "table", "row_id", "from", "to"] as const;

export default function AuditLog() {
  const [params, setParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const [reads, setReads] = useState(false);
  const filters = Object.fromEntries(FIELDS.map((f) => [f, params.get(f) ?? ""])) as Record<(typeof FIELDS)[number], string>;
  const log = useLoad(() => api.get<{ items: Row[]; total: number; actions: string[]; tables: string[] }>(
    `/api/admin/logs/audit${query({ ...filters, include_reads: reads, limit: LIMIT, offset })}`), [params.toString(), offset, reads]);

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
    setOffset(0);
  };

  return (
    <>
      <div className="spread">
        <PageHeader eyebrow="/admin/logs/audit" title="Audit-log">
          Alle ændringer i data: hvem, hvornår, hvilken tabel og række, og værdier før og efter.
        </PageHeader>
        <button className="btn btn-outline" onClick={() => download(`/api/admin/logs/audit.csv${query(filters)}`)}>Eksportér CSV</button>
      </div>
      <div className="filters">
        <label className="field">Aktør (bruger-id)<input value={filters.actor_id} onChange={(e) => set("actor_id", e.target.value)} /></label>
        <label className="field">Handling
          <select value={filters.action} onChange={(e) => set("action", e.target.value)}>
            <option value="">Alle</option>
            {log.data?.actions.map((a) => <option key={a}>{a}</option>)}
          </select>
        </label>
        <label className="field">Tabel
          <select value={filters.table} onChange={(e) => set("table", e.target.value)}>
            <option value="">Alle</option>
            {log.data?.tables.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="field">Række<input value={filters.row_id} onChange={(e) => set("row_id", e.target.value)} /></label>
        <label className="field">Fra<input type="datetime-local" value={filters.from} onChange={(e) => set("from", e.target.value)} /></label>
        <label className="field">Til<input type="datetime-local" value={filters.to} onChange={(e) => set("to", e.target.value)} /></label>
        <label className="row small" style={{ fontWeight: 600 }}>
          <input type="checkbox" checked={reads} onChange={(e) => { setReads(e.target.checked); setOffset(0); }} /> Vis admin-opslag
        </label>
      </div>
      <Alert>{log.error}</Alert>
      {log.loading && !log.data ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Tidspunkt</th><th>Aktør</th><th>Handling</th><th>Række</th><th>Før</th><th>Efter</th><th>Detaljer</th></tr></thead>
            <tbody>
              {log.data?.items.length === 0 && <tr><td colSpan={7} className="muted">Ingen hændelser.</td></tr>}
              {log.data?.items.map((r) => (
                <tr key={r.id}>
                  <td style={{ whiteSpace: "nowrap" }}>{fmtDate(r.ts)}<div className="muted tiny mono">{r.ip}</div></td>
                  <td>{r.actor_user_id ? `#${r.actor_user_id}` : <span className="muted">system</span>}</td>
                  <td><span className="pill mono">{r.action}</span></td>
                  <td className="mono small">{r.table_name ? `${r.table_name}${r.row_id ? ` #${r.row_id}` : ""}` : "—"}</td>
                  <td className="wrap"><Json value={r.before} /></td>
                  <td className="wrap"><Json value={r.after} /></td>
                  <td className="wrap"><Json value={r.detail} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {log.data && <Pager total={log.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />}
    </>
  );
}
