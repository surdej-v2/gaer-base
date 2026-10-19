import { useEffect, useState } from "react";
import { api, errorText } from "../../api";
import { Alert, fmtBytes, fmtDate, fmtDay, fmtNumber, Loading, PageHeader, useLoad } from "../../ui";

type Data = {
  auth_log_days: number; audit_log_days: number; analytics_days: number; last_run: string | null;
  logs: { log: string; rows: number; oldest: string | null; bytes: number }[];
};

const NAMES: Record<string, string> = { auth_log: "Auth-log", audit_log: "Audit-log", analytics: "Analytics og heatmaps" };

export default function Retention() {
  const data = useLoad(() => api.get<Data>("/api/admin/retention"));
  const [days, setDays] = useState({ auth_log_days: 365, audit_log_days: 1825, analytics_days: 395 });
  const [userId, setUserId] = useState("");
  const [logs, setLogs] = useState<string[]>(["auth_log", "audit_log", "analytics"]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (data.data) setDays({ auth_log_days: data.data.auth_log_days, audit_log_days: data.data.audit_log_days, analytics_days: data.data.analytics_days });
  }, [data.data]);

  const act = async (fn: () => Promise<{ deleted?: Record<string, number> } | unknown>, label: string, confirmText?: string) => {
    if (confirmText && !confirm(confirmText)) return;
    setError(null);
    setNotice(null);
    try {
      const res = (await fn()) as { deleted?: Record<string, number> };
      const counts = res?.deleted ? " " + Object.entries(res.deleted).map(([k, v]) => `${NAMES[k] ?? k}: ${fmtNumber(v)}`).join(" · ") : "";
      setNotice(label + counts);
      await data.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <PageHeader eyebrow="/admin/retention" title="Sletning af logdata">
        Loggene kan kun tilføjes til. Sletning sker herfra med en separat databasebruger, og hver sletning bliver selv
        skrevet i audit-loggen som log.purged. Gamle hændelser slettes automatisk én gang i døgnet.
      </PageHeader>
      <Alert kind="success">{notice}</Alert>
      <Alert>{error || data.error}</Alert>
      {!data.data ? <Loading /> : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Log</th><th>Rækker</th><th>Ældste hændelse</th><th>Størrelse</th></tr></thead>
              <tbody>
                {data.data.logs.map((l) => (
                  <tr key={l.log}><td><strong>{NAMES[l.log]}</strong></td><td className="num">{fmtNumber(l.rows)}</td><td>{fmtDate(l.oldest)}</td><td className="num">{fmtBytes(l.bytes)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <span className="eyebrow">Opbevaringsperiode</span>
            <div className="filters">
              <label className="field">Auth-log (dage)<input type="number" min={30} value={days.auth_log_days} onChange={(e) => setDays({ ...days, auth_log_days: Number(e.target.value) })} /></label>
              <label className="field">Audit-log (dage)<input type="number" min={30} value={days.audit_log_days} onChange={(e) => setDays({ ...days, audit_log_days: Number(e.target.value) })} /></label>
              <label className="field">Analytics (dage)<input type="number" min={7} value={days.analytics_days} onChange={(e) => setDays({ ...days, analytics_days: Number(e.target.value) })} /></label>
            </div>
            <p className="muted tiny">Sidst kørt automatisk: {fmtDay(data.data.last_run)}. log.purged-hændelser slettes aldrig.</p>
            <div className="row">
              <button className="btn btn-primary" onClick={() => act(() => api.put("/api/admin/retention", days), "Opbevaringsperioden er gemt.")}>Gem</button>
              <button className="btn btn-outline" onClick={() => act(() => api.post("/api/admin/retention/run"), "Gamle hændelser er slettet.",
                "Slet alle hændelser, der er ældre end opbevaringsperioden, nu?")}>Slet gamle hændelser nu</button>
            </div>
          </div>

          <div className="card">
            <span className="eyebrow">Manuel sletning for én bruger</span>
            <p className="small muted">Sletter logdata om brugeren. Brugeren selv slettes ikke — brug en GDPR-sletning til det.</p>
            <div className="row">
              <input placeholder="Bruger-id" inputMode="numeric" value={userId} onChange={(e) => setUserId(e.target.value.replace(/\D/g, ""))} style={{ width: 140 }} />
              {Object.entries(NAMES).map(([k, v]) => (
                <label key={k} className="row small" style={{ "--gap": "6px" } as React.CSSProperties}>
                  <input type="checkbox" checked={logs.includes(k)} onChange={(e) => setLogs(e.target.checked ? [...logs, k] : logs.filter((x) => x !== k))} />{v}
                </label>
              ))}
            </div>
            <div>
              <button className="btn btn-danger" disabled={!userId || !logs.length} onClick={() => act(
                () => api.post(`/api/admin/retention/users/${userId}`, { logs }), "Logdata for brugeren er slettet.",
                `Slet ${logs.map((l) => NAMES[l]).join(", ")} for bruger #${userId}? Det kan ikke fortrydes.`)}>Slet logdata</button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
