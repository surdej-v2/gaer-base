// /office-addin — the one URL all four Office manifests point to.
//
// It is public on purpose: Office closes the pane if Office.js is not initialised quickly,
// so nothing here may wait for login. It loads Office.js, finds the host with Office.onReady,
// and embeds the right tool (/extension/tools/<app>) in a same-origin iframe. The iframe holds
// login and UI; this shell only runs Office.js commands for it (see bridge/officeHost.ts).

import { useEffect, useRef, useState } from "react";
import { OFFICE_NS, type HostMessage, type OfficeApp, type OfficeInfo } from "../bridge/officeHost";
import { HANDLERS } from "./handlers";

declare const Office: any; // eslint-disable-line @typescript-eslint/no-explicit-any

const OFFICE_JS = "https://appsforoffice.microsoft.com/lib/1/hosted/office.js";

function loadOfficeJs(): Promise<void> {
  if ((window as unknown as { Office?: unknown }).Office) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = OFFICE_JS;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Office.js kunne ikke hentes"));
    document.head.appendChild(s);
  });
}

function hostToApp(host: string | undefined): OfficeApp {
  switch (host) {
    case "Word": return "word";
    case "Excel": return "excel";
    case "PowerPoint": return "powerpoint";
    case "Outlook": return "outlook";
    default: return "unknown";
  }
}

export default function OfficeShell() {
  const params = new URLSearchParams(window.location.search);
  const requested = (params.get("app") as OfficeApp | null) ?? "unknown";
  const [info, setInfo] = useState<OfficeInfo>({ app: requested, mode: params.get("mode"), fileName: null, ready: false });
  const [error, setError] = useState<string | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const infoRef = useRef(info);
  infoRef.current = info;
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  useEffect(() => {
    loadOfficeJs()
      .then(() => Office.onReady((r: { host?: string }) => {
        const app = hostToApp(r.host);
        const url: string | undefined = Office.context?.document?.url;
        setInfo({ app: app === "unknown" ? requested : app, mode: params.get("mode"), fileName: url ? url.split(/[\\/]/).pop() ?? null : null, ready: true });
        // Outlook: the pane stays open while the user moves between mails.
        if (app === "outlook" && Office.context.mailbox?.addHandlerAsync) {
          Office.context.mailbox.addHandlerAsync(Office.EventType.ItemChanged, () => {
            frame.current?.contentWindow?.postMessage({ ns: OFFICE_NS, kind: "office-info", info: { ...infoRef.current } }, window.location.origin);
          });
        }
      }))
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Tell the tool when Office is ready.
  useEffect(() => {
    if (info.ready) frame.current?.contentWindow?.postMessage({ ns: OFFICE_NS, kind: "office-info", info }, window.location.origin);
  }, [info]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const msg = event.data as HostMessage;
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow || msg?.ns !== OFFICE_NS) return;
      const reply = (body: object) => frame.current?.contentWindow?.postMessage({ ns: OFFICE_NS, kind: "result", ...body }, window.location.origin);
      if (msg.kind === "hello") {
        if (infoRef.current.ready) frame.current?.contentWindow?.postMessage({ ns: OFFICE_NS, kind: "office-info", info: infoRef.current }, window.location.origin);
        return;
      }
      if (msg.kind !== "exec") return;
      const handler = HANDLERS[infoRef.current.app]?.[msg.type];
      if (!handler) {
        reply({ id: msg.id, ok: false, error: `Ukendt kommando: ${msg.type}` });
        return;
      }
      // Office.js does not like overlapping batches; run commands one at a time.
      queue.current = queue.current.then(() =>
        handler(msg.payload).then(
          (data) => reply({ id: msg.id, ok: true, data }),
          (e: Error) => reply({ id: msg.id, ok: false, error: e?.message || String(e) }),
        ),
      );
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const tool = ["word", "outlook"].includes(info.app) ? info.app : "document";
  const src = `/extension/tools/${tool}${info.mode ? `?mode=${encodeURIComponent(info.mode)}` : ""}`;

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      {error && <div className="alert error" style={{ margin: 12 }}>{error}</div>}
      <iframe ref={frame} src={src} title="Tørgær" style={{ border: 0, flex: 1, width: "100%" }}
              allow="publickey-credentials-get *; publickey-credentials-create *; clipboard-write" />
    </div>
  );
}
