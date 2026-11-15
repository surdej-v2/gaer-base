// Upload to the file tree straight to Object Storage (server/app/uploads.py), shared by /filer, /m/filer and
// /admin/files. The bytes go from the browser to the bucket on presigned links — one PUT, or multipart in parts for
// big files — so Cloudflare's request limit (100 MB) and the app server are not in the way. sendToBucket is also
// used by /admin/data/transfer for import packages. Only when the bucket
// cannot be reached (direct upload off, or its CORS not set) does the file go through the server (MAX_UPLOAD_MB).
//
// A zip can be kept as it is or unpacked (unzip): the server's job files.unzip then puts every file in it into the
// folder, keeping its folders; waitForUnzip follows it.
import { api, ApiError } from "./api";

export type UploadBase = "/api/files" | "/api/admin/files";

export type UploadStatus = {
  id: string; path: string; filename: string; size_bytes: number; unzip: boolean; done: number; error: string | null;
  status: "uploading" | "unpacking" | "done" | "failed" | "aborted";
  result: { file?: unknown; files?: number; bytes?: number; total?: number; skipped?: { name: string; reason: string }[] } | null;
};

export type UploadOptions = {
  base: UploadBase;
  path: string;              // the target folder, e.g. ~/noter/ or /shared/
  overwrite?: boolean;
  unzip?: boolean;           // a zip: unpack it into the folder
  intoFolder?: boolean;      // unzip into a new folder named after the zip
  onProgress?: (sent: number, total: number) => void;
};

type Start = BucketStart & { id: string };

export const isZip = (f: File) => /\.zip$/i.test(f.name);
export const zipStem = (name: string) => name.replace(/\.zip$/i, "").trim() || "zip";

const PARALLEL = 4;
const TRIES = 3;
const SERVER_MAX = 95 * 1024 * 1024; // what can go through Cloudflare to the server (its limit is 100 MB)

/** A failed PUT to the bucket: status 0 means the browser could not reach it (network or CORS). */
class PutError extends Error {
  constructor(public status: number) {
    super(status ? `Object Storage svarede ${status}` : "Object Storage kunne ikke nås");
  }
}

function put(url: string, body: Blob, contentType: string | null, onProgress: (sent: number) => void): Promise<string | null> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    if (contentType) xhr.setRequestHeader("Content-Type", contentType);
    if (url.startsWith("/")) xhr.setRequestHeader("X-Gaer", "1"); // the memory backend in tests answers on our origin
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve(xhr.getResponseHeader("ETag")) : reject(new PutError(xhr.status)));
    xhr.onerror = () => reject(new PutError(0));
    xhr.send(body);
  });
}

async function putWithRetry(url: () => Promise<string>, body: Blob, contentType: string | null, onProgress: (sent: number) => void) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await put(await url(), body, contentType, onProgress);
    } catch (e) {
      onProgress(0);
      if (attempt >= TRIES) throw e;
      await new Promise((r) => setTimeout(r, 1000 * attempt));
    }
  }
}

/** A started direct upload: one presigned PUT link, or multipart (part_size, parts) with links asked for as it goes. */
export type BucketStart = { id: string | number; content_type: string; multipart: boolean; url?: string; part_size?: number; parts?: number };

/**
 * Sends a file's bytes to the bucket on the links of a started upload (`partsUrl` gives the links of multipart parts).
 * Returns the parts with their ETags for the complete call (empty after a single PUT).
 */
