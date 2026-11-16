// /developer — Oversigt (DS-1): the four studios as cards and what waits for the user. With AI (specs/developer/AI.md,
// fase 1): "Hvad vil du bygge?" sends a description to the right studio, "AI foreslår" (rules on the server, works without
// AI) and a short AI summary of each version waiting for the user.
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage } from "./DeveloperShell";
import { useAiContext, useDevAi } from "./ai/context";
import { AiSuggestions, ExplainButton } from "./ai/patterns";
import { ChangePill } from "./devui";
import Dashboard from "./Dashboard";

type Overview = {
  studios: { key: string; title: string; text: string; path: string; count: number; review: number }[];
  counts: { approvals: number; reviews: number; unread: number };
  datasets: number; connections: number;
  waiting: { version_id: number; artifact_id: number; name: string; typeLabel: string; version: string; change_type: string; role: string;
             changes: string[]; ai_summary: string | null }[];
  reviews: { id: number; artifact_id: number; name: string; cause: string }[];
  notifications: { id: number; title: string; body: string; link: string; created_at: string; read_at: string | null }[];
};

export default function Overview() {
  const { data, error } = useLoad(() => api.get<Overview>("/api/admin/dev/overview"), []);
  const ai = useDevAi();
  useAiContext("overview");
  return (
    <DevPage title="Developer" intro={ai.enabled ? "Vælg et studie, eller beskriv hvad du vil bygge" : "Vælg et studie for at komme i gang"}>
      {error && <Alert>{error}</Alert>}
      <BuildBox />
      <AiSuggestions scope="overview" />
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
              <div key={w.version_id} className="stack" style={{ "--gap": "2px" } as React.CSSProperties}>
                <Link to={`/developer/godkendelser/${w.version_id}`} className="row small" style={{ gap: 8 }}>
                  <ChangePill type={w.change_type} /> {w.name} {w.version} <span className="tiny muted">{w.typeLabel} · du er {w.role}</span>
                </Link>
                {w.ai_summary ? <span className="dev-ai-summary"><b>AI-resumé:</b> {w.ai_summary}</span>
                  : w.changes.length > 0 && <span className="dev-ai-summary">{w.changes.join(" · ")}</span>}
              </div>
            ))}
            {data.reviews.map((r) => (
              <Link key={r.id} to={`/developer/artefakter/${r.artifact_id}`} className="small">Review: {r.name} (fordi {r.cause} fik en major)</Link>
            ))}
          </div>
          <div className="dev-card">
            <strong>Beskeder</strong>
            {data.notifications.length === 0 && <span className="muted small">Ingen beskeder.</span>}
            {data.notifications.map((n) => {
              const run = /^\/developer\/pipeline\/koersler\/(\d+)/.exec(n.link ?? "")?.[1];
              return (
                <div key={n.id} className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Link to={n.link || "/developer"} className="small" style={{ fontWeight: n.read_at ? 400 : 600 }}>
                    {n.title} <span className="tiny muted">{fmtDate(n.created_at)}</span>
                  </Link>
                  {run && <ExplainButton question={`Forklar: ${n.title}`} about={{ kind: "run", id: Number(run) }}>Forklar</ExplainButton>}
                </div>
              );
            })}
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

/** "Hvad vil du bygge?": the server picks the studio (keywords, refined by AI when it is on) and the page that starts a
 *  draft gets the description. Hidden when AI is off — the studio cards do the same job then. */
function BuildBox() {
  const ai = useDevAi();
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!ai.enabled) return null;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (text.trim().length < 3) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ path: string }>("/api/admin/dev/ai/route", { text: text.trim() });
      navigate(r.path);
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="dev-ai-build" onSubmit={submit}>
      <Sparkles size={18} aria-hidden className="dev-ai-icon" />
      <label htmlFor="dev-ai-build" className="sr-only">Hvad vil du bygge?</label>
      <input id="dev-ai-build" value={text} onChange={(e) => setText(e.target.value)} maxLength={2000}
             placeholder="Hvad vil du bygge? Fx: et MCP-endpoint, der slår årlig leje op pr. ejendom" />
      <span className="tiny muted">AI vælger studie og laver en kladde</span>
      <button className="btn btn-primary btn-sm" disabled={busy || text.trim().length < 3}>{busy ? "Finder studie …" : "Lav kladde"}</button>
      {err && <span className="small" role="alert" style={{ color: "#b91c1c", flexBasis: "100%" }}>{err}</span>}
    </form>
  );
}
