// The AI panel beside every page in /developer (⌘J). It shows what the AI knows ("Kender:", built by the server from
// the page's {kind, id}), the conversation, and proposals the AI made that no open editor took. On a phone
// (specs/developer/AI.md fase 6) it is a sheet from the bottom: half or full height, the handle toggles and can be
// dragged (up → full, down → half, further down → closed), a scrim behind it closes it, and a floating "Spørg AI"
// opens it.
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronUp, Copy, Sparkles, X } from "lucide-react";
import { api } from "../../api";
import { Chat } from "../../ai";
import { useDevAi, type Proposal } from "./context";
import { proposalBody, proposalTitle, runDevTool } from "./tools";

type Known = { label: string; chips: string[] };
type Sheet = "half" | "full";

const PHONE = 768;

function usePhone(): boolean {
  const [phone, setPhone] = useState(() => window.innerWidth < PHONE);
  useEffect(() => {
    const on = () => setPhone(window.innerWidth < PHONE);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return phone;
}

/** Where a drag of the handle ends: the sheet's height as a share of the screen decides. */
export function snap(heightPx: number, viewportPx: number): Sheet | "closed" {
  const share = heightPx / Math.max(1, viewportPx);
  return share > 0.72 ? "full" : share < 0.28 ? "closed" : "half";
}

export function AiDock() {
  const ai = useDevAi();
  const [known, setKnown] = useState<Known | null>(null);
  const [session, setSession] = useState<{ key: number; question?: string }>({ key: 0 });
  const phone = usePhone();
  const [sheet, setSheet] = useState<Sheet>("half");
  const [dragH, setDragH] = useState<number | null>(null);
  const drag = useRef<{ startY: number; startH: number } | null>(null);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => { if (!ai.open) setSheet("half"); }, [ai.open]);

  const onHandleDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (!panel.current) return;
    drag.current = { startY: e.clientY, startH: panel.current.getBoundingClientRect().height };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onHandleMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (!drag.current) return;
    const h = drag.current.startH + (drag.current.startY - e.clientY);
    setDragH(Math.max(80, Math.min(window.innerHeight - 12, h)));
  };
  const onHandleUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    const moved = Math.abs(e.clientY - d.startY);
    setDragH(null);
    if (moved < 8) {                      // a tap: toggle half ↔ full
      setSheet((x) => (x === "full" ? "half" : "full"));
      return;
    }
    const to = snap(d.startH + (d.startY - e.clientY), window.innerHeight);
    if (to === "closed") ai.setOpen(false);
    else setSheet(to);
  };

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

  if (!ai.enabled) return null;
  if (!ai.open) {
    return phone ? (
      <button type="button" className="dev-ai-fab" onClick={() => ai.setOpen(true)} aria-label="Spørg AI">
        <Sparkles size={18} aria-hidden /> Spørg AI
      </button>
    ) : null;
  }
  const full = sheet === "full";
  return (
    <>
    {phone && <button type="button" className="dev-ai-scrim" aria-label="Luk AI-panelet" onClick={() => ai.setOpen(false)} />}
    <aside ref={panel} className={`dev-ai-dock${phone ? ` ai-sheet ${sheet}` : ""}`} aria-label="AI-assistent"
           role={phone ? "dialog" : undefined} aria-modal={phone ? true : undefined}
           style={phone && dragH !== null ? { height: dragH, transition: "none" } : undefined}>
      {phone && (
        <button type="button" className="dev-ai-handle" onPointerDown={onHandleDown} onPointerMove={onHandleMove} onPointerUp={onHandleUp}
                onPointerCancel={() => { drag.current = null; setDragH(null); }}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSheet(full ? "half" : "full"); } }}
                aria-label={full ? "Gør panelet lavere" : "Udvid panelet"} aria-expanded={full}>
          <span aria-hidden />
        </button>
      )}
      <div className="dev-ai-head">
        <Sparkles size={16} aria-hidden className="dev-ai-icon" />
        <strong>AI-assistent</strong>
        {phone && (
          <button type="button" className="dev-icon-btn" onClick={() => setSheet(full ? "half" : "full")}
                  aria-label={full ? "Gør panelet lavere" : "Udvid panelet"}>
            {full ? <ChevronDown size={18} /> : <ChevronUp size={18} />}
          </button>
        )}
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
    </>
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
