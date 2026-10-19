// Connection to the Happy AI extension, as useBridge in surdej-v1: PING with backoff (1, 2, 3, 5 s).
// Without a handshake the install guide opens once per browser (localStorage tg-ext-guide-seen).
import { useSyncExternalStore } from "react";
import { happyAi } from "../../bridge/extension";

/** Oldest Happy AI build with the host-page bridge /admin uses (OPEN_SIDE_PANEL, instances). */
export const REQUIRED_VERSION = "0.12.0";
const RETRY_DELAYS = [1000, 2000, 3000, 5000]; // four attempts
const GUIDE_SEEN = "tg-ext-guide-seen";

export type Handshake = "idle" | "checking" | "none";
export type BridgeLine = { ok: boolean; verb: string; detail: string };

type State = {
  status: "unknown" | "connected" | "outdated" | "missing";
  version: string | null;
  handshake: Handshake;
  attempt: number;
  popoverOpen: boolean;
  guideOpen: boolean;
  log: BridgeLine[];
};

let state: State = { status: "unknown", version: null, handshake: "idle", attempt: 0, popoverOpen: false, guideOpen: false, log: [] };
const listeners = new Set<() => void>();
function set(patch: Partial<State> | ((s: State) => Partial<State>)) {
  state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
  listeners.forEach((l) => l());
}

export function newer(a: string, b: string): boolean {
  const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) > (pb[i] ?? 0);
  return false;
}

export function logLine(ok: boolean, verb: string, detail: string) {
  set((s) => ({ log: [{ ok, verb, detail }, ...s.log].slice(0, 5) }));
}

/** Runs a bridge call and writes it to the bridge log. */
export async function bridge<T>(verb: string, call: () => Promise<T>, detail: (r: T) => string = () => "ok"): Promise<T> {
  const t0 = performance.now();
  try {
    const r = await call();
    logLine(true, verb, detail(r) || `${Math.round(performance.now() - t0)} ms`);
    return r;
  } catch (e) {
    logLine(false, verb, e instanceof Error ? e.message : "fejl");
    throw e;
  }
}

let timer = 0;
let run = 0;

/** PING with backoff. `auto`: the first check on load, which may open the guide once per browser. */
export function handshake(auto: boolean) {
  window.clearTimeout(timer);
  const mine = ++run;
  const attempt = async (i: number) => {
    if (mine !== run) return;
    set({ handshake: "checking", attempt: i + 1 });
    const t0 = performance.now();
    try {
      const pong = await happyAi.ping(1000);
      if (mine !== run) return;
      const version = pong?.version ?? null;
      logLine(true, "PING", `${Math.round(performance.now() - t0)} ms`);
      const outdated = !version || newer(REQUIRED_VERSION, version);
      set({ status: outdated ? "outdated" : "connected", version, handshake: "idle", guideOpen: false });
    } catch {
      if (mine !== run) return;
      if (i + 1 < RETRY_DELAYS.length) {
        timer = window.setTimeout(() => attempt(i + 1), RETRY_DELAYS[i]);
        return;
      }
      let seen = false;
      try {
        seen = localStorage.getItem(GUIDE_SEEN) === "1";
      } catch {
        /* storage blocked */
      }
      set((s) => ({ status: "missing", version: null, handshake: "none", guideOpen: auto ? !seen : s.guideOpen }));
    }
  };
  void attempt(0);
}

export const happyAiUi = {
  togglePopover: () => {
    if (state.status === "missing") {
      set({ guideOpen: true, popoverOpen: false });
      if (state.handshake !== "checking") handshake(false);
    } else {
      set((s) => ({ popoverOpen: !s.popoverOpen }));
    }
  },
  closePopover: () => set({ popoverOpen: false }),
  openGuide: () => set({ guideOpen: true, popoverOpen: false }),
  closeGuide: () => {
    try {
      localStorage.setItem(GUIDE_SEEN, "1");
    } catch {
      /* storage blocked */
    }
    set({ guideOpen: false });
  },
};

export function useHappyAi(): State {
  return useSyncExternalStore((l) => {
    listeners.add(l);
    return () => void listeners.delete(l);
  }, () => state);
}
