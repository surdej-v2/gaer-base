// The AI patterns of /developer (canvas "AI-mønstre", specs/developer/AI.md): the "Spørg AI" button, suggestion cards,
// the "Forklar"-button. Blue and the sparkle always mean: this comes from AI and nothing is saved yet.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { api, errorText } from "../../api";
import { useDevAi, type AiRef } from "./context";

/** "Spørg AI" in the top bar; hidden when AI is off. */
export function AskAiButton() {
  const ai = useDevAi();
  if (!ai.enabled) return null;
  return (
    <button type="button" className={`dev-ai-btn${ai.open ? " on" : ""}`} onClick={ai.toggle} aria-pressed={ai.open}
            aria-label="Spørg AI (⌘J)" title="Spørg AI (⌘J)">
      <Sparkles size={14} aria-hidden />
      <span className="dev-ai-btn-text">Spørg AI</span>
      <kbd>⌘J</kbd>
    </button>
  );
}

/** A small button that asks the AI to explain something (a failed run, a stage, a change). Hidden when AI is off. */
export function ExplainButton({ question, about, children = "Forklar" }: { question: string; about?: AiRef; children?: string }) {
  const ai = useDevAi();
  if (!ai.enabled) return null;
  return (
    <button type="button" className="dev-ai-chipbtn" onClick={() => ai.ask(question, about)}>{children}</button>
  );
}

type Suggestion = {
  key: string; kind: string; where: string; title: string; body: string;
  action: { label: string; path: string }; ref: AiRef | null;
};

/** "AI foreslår": suggestions found by rules on the server (they work without AI); the AI explains on request. */
export function AiSuggestions({ scope = "overview", max = 3 }: { scope?: string; max?: number }) {
  const ai = useDevAi();
  const [items, setItems] = useState<Suggestion[] | null>(null);
  const [undo, setUndo] = useState<Suggestion | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<{ items: Suggestion[] }>(`/api/admin/dev/ai/suggestions?scope=${encodeURIComponent(scope)}`)
    .then((r) => setItems(r.items), () => setItems([]));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [scope]);

  const dismiss = async (s: Suggestion) => {
    setErr(null);
    try {
      await api.post("/api/admin/dev/ai/suggestions/dismiss", { key: s.key });
      setItems((xs) => (xs ?? []).filter((x) => x.key !== s.key));
      setUndo(s);
    } catch (e) {
      setErr(errorText(e));
    }
  };
  const restore = async () => {
    if (!undo) return;
    try {
      await api.del(`/api/admin/dev/ai/suggestions/dismiss/${encodeURIComponent(undo.key)}`);
      setUndo(null);
      load();
    } catch (e) {
      setErr(errorText(e));
    }
  };

  if (!items || (items.length === 0 && !undo)) return null;
  return (
    <section className="dev-ai-suggestions" aria-labelledby="dev-ai-sugg">
      <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
        <h2 id="dev-ai-sugg" className="dev-ai-title">{ai.enabled ? "AI foreslår" : "Forslag"}</h2>
        <span className="tiny muted">ud fra kørsler, kataloget og testbænken</span>
      </div>
      <div className="dev-ai-grid">
        {items.slice(0, max).map((s) => (
          <div key={s.key} className="dev-ai-card">
            <span className="dev-ai-where"><Sparkles size={13} aria-hidden /> {s.where}</span>
            <strong>{s.title}</strong>
            <span className="small" style={{ color: "#475569" }}>{s.body}</span>
            <div className="row" style={{ gap: 6, marginTop: 2 }}>
              <Link to={s.action.path} className="btn btn-primary btn-sm">{s.action.label}</Link>
              {ai.enabled && (
                <button type="button" className="btn btn-outline btn-sm"
                        onClick={() => ai.ask(`${s.title}. Hvad betyder det, og hvad bør jeg gøre?`, s.ref ?? undefined)}>
                  Spørg AI
                </button>
              )}
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => dismiss(s)}>Afvis</button>
            </div>
          </div>
        ))}
      </div>
      {undo && (
        <p className="small muted" role="status" style={{ margin: 0 }}>
          „{undo.title}“ er afvist og kommer ikke igen, før der er noget nyt.{" "}
          <button type="button" className="btn btn-ghost btn-sm" onClick={restore}>Fortryd</button>
        </p>
      )}
      {err && <p className="small" role="alert" style={{ color: "#b91c1c", margin: 0 }}>{err}</p>}
    </section>
  );
}
