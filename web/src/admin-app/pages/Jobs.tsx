// /admin/jobs: the background job queue, the recurring schedules and how long operational data is kept.
// The four logs (auth, audit, analytics, MCP) keep their periods in /admin/retention.
import { Fragment, useState } from "react";
import { api, errorText } from "../../api";
import { Alert, fmtDate, fmtNumber, Json, Loading, PageHeader, useLoad } from "../ui";

type Job = {
  id: number; kind: string; payload: unknown; status: string; run_at: string; attempts: number; max_attempts: number;
  last_error: string | null; result: unknown; schedule: string | null; created_at: string; finished_at: string | null;
};
type JobsData = { items: Job[]; counts: Record<string, number>; kinds: string[]; handlers: string[] };
type Schedule = {
  name: string; kind: string; description: string; interval_seconds: number; enabled: boolean; next_run_at: string;
  last_run_at: string | null; last_status: string | null; last_error: string | null;
};
type Policy = { key: string; title: string; description: string; days: number; min_days: number; enabled: boolean; last_run_at: string | null; last_deleted: number | null };

const STATUS: Record<string, [string, string]> = {
  queued: ["Venter", "amber"], running: ["Kører", "blue"], succeeded: ["Færdig", "green"], failed: ["Fejlet", "red"], dead: ["Opgivet", "red"],
};

function every(seconds: number): string {
  if (seconds % 86400 === 0) return seconds === 86400 ? "hvert døgn" : `hver ${seconds / 86400}. dag`;
  if (seconds % 3600 === 0) return seconds === 3600 ? "hver time" : `hver ${seconds / 3600}. time`;
  return `hvert ${Math.round(seconds / 60)}. minut`;
}

