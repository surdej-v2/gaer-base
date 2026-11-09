import { useMemo, useState } from "react";
import { PROMPTS, type Prompt } from "../develop/prompts";

const ALL = "Alle";

/** Shows a prompt with its [FILL-IN FIELDS] highlighted. */
function PromptText({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]\n]+\])/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("[") && part.endsWith("]") ? <mark key={i} className="prompt-field">{part}</mark> : part,
      )}
    </>
  );
}

function PromptCard({ prompt }: { prompt: Prompt }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(prompt.prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <article className="card prompt-card" data-deeplink={`prompt-${prompt.id}`}>
      <div className="row" style={{ "--gap": "6px" } as React.CSSProperties}>
        <span className="pill blue">{prompt.category}</span>
        <span className="pill">{prompt.tool}</span>
      </div>
      <h3>{prompt.title}</h3>
      <p className="muted small">{prompt.summary}</p>
      <pre className={`prompt-text${open ? " open" : ""}`}>
        <PromptText text={prompt.prompt} />
      </pre>
      <div className="row" style={{ "--gap": "8px", marginTop: "auto" } as React.CSSProperties}>
        <button type="button" className="btn btn-primary btn-sm" onClick={copy} aria-live="polite">
          {copied ? "Kopieret" : "Kopiér prompt"}
        </button>
        <button type="button" className="btn btn-outline btn-sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "Vis mindre" : "Vis hele"}
        </button>
      </div>
    </article>
  );
}

export default function Develop() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState(ALL);

  const categories = useMemo(() => [ALL, ...new Set(PROMPTS.map((p) => p.category))], []);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return PROMPTS.filter((p) => {
      if (category !== ALL && p.category !== category) return false;
      if (!q) return true;
      return [p.title, p.summary, p.prompt, p.category, p.tool].join(" ").toLowerCase().includes(q);
    });
  }, [query, category]);

  return (
    <div className="page">
      <div className="stack" style={{ "--gap": "10px", maxWidth: 720 } as React.CSSProperties}>
        <div className="eyebrow">Udvikling</div>
        <h1>Promptkatalog</h1>
        <p className="muted">
          Prompts du kan kopiere ind i Claude, når du vil lave artifacts, planlægge en feature, skrive tests eller få et
          review. Udfyld felterne i [KANTEDE PARENTESER], før du sender.
        </p>
      </div>

      <div className="row" style={{ "--gap": "16px", alignItems: "flex-end" } as React.CSSProperties}>
        <label className="field" style={{ flex: "1 1 280px", maxWidth: 420 }}>
          Søg
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Fx migration, dashboard, review"
          />
        </label>
        <div className="row" role="group" aria-label="Kategori" style={{ "--gap": "8px" } as React.CSSProperties}>
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              className={`btn btn-sm ${c === category ? "btn-dark" : "btn-outline"}`}
              style={{ borderRadius: 9999 }}
              aria-pressed={c === category}
              onClick={() => setCategory(c)}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      <div className="muted small">{shown.length === 1 ? "1 prompt" : `${shown.length} prompts`}</div>

      {shown.length > 0 ? (
        <div className="grid" style={{ "--min": "330px" } as React.CSSProperties}>
          {shown.map((p) => <PromptCard key={p.id} prompt={p} />)}
        </div>
      ) : (
        <div className="card muted-bg" style={{ textAlign: "center" }}>
          <p className="muted">Ingen prompts matcher søgningen.</p>
        </div>
      )}

      <div className="alert info">
        Prompts mærket Claude Code bruger projektets slash-kommandoer (/gaer-new-feature, /gaer-db, /gaer-pr-review …)
        og virker i et repo, der er forket fra gaer-base.
      </div>
    </div>
  );
}
