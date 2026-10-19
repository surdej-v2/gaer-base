// API tracer state, after happy-groups core/tracer/tracerStore.ts. Records every call made through
// api.ts while /admin is open (setApiObserver), keeps the latest 80 in memory and nothing on the server.
import { useSyncExternalStore } from "react";
import { setApiObserver, type ApiCall } from "../../api";

export type TracedRequest = {
  id: number;
  method: string;
  path: string;
  status: number | null;
  pending: boolean;
  requestBody?: unknown;
  responseBody?: unknown;
  ts: number;
  ms: number;
  page: string;
};

type State = {
  requests: TracedRequest[];
  recording: boolean;
  open: boolean;
  hidden: boolean;
};

const HIDDEN_KEY = "tg-tracer-hidden";
const MAX = 80;

let state: State = {
  requests: [],
  recording: true,
  open: false,
  hidden: (() => {
    try {
      return sessionStorage.getItem(HIDDEN_KEY) === "1";
    } catch {
      return false;
    }
  })(),
};
const listeners = new Set<() => void>();

function set(patch: Partial<State> | ((s: State) => Partial<State>)) {
  state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
  listeners.forEach((l) => l());
}

const page = () => window.location.pathname + window.location.search;

export function installTracer(): void {
  setApiObserver({
    start(call: ApiCall) {
      if (!state.recording) return;
      const req: TracedRequest = { id: call.id, method: call.method, path: call.path, status: null, pending: true,
        requestBody: call.requestBody, ts: call.startedAt, ms: 0, page: page() };
      set((s) => ({ requests: [req, ...s.requests].slice(0, MAX) }));
    },
    end(call: ApiCall) {
      set((s) => ({
        requests: s.requests.map((r) => (r.id === call.id ? { ...r, pending: false, status: call.status, responseBody: call.responseBody, ms: call.ms } : r)),
      }));
    },
  });
}

export const tracer = {
  setOpen: (open: boolean) => set({ open }),
  toggleOpen: () => set((s) => ({ open: !s.open })),
  setRecording: (recording: boolean) => set({ recording }),
  clear: () => set({ requests: [] }),
  /** Hide the badge until reload, or for the rest of the browser session. */
  hide: (forSession: boolean) => {
    if (forSession) {
      try {
        sessionStorage.setItem(HIDDEN_KEY, "1");
      } catch {
        /* storage blocked */
      }
    }
    set({ hidden: true, open: false });
  },
  show: () => {
    try {
      sessionStorage.removeItem(HIDDEN_KEY);
    } catch {
      /* storage blocked */
    }
    set({ hidden: false, open: true });
  },
};

export function useTracer(): State {
  return useSyncExternalStore((l) => {
    listeners.add(l);
    return () => void listeners.delete(l);
  }, () => state);
}

export function toYaml(reqs: TracedRequest[]): string {
  return reqs.map((r) => `- method: ${r.method}\n  path: ${r.path}\n  status: ${r.status}\n  duration_ms: ${r.ms}\n  timestamp: ${new Date(r.ts).toISOString()}`
    + (r.requestBody !== undefined ? `\n  request:\n    body: ${JSON.stringify(r.requestBody)}` : "")
    + `\n  response:\n    body: ${JSON.stringify(r.responseBody ?? null)}`).join("\n");
}