export default function Jobs() {
  const [status, setStatus] = useState("");
  const jobs = useLoad(() => api.get<JobsData>(`/api/admin/jobs${status ? `?status=${status}` : ""}`), [status]);
  const schedules = useLoad(() => api.get<Schedule[]>("/api/admin/jobs/schedules"));
  const policies = useLoad(() => api.get<Policy[]>("/api/admin/retention/policies"));
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<number | null>(null);

  const act = async (fn: () => Promise<unknown>, label: string) => {
    setError(null);
    setNotice(null);
    try {
      const r = await fn() as { deleted?: Record<string, number>; errors?: Record<string, string> } | undefined;
      const counts = r?.deleted ? " " + Object.entries(r.deleted).map(([k, v]) => `${k}: ${fmtNumber(v)}`).join(" · ") : "";
      setNotice(label + counts);
      // ClickHouse policies can fail on their own (the rest is still deleted).
      if (r?.errors && Object.keys(r.errors).length) {
        setError("Fejl: " + Object.entries(r.errors).map(([k, v]) => `${k}: ${v}`).join(" · "));
      }
      await Promise.all([jobs.reload(), schedules.reload(), policies.reload()]);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <PageHeader eyebrow="/admin/jobs" title="Jobs og oprydning">
        Baggrundsjobs kører i appens egen proces fra en kø i Postgres: webhook-leveringer, sletning af gamle logdata og
        oprydning. Et job, der fejler, prøves igen med stigende pause og opgives til sidst. Planlagte jobs og
        opbevaringsregler kan ændres her; alt bliver skrevet i audit-loggen.
      </PageHeader>
      <Alert kind="success">{notice}</Alert>
      <Alert>{error || jobs.error || schedules.error || policies.error}</Alert>

      <div className="card">
        <span className="eyebrow">Planlagte jobs</span>
        {!schedules.data ? <Loading /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Plan</th><th>Hvor tit</th><th>Næste kørsel</th><th>Sidst</th><th>Til</th><th /></tr></thead>
              <tbody>
                {schedules.data.map((s) => (
                  <tr key={s.name}>
                    <td><strong>{s.name}</strong><div className="muted tiny">{s.description}</div></td>
                    <td>
                      <select value={s.interval_seconds} onChange={(e) => act(() => api.patch(`/api/admin/jobs/schedules/${s.name}`, { interval_seconds: Number(e.target.value) }), "Planen er gemt.")}>
                        {[...new Set([300, 900, 3600, 6 * 3600, 86400, 7 * 86400, s.interval_seconds])].sort((a, b) => a - b).map((v) => <option key={v} value={v}>{every(v)}</option>)}
                      </select>
                    </td>
                    <td className="small">{s.enabled ? fmtDate(s.next_run_at) : "–"}</td>
                    <td className="small">{fmtDate(s.last_run_at)}{s.last_status && <> · <span className={`pill ${STATUS[s.last_status]?.[1] ?? ""}`}>{STATUS[s.last_status]?.[0] ?? s.last_status}</span></>}</td>
                    <td><input type="checkbox" aria-label={`${s.name} slået til`} checked={s.enabled} onChange={() => act(() => api.patch(`/api/admin/jobs/schedules/${s.name}`, { enabled: !s.enabled }), s.enabled ? "Planen er sat på pause." : "Planen er slået til.")} /></td>
                    <td><button className="btn btn-outline btn-sm" onClick={() => act(() => api.post(`/api/admin/jobs/schedules/${s.name}/run`), "Jobbet er sat i kø.")}>Kør nu</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <span className="eyebrow">Opbevaringsregler for driftsdata</span>
        <p className="small muted">Kører hver time som jobbet retention.cleanup. Loggene har deres egne perioder under <a href="/admin/retention">Sletning af logdata</a>.</p>
        {!policies.data ? <Loading /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Data</th><th>Gem i (dage)</th><th>Sidst</th><th>Til</th></tr></thead>
              <tbody>
                {policies.data.map((p) => (
                  <tr key={p.key}>
                    <td><strong>{p.title}</strong><div className="muted tiny">{p.description}</div></td>
                    <td><input type="number" min={p.min_days} max={3650} defaultValue={p.days} style={{ width: 90 }}
                               onBlur={(e) => Number(e.target.value) !== p.days && act(() => api.patch(`/api/admin/retention/policies/${p.key}`, { days: Number(e.target.value) }), "Reglen er gemt.")} /></td>
                    <td className="small">{fmtDate(p.last_run_at)}{p.last_deleted !== null && ` · ${fmtNumber(p.last_deleted)} slettet`}</td>
                    <td><input type="checkbox" aria-label={`${p.title} slået til`} checked={p.enabled} onChange={() => act(() => api.patch(`/api/admin/retention/policies/${p.key}`, { enabled: !p.enabled }), "Reglen er gemt.")} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div><button className="btn btn-outline" onClick={() => act(() => api.post("/api/admin/retention/policies/run"), "Oprydningen er kørt.")}>Ryd op nu</button></div>
      </div>

      <div className="card">
        <div className="spread">
          <span className="eyebrow">Køen</span>
          <div className="row">
            <select aria-label="Filtrér på status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Alle</option>
              {Object.entries(STATUS).map(([k, [label]]) => <option key={k} value={k}>{label} ({jobs.data?.counts[k] ?? 0})</option>)}
            </select>
            <button className="btn btn-ghost btn-sm" onClick={jobs.reload}>Opdatér</button>
          </div>
        </div>
        {!jobs.data ? <Loading /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Type</th><th>Status</th><th>Forsøg</th><th>Oprettet</th><th>Færdig / næste forsøg</th><th /></tr></thead>
              <tbody>
                {jobs.data.items.length === 0 && <tr><td colSpan={7} className="muted">Ingen jobs.</td></tr>}
                {jobs.data.items.map((j) => (
                  <Fragment key={j.id}>
                    <tr>
                      <td className="num">{j.id}</td>
                      <td className="mono small">{j.kind}{j.schedule && <div className="muted tiny">plan: {j.schedule}</div>}</td>
                      <td><span className={`pill ${STATUS[j.status]?.[1] ?? ""}`}>{STATUS[j.status]?.[0] ?? j.status}</span>{j.last_error && <div className="muted tiny" style={{ maxWidth: 280 }}>{j.last_error.slice(0, 140)}</div>}</td>
                      <td className="num">{j.attempts}/{j.max_attempts}</td>
                      <td className="small">{fmtDate(j.created_at)}</td>
                      <td className="small">{j.finished_at ? fmtDate(j.finished_at) : j.status === "queued" ? fmtDate(j.run_at) : "–"}</td>
                      <td>
                        <div className="row" style={{ "--gap": "6px", flexWrap: "nowrap" } as React.CSSProperties}>
                          <button className="btn btn-ghost btn-sm" onClick={() => setShown(shown === j.id ? null : j.id)}>Vis</button>
                          {(j.status === "dead" || j.status === "failed") && <button className="btn btn-outline btn-sm" onClick={() => act(() => api.post(`/api/admin/jobs/${j.id}/retry`), "Jobbet prøves igen.")}>Prøv igen</button>}
                          {(j.status === "queued" || j.status === "dead") && <button className="btn btn-danger btn-sm" onClick={() => act(() => api.del(`/api/admin/jobs/${j.id}`), "Jobbet er slettet.")}>Slet</button>}
                        </div>
                      </td>
                    </tr>
                    {shown === j.id && <tr><td colSpan={7}><div className="stack small"><span className="muted">Payload</span><Json value={j.payload} /><span className="muted">Resultat</span><Json value={j.result} /></div></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
