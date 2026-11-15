// /developer — Oversigt (DS-1): the four studios as cards and what waits for the user.
import { Link } from "react-router-dom";
import { api } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage } from "./DeveloperShell";
import { ChangePill } from "./devui";
import Dashboard from "./Dashboard";

type Overview = {
  studios: { key: string; title: string; text: string; path: string; count: number; review: number }[];
  counts: { approvals: number; reviews: number; unread: number };
  datasets: number; connections: number;
  waiting: { version_id: number; artifact_id: number; name: string; typeLabel: string; version: string; change_type: string; role: string }[];
  reviews: { id: number; artifact_id: number; name: string; cause: string }[];
  notifications: { id: number; title: string; body: string; link: string; created_at: string; read_at: string | null }[];
};

export default function Overview() {
  const { data, error } = useLoad(() => api.get<Overview>("/api/admin/dev/overview"), []);
  return (
    <DevPage title="Developer" intro="Vælg et studie for at komme i gang">
      {error && <Alert>{error}</Alert>}
      <div className="dev-cards">
        {(data?.studios ?? []).map((s) => (
          <div key={s.key} className="dev-card">
            <strong>{s.title}</strong>
            <span className="small muted">{s.text}</span>
            <span className="tiny muted">{s.count} artefakter{s.review ? ` · ${s.review} kræver review` : ""}</span>
            <Link to={s.path} className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} data-deeplink={`developer-${s.key}`}>Åbn studie</Link>
          </div>
        ))}
      </div>
      {data && (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 }}>
          <div className="dev-card">
            <strong>Venter på dig</strong>
            {data.waiting.length === 0 && data.reviews.length === 0 && <span className="muted small">Intet venter på dig.</span>}
            {data.waiting.map((w) => (
              <Link key={w.version_id} to={`/developer/godkendelser/${w.version_id}`} className="row small" style={{ gap: 8 }}>
                <ChangePill type={w.change_type} /> {w.name} {w.version} <span className="tiny muted">{w.typeLabel} · du er {w.role}</span>
              </Link>
            ))}
            {data.reviews.map((r) => (
              <Link key={r.id} to={`/developer/artefakter/${r.artifact_id}`} className="small">Review: {r.name} (fordi {r.cause} fik en major)</Link>
            ))}
          </div>
          <div className="dev-card">
            <strong>Beskeder</strong>
            {data.notifications.length === 0 && <span className="muted small">Ingen beskeder.</span>}
            {data.notifications.map((n) => (
              <Link key={n.id} to={n.link || "/developer"} className="small" style={{ fontWeight: n.read_at ? 400 : 600 }}>
                {n.title} <span className="tiny muted">{fmtDate(n.created_at)}</span>
              </Link>
            ))}
          </div>
        </div>
      )}
      <details>
        <summary className="small muted" style={{ cursor: "pointer" }}>Instansen i tal: API-kilder, katalog, MCP-endpoints og værktøjer</summary>
        <div style={{ marginTop: 12 }}><Dashboard /></div>
      </details>
    </DevPage>
  );
}
