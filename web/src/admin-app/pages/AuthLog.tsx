import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, query } from "../../api";
import { Alert, fmtDate, Json, Loading, PageHeader, Pager, useLoad } from "../../ui";

type Row = { id: number; ts: string; event: string; result: "ok" | "fail"; user_id: number | null; phone_masked: string | null; ip: string | null; device: string | null; detail: object };

const LIMIT = 100;
const FIELDS = ["user_id", "phone", "device", "ip", "event", "result", "from", "to"] as const;

export default function AuthLog() {
  const [params, setParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const filters = Object.fromEntries(FIELDS.map((f) => [f, params.get(f) ?? ""])) as Record<(typeof FIELDS)[number], string>;
  const log = useLoad(() => api.get<{ items: Row[]; total: number; events: string[] }>(
    `/api/admin/logs/auth${query({ ...filters, limit: LIMIT, offset })}`), [params.toString(), offset]);

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
    setOffset(0);
  };

  return (
    <>
      <PageHeader eyebrow="/admin/logs/auth" title="Auth-log">
        Alle login-hændelser med tidspunkt, maskeret telefonnummer, enhed, IP og resultat. Søg på det fulde telefonnummer —
        det sammenlignes med en hash, så nummeret står aldrig i klartekst i loggen.
      </PageHeader>
      <div className="filters">
        <label className="field">Telefonnummer<input value={filters.phone} onChange={(e) => set("phone", e.target.value)} /></label>
        <label className="field">Bruger-id<input value={filters.user_id} inputMode="numeric" onChange={(e) => set("user_id", e.target.value)} /></label>
        <label className="field">Enhed<input value={filters.device} placeholder="fx iOS" onChange={(e) => set("device", e.target.value)} /></label>
        <label className="field">IP<input value={filters.ip} onChange={(e) => set("ip", e.target.value)} /></label>
        <label className="field">Hændelse
          <select value={filters.event} onChange={(e) => set("event", e.target.value)}>
            <option value="">Alle</option>
            {log.data?.events.map((ev) => <option key={ev}>{ev}</option>)}
          </select>
        </label>
        <label className="field">Resultat
          <select value={filters.result} onChange={(e) => set("result", e.target.value)}>
            <option value="">Alle</option><option value="ok">ok</option><option value="fail">fejl</option>
          </select>
        </label>
        <label className="field">Fra<input type="datetime-local" value={filters.from} onChange={(e) => set("from", e.target.value)} /></label>
        <label className="field">Til<input type="datetime-local" value={filters.to} onChange={(e) => set("to", e.target.value)} /></label>
      </div>
      <Alert>{log.error}</Alert>
      {log.loading && !log.data ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Tidspunkt</th><th>Hændelse</th><th>Bruger</th><th>Enhed</th><th>IP</th><th>Detaljer</th></tr></thead>
            <tbody>
              {log.data?.items.length === 0 && <tr><td colSpan={6} className="muted">Ingen hændelser.</td></tr>}
              {log.data?.items.map((r) => (
                <tr key={r.id}>
                  <td style={{ whiteSpace: "nowrap" }}>{fmtDate(r.ts)}</td>
                  <td><span className={`pill mono ${r.result === "ok" ? "" : "red"}`}>{r.event}</span></td>
                  <td style={{ whiteSpace: "nowrap" }}>{r.phone_masked ?? "—"}{r.user_id && <div className="muted tiny">#{r.user_id}</div>}</td>
                  <td>{r.device ?? "—"}</td>
                  <td className="mono small">{r.ip ?? "—"}</td>
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
