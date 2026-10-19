// Every call to the Python server goes through here. State-changing requests carry the
// X-Gaer header, which the server requires as CSRF protection.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Inside the browser extension or an Office pane the app runs in an iframe; the server then
// issues a partitioned SameSite=None session cookie that works there.
const embedded = window.self !== window.top;

// The admin app on /admin marks its calls, so the server always answers them as the real admin,
// also while the app on / is impersonating a test account.
let appName: string | null = null;
export function setApiApp(name: "admin"): void {
  appName = name;
}

// Optional observer used by the API tracer in /admin. Nothing is recorded unless one is set.
export type ApiCall = {
  id: number;
  method: string;
  path: string;
  requestBody?: unknown;
  status: number | null;
  responseBody?: unknown;
  startedAt: number;
  ms: number;
};
export type ApiObserver = { start(call: ApiCall): void; end(call: ApiCall): void };
let observer: ApiObserver | null = null;
let callSeq = 0;
export function setApiObserver(o: ApiObserver | null): void {
  observer = o;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Gaer": "1" };
  if (embedded) headers["X-Gaer-Embedded"] = "1";
  if (appName) headers["X-Gaer-App"] = appName;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const call: ApiCall | null = observer
    ? { id: ++callSeq, method, path, requestBody: body instanceof FormData ? "[FormData]" : body, status: null,
        startedAt: Date.now(), ms: 0 }
    : null;
  if (call) observer?.start(call);
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  } catch (e) {
    if (call) observer?.end({ ...call, status: 0, responseBody: String(e), ms: Date.now() - call.startedAt });
    throw e;
  }
  let data: unknown = undefined;
  if (res.status !== 204) {
    const text = await res.text();
    try {
      data = text ? JSON.parse(text) : undefined;
    } catch {
      data = text; // not JSON
    }
  }
  if (call) observer?.end({ ...call, status: res.status, responseBody: data, ms: Date.now() - call.startedAt });
  if (!res.ok) {
    let message = `Fejl ${res.status}`;
    const detail = (data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") message = detail;
    else if (Array.isArray(detail)) message = "Tjek felterne og prøv igen";
    throw new ApiError(res.status, message);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : "Noget gik galt";
}
