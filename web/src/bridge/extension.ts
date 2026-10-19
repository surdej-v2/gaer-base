// Client side of the browser extension bridge (see extension/sidepanel.js).
// The app runs in an iframe inside the side panel and asks the panel for page context.

const NS = "gaer-bridge";
const TIMEOUT_MS = 10_000;

export type PageInfo = { url: string; title: string; readable: boolean };
export type PageSnapshot = { url: string; title: string; description: string; headings: string[]; selection: string; text: string };

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: number };
const pending = new Map<string, Pending>();
const tabListeners = new Set<(info: PageInfo) => void>();

window.addEventListener("message", (event) => {
  const msg = event.data;
  if (event.source !== window.parent || !msg || msg.ns !== NS || msg.direction !== "host-to-iframe") return;
  if (msg.type === "TAB_CHANGED") {
    tabListeners.forEach((fn) => fn(msg.payload));
    return;
  }
  const p = pending.get(msg.id);
  if (!p) return;
  pending.delete(msg.id);
  clearTimeout(p.timer);
  if (msg.ok) p.resolve(msg.data);
  else p.reject(new Error(msg.error || "Fejl i udvidelsen"));
});

export function inIframe(): boolean {
  return window.self !== window.top;
}

export function request<T>(type: string, payload?: unknown, timeout = TIMEOUT_MS): Promise<T> {
  if (!inIframe()) return Promise.reject(new Error("Ikke åbnet i udvidelsen"));
  const id = crypto.randomUUID();
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      pending.delete(id);
      reject(new Error("Udvidelsen svarede ikke"));
    }, timeout);
    pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
    // The panel's origin is chrome-extension://<id>; the panel checks our origin on its side.
    window.parent.postMessage({ ns: NS, id, direction: "iframe-to-host", type, payload }, "*");
  });
}

let detected: Promise<boolean> | null = null;

/** True when the app is running inside the Gær browser extension. */
export function isExtension(): Promise<boolean> {
  detected ??= request<{ version: string }>("GET_VERSION", undefined, 1500).then(() => true, () => false);
  return detected;
}

export const extension = {
  version: () => request<{ version: string; endpoint: string }>("GET_VERSION"),
  pageInfo: () => request<PageInfo>("GET_PAGE_INFO"),
  snapshot: (maxChars = 5000) => request<PageSnapshot>("GET_PAGE_SNAPSHOT", { maxChars }),
  selection: () => request<string>("GET_SELECTION"),
  openUrl: (url: string) => request<boolean>("OPEN_URL", { url }),
  openOptions: () => request<void>("OPEN_OPTIONS"),
  onTabChanged(fn: (info: PageInfo) => void) {
    tabListeners.add(fn);
    return () => void tabListeners.delete(fn);
  },
};

export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

// ── Happy AI (happy-management/surdej-v1 apps/extension) ───────────────────
// The Happy AI extension injects a content script in every page. A top-level page such as /admin talks
// to it with window.postMessage on the "happyai-bridge" namespace (src/shared/protocol.ts there):
// request {namespace, id, direction: "iframe-to-host", type, payload} → response {…, direction: "host-to-iframe", ok, data, error}.
// PING, GET_PAGE_INFO, GET_SELECTION and OPEN_SIDE_PANEL are answered by the content script;
// GET_VERSION, GET_INSTANCES, SET_INSTANCE and OPEN_OPTIONS only by the side panel, so they fail from a normal page.

export const HAPPY_AI_NS = "happyai-bridge";
export const HAPPY_AI_STORE_URL = "https://chromewebstore.google.com/detail/happy-ai/cpfeknokgmlbnnllagedhmiaiocflegh";

export type HappyAiRequest =
  | "PING" | "GET_VERSION" | "GET_PAGE_INFO" | "GET_SELECTION" | "OPEN_SIDE_PANEL" | "GET_INSTANCES" | "SET_INSTANCE" | "OPEN_OPTIONS";
export type HappyAiPageInfo = { url: string; title: string; hostname: string; pathname: string };
export type HappyAiInstance = { label: string; url: string };

const happyPending = new Map<string, Pending>();
let happySeq = 0;

window.addEventListener("message", (event) => {
  const msg = event.data;
  if (event.source !== window || !msg || msg.namespace !== HAPPY_AI_NS || msg.direction !== "host-to-iframe") return;
  const p = happyPending.get(msg.id);
  if (!p) return;
  happyPending.delete(msg.id);
  clearTimeout(p.timer);
  if (msg.ok) p.resolve(msg.data);
  else p.reject(new Error(msg.error || "Fejl i Happy AI-udvidelsen"));
});

export function happyAiRequest<T>(type: HappyAiRequest, payload?: unknown, timeout = 2500): Promise<T> {
  const id = `gaer-${Date.now()}-${++happySeq}`;
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      happyPending.delete(id);
      reject(new Error("Udvidelsen svarede ikke"));
    }, timeout);
    happyPending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
    window.postMessage({ namespace: HAPPY_AI_NS, id, direction: "iframe-to-host", type, payload }, window.location.origin);
  });
}

export const happyAi = {
  ping: (timeout = 1000) => happyAiRequest<{ pong: boolean; version?: string }>("PING", undefined, timeout),
  version: () => happyAiRequest<{ version: string }>("GET_VERSION"),
  pageInfo: () => happyAiRequest<HappyAiPageInfo>("GET_PAGE_INFO"),
  selection: () => happyAiRequest<{ text: string }>("GET_SELECTION").then((r) => r?.text ?? ""),
  openSidePanel: () => happyAiRequest<unknown>("OPEN_SIDE_PANEL"),
  instances: () => happyAiRequest<{ endpoint: string; endpoints: HappyAiInstance[] }>("GET_INSTANCES"),
  setInstance: (url: string) => happyAiRequest<{ endpoint: string }>("SET_INSTANCE", { url }),
  openOptions: () => happyAiRequest<void>("OPEN_OPTIONS"),
};
