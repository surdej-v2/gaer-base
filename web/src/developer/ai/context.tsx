// AI in Gær Developer (specs/developer/AI.md, fase 1): one panel for the whole shell. Pages say what the user is
// looking at with useAiContext({kind, id}); the server builds the context from that (only metadata), so nothing the
// page shows is sent from here. Everything hides itself when /api/ai/status says AI is off.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { api } from "../../api";

export type AiKind = "overview" | "artifact" | "dataset" | "run";
export type AiRef = { kind: AiKind; id?: number };
export type Proposal = { id: number; tool: string; args: Record<string, unknown> };
/** A page's handler for a kind of proposal; null = not for this page (it goes to the panel instead). */
type Handler = (args: Record<string, unknown>) => string | null | Promise<string | null>;
type Status = { enabled: boolean; blocked?: boolean; status?: string };

type Ctx = {
  /** AI is on for this user (configured, not closed, has a limit). */
  enabled: boolean;
  /** The user's limit or budget is spent: the panel shows a note instead of the input. */
  blocked: boolean;
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  ref: AiRef;
  setRef: (ref: AiRef | null) => void;
  /** Open the panel and send a question, optionally about something else than the page (a run, a dataset). */
  ask: (question: string, ref?: AiRef) => void;
  /** A question waiting to be sent by the panel (n changes for every ask, so the same question can be asked twice). */
  pending: { question: string; n: number } | null;
  proposals: Proposal[];
  dropProposal: (id: number) => void;
  /** Pages with an editor take proposals of a kind (propose_pipeline_change …) directly. */
  handlers: MutableRefObject<Record<string, Handler>>;
  propose: (tool: string, args: Record<string, unknown>) => Promise<string>;
  reloadStatus: () => void;
};

const OVERVIEW: AiRef = { kind: "overview" };
const AiContext = createContext<Ctx | null>(null);

export function AiProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [open, setOpen] = useState(false);
  const [ref, setRefState] = useState<AiRef>(OVERVIEW);
  const [pending, setPending] = useState<Ctx["pending"]>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const handlers = useRef<Record<string, Handler>>({});
  const seq = useRef(0);

  const reloadStatus = useCallback(() => {
    api.get<Status>("/api/ai/status").then(setStatus, () => setStatus({ enabled: false }));
  }, []);
  useEffect(reloadStatus, [reloadStatus]);

  const setRef = useCallback((r: AiRef | null) => setRefState(r ?? OVERVIEW), []);
  const ask = useCallback((question: string, about?: AiRef) => {
    if (about) setRefState(about);
    seq.current += 1;
    setPending({ question, n: seq.current });
    setOpen(true);
  }, []);
  const propose = useCallback(async (tool: string, args: Record<string, unknown>) => {
    const handler = handlers.current[tool];
    const taken = handler ? await handler(args) : null;
    if (taken !== null) return taken;
    seq.current += 1;
    const id = seq.current;
    setProposals((p) => [...p, { id, tool, args }]);
    setOpen(true);
    return "Forslaget vises i AI-panelet. Brugeren gemmer det selv; intet er gemt endnu.";
  }, []);

  const value = useMemo<Ctx>(() => ({
    enabled: !!status?.enabled, blocked: !!status?.blocked, open, setOpen, toggle: () => setOpen((o) => !o), ref, setRef, ask,
    pending, proposals, dropProposal: (id) => setProposals((p) => p.filter((x) => x.id !== id)), handlers, propose, reloadStatus,
  }), [status, open, ref, setRef, ask, pending, proposals, propose, reloadStatus]);
  return <AiContext.Provider value={value}>{children}</AiContext.Provider>;
}

const OFF: Ctx = {
  enabled: false, blocked: false, open: false, setOpen: () => {}, toggle: () => {}, ref: OVERVIEW, setRef: () => {},
  ask: () => {}, pending: null, proposals: [], dropProposal: () => {}, handlers: { current: {} },
  propose: async () => "", reloadStatus: () => {},
};

export function useDevAi(): Ctx {
  return useContext(AiContext) ?? OFF;
}

/** A page tells the panel what it shows; it falls back to the overview when the page goes. */
export function useAiContext(kind: AiKind, id?: number | string | null) {
  const { setRef } = useDevAi();
  const n = id === undefined || id === null ? undefined : Number(id);
  useEffect(() => {
    if (kind !== "overview" && !(n && Number.isFinite(n))) return;
    setRef({ kind, id: n });
    return () => setRef(null);
  }, [kind, n, setRef]);
}

/** A page with an editor takes a kind of proposal itself (it shows the change and the user saves). */
export function useAiProposalHandler(tool: string, handler: Handler) {
  const { handlers } = useDevAi();
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    handlers.current[tool] = (args) => latest.current(args);
    return () => { delete handlers.current[tool]; };
  }, [handlers, tool]);
}
