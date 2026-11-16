// The AI panel beside every page in /developer (⌘J). It shows what the AI knows ("Kender:", built by the server from
// the page's {kind, id}), the conversation, and proposals the AI made that no open editor took. On a phone it is a
// sheet from the bottom.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Copy, Sparkles, X } from "lucide-react";
import { api } from "../../api";
import { Chat } from "../../ai";
import { useDevAi, type Proposal } from "./context";
import { proposalBody, proposalTitle, runDevTool } from "./tools";

type Known = { label: string; chips: string[] };

export function AiDock() {
  const ai = useDevAi();
  const [known, setKnown] = useState<Known | null>(null);
  const [session, setSession] = useState<{ key: number; question?: string }>({ key: 0 });

  const refKey = `${ai.ref.kind}:${ai.ref.id ?? ""}`;
  useEffect(() => {
    if (!ai.enabled || !ai.open) return;
    const q = ai.ref.id ? `kind=${ai.ref.kind}&id=${ai.ref.id}` : `kind=${ai.ref.kind}`;
    api.get<Known>(`/api/admin/dev/ai/context?${q}`).then(setKnown, () => setKnown(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refKey, ai.enabled, ai.open]);

  // A question from elsewhere ("Spørg AI" on a suggestion, ⌘K, "Forklar fejlen") starts a new conversation with it.
  useEffect(() => {
    if (ai.pending) setSession((s) => ({ key: s.key + 1, question: ai.pending!.question }));
  }, [ai.pending]);

  useEffect(() => {
    if (!ai.open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector(".dev-palette")) ai.setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ai]);

  if (!ai.enabled || !ai.open) return null;
  return (
    <aside className="dev-ai-dock" aria-label="AI-assistent">
      <div className="dev-ai-head">
        <Sparkles size={16} aria-hidden className="dev-ai-icon" />
        <strong>AI-assistent</strong>
        <button type="button" className="dev-icon-btn" onClick={() => setSession((s) => ({ key: s.key + 1 }))}
                aria-label="Ny samtale" title="Ny samtale">
          <span aria-hidden style={{ fontSize: 18, lineHeight: 1 }}>+</span>
        </button>
        <button type="button" className="dev-icon-btn" onClick={() => ai.setOpen(false)} aria-label="Luk AI-panelet (Esc)" title="Luk (Esc)">
          <X size={16} />
        </button>
      </div>
      {known && (
        <div className="dev-ai-known" aria-label="Det AI kender">
          <span className="tiny muted">Kender:</span>
          {known.chips.map((c) => <span key={c} className="dev-ai-chip">{c}</span>)}
        </div>
      )}
      {ai.proposals.length > 0 && (
        <div className="dev-ai-proposals">
          {ai.proposals.map((p) => <ProposalCard key={p.id} p={p} onDrop={() => ai.dropProposal(p.id)} />)}
        </div>
      )}
      <div className="dev-ai-chat">
        {ai.blocked ? (
          <div className="dev-ai-budget" role="status">
            Du har brugt dit AI-budget. <a href="/konto/forbrug">Se dit forbrug</a>
          </div>
        ) : (
          <Chat key={session.key} surface="developer" endpoint="/api/admin/dev/ai/chat"
                extra={{ ref: ai.ref.id ? { kind: ai.ref.kind, id: ai.ref.id } : { kind: ai.ref.kind } }}
                runTool={(name, args) => runDevTool(name, args, ai.propose)}
                initialDraft={session.question} autoSend={!!session.question}
                contextHint="Spørg om siden, data eller kode. AI ser kun metadata og gemmer aldrig selv."
                placeholder="Spørg om siden, data eller kode …" />
        )}
      </div>
      <p className="dev-ai-foot tiny muted">AI ser kun det, du selv har adgang til, og aldrig værdier. Forbruget tæller i dit AI-budget.</p>
    </aside>
  );
}

function ProposalCard({ p, onDrop }: { p: Proposal; onDrop: () => void }) {
  const { title, path } = proposalTitle(p.tool, p.args);
  const body = proposalBody(p.tool, p.args);
  const [copied, setCopied] = useState(false);
  const copy = () => navigator.clipboard?.writeText(body).then(() => setCopied(true), () => {});
  return (
    <div className="dev-ai-card">
      <span className="dev-ai-where"><Sparkles size={13} aria-hidden /> Forslag fra AI · ikke gemt</span>
      <strong className="small">{title}</strong>
      {typeof p.args.summary === "string" && <span className="small">{p.args.summary}</span>}
      <pre className="dev-code" style={{ maxHeight: 220 }}>{body}</pre>
      <div className="row" style={{ gap: 6 }}>
        {path && <Link to={path} className="btn btn-primary btn-sm">Åbn editoren</Link>}
        <button type="button" className="btn btn-outline btn-sm" onClick={copy}><Copy size={13} aria-hidden /> {copied ? "Kopieret" : "Kopiér"}</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDrop}>Afvis</button>
      </div>
    </div>
  );
}