export async function sendToBucket(file: File, start: BucketStart, partsUrl: string,
  onProgress: (sent: number, total: number) => void = () => {}): Promise<{ number: number; etag: string }[]> {
  if (!start.multipart) {
    await putWithRetry(async () => start.url!, file, start.content_type, (sent) => onProgress(sent, file.size));
    onProgress(file.size, file.size);
    return [];
  }
  const size = start.part_size!;
  const count = start.parts!;
  const sent = new Array<number>(count + 1).fill(0);
  const report = () => onProgress(sent.reduce((a, b) => a + b, 0), file.size);
  const urls = new Map<number, string>();
  const urlFor = async (n: number) => {
    if (!urls.has(n)) {
      const numbers = Array.from({ length: Math.min(50, count - n + 1) }, (_, i) => n + i);
      const got = await api.post<{ urls: Record<string, string> }>(partsUrl, { numbers });
      for (const [k, v] of Object.entries(got.urls)) urls.set(Number(k), v);
    }
    return urls.get(n)!;
  };
  const etags: { number: number; etag: string }[] = [];
  let next = 1;
  const worker = async () => {
    while (next <= count) {
      const n = next++;
      const blob = file.slice((n - 1) * size, Math.min(file.size, n * size));
      const etag = await putWithRetry(() => urlFor(n), blob, null, (s) => { sent[n] = s; report(); });
      if (!etag) throw new Error("Object Storage sendte ikke ETag tilbage – bucketens CORS skal vise ETag-headeren");
      sent[n] = blob.size;
      report();
      etags.push({ number: n, etag });
    }
  };
  await Promise.all(Array.from({ length: Math.min(PARALLEL, count) }, worker));
  return etags;
}

/** Whether to fall back to sending the file through the server: direct upload is off, or the bucket cannot be reached
 * from the browser (and the file fits through Cloudflare). */
export function sendViaServerInstead(e: unknown, size: number): boolean {
  const off = e instanceof ApiError && e.status === 404 && /Direkte upload/.test(e.message);
  return off || (e instanceof PutError && e.status === 0 && size <= SERVER_MAX);
}

async function direct(file: File, o: UploadOptions): Promise<UploadStatus> {
  const start = await api.post<Start>(`${o.base}/uploads`, {
    path: o.path, filename: file.name, size: file.size, content_type: file.type || null,
    overwrite: !!o.overwrite, unzip: !!o.unzip, into_folder: !!o.intoFolder,
  });
  try {
    const parts = await sendToBucket(file, start, `${o.base}/uploads/${start.id}/parts`, o.onProgress);
    return await api.post<UploadStatus>(`${o.base}/uploads/${start.id}/complete`, start.multipart ? { parts } : {});
  } catch (e) {
    api.del(`${o.base}/uploads/${start.id}`).catch(() => {});
    throw e;
  }
}

/** Through the server: the fallback when the bucket cannot be reached from the browser. */
async function viaServer(file: File, o: UploadOptions): Promise<UploadStatus | { file: unknown }> {
  const form = new FormData();
  form.append("file", file);
  const q = new URLSearchParams({ path: o.path });
  if (o.overwrite) q.set("overwrite", "true");
  if (o.unzip) q.set("unzip", "true");
  if (o.unzip && o.intoFolder) q.set("into_folder", "true");
  const url = o.base === "/api/files" ? `/api/files?${q}` : `${o.base}/upload?${q}`;
  o.onProgress?.(0, file.size);
  const r = await api.post<{ upload?: UploadStatus } & Record<string, unknown>>(url, form);
  o.onProgress?.(file.size, file.size);
  return r.upload ?? { file: r };
}

/** Uploads one file. Resolves with the upload; for unzip its status is "unpacking" (follow it with waitForUnzip). */
export async function uploadFile(file: File, o: UploadOptions): Promise<UploadStatus | { file: unknown }> {
  let status: UploadStatus;
  try {
    status = await direct(file, o);
  } catch (e) {
    if (sendViaServerInstead(e, file.size)) return viaServer(file, o);
    throw e;
  }
  if (status.status === "failed" || status.status === "aborted") throw new Error(status.error ?? "Uploaden blev ikke gennemført");
  return status;
}

/** Follows a zip being unpacked until it is done. onProgress gets files handled so far and the total. */
export async function waitForUnzip(base: UploadBase, id: string, onProgress?: (done: number, total: number | null) => void): Promise<UploadStatus> {
  for (let delay = 700; ; delay = Math.min(3000, delay * 1.3)) {
    const s = await api.get<UploadStatus>(`${base}/uploads/${id}`);
    onProgress?.(s.done, s.result?.total ?? null);
    if (s.status === "done") return s;
    if (s.status !== "unpacking") throw new Error(s.error ?? "Udpakningen fejlede");
    await new Promise((r) => setTimeout(r, delay));
  }
}

export function pct(sent: number, total: number): string {
  return total ? `${Math.floor((sent / total) * 100)} %` : "";
}
