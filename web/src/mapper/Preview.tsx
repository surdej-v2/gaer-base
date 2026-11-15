// Mapper's preview of a shortcut: the page in a frame next to or below the list. The server first reads the page's
// headers (GET /api/mapper/frame-check): sites that refuse to be framed (X-Frame-Options, CSP frame-ancestors) get
// "Kan ikke vises her" and a link instead of a broken frame. HTML is sandboxed; a PDF opens in the browser's own
// PDF viewer, which does not run in a sandboxed frame. The CSP of /extension/mapper allows https frames (main.py).
import { ExternalLink, Maximize2, Minimize2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { errorText } from "../api";
import { extension } from "../bridge/extension";
import { mapperApi, type FrameCheck, type Shortcut } from "./api";

export type Placement = "auto" | "right" | "below";

const checks = new Map<string, FrameCheck>();

/** Known after a check: false for pages that refuse frames, so the list stops offering them. */
export function knownFramable(url: string): boolean | undefined {
  return checks.get(url)?.framable;
}

export function previewable(s: Shortcut): boolean {
  return s.url.startsWith("https://") && knownFramable(s.url) !== false;
}

export function openInTab(url: string) {
  extension.openUrl(url).catch(() => window.open(url, "_blank", "noopener"));
}

export function Preview({ shortcut, placement, onPlacement, expanded, onExpanded, onClose, onChecked, variant = "panel", footer }: {
  shortcut: Shortcut;
  /** panel: next to or below the list (wide); sheet: a bottom sheet in the narrow panel. */
  variant?: "panel" | "sheet";
  /** More actions under "Åbn i ny fane" in the sheet (Ret, Fjern). */
  footer?: ReactNode;
  placement: Placement;
  onPlacement: (p: Placement) => void;
  expanded: boolean;
  onExpanded: (on: boolean) => void;
  onClose: () => void;
  onChecked: () => void;
}) {
  const [check, setCheck] = useState<FrameCheck | null>(() => checks.get(shortcut.url) ?? null);
  const [error, setError] = useState<string | null>(null);
  const [anyway, setAnyway] = useState(false);

  useEffect(() => {
    setAnyway(false);
    setError(null);
    const known = checks.get(shortcut.url);
    setCheck(known ?? null);
    if (known) return;
    let live = true;
    mapperApi.frameCheck(shortcut.url).then(
      (r) => {
        checks.set(shortcut.url, r);
        if (live) setCheck(r);
        onChecked();
      },
      (e) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [shortcut.url, onChecked]);

  const show = check && (check.framable || anyway);
  const src = check?.final_url ?? shortcut.url;

  return (
    <section className={`mp-preview${expanded ? " mp-expanded" : ""}${variant === "sheet" ? " mp-preview-sheet" : ""}`} aria-label={`Forhåndsvisning af ${shortcut.title}`}>
      <div className="mp-head">
        <strong title={shortcut.url}>{shortcut.title}</strong>
        {variant === "panel" && <span className="mp-place" role="group" aria-label="Placering af forhåndsvisningen">
          {(["auto", "right", "below"] as Placement[]).map((p) => (
            <button key={p} type="button" className={placement === p ? "active" : ""} aria-pressed={placement === p} onClick={() => onPlacement(p)}>
              {p === "auto" ? "Auto" : p === "right" ? "Højre" : "Under"}
            </button>
          ))}
        </span>}
        <button type="button" className="mp-icon" onClick={() => onExpanded(!expanded)}
          aria-label={variant === "sheet" ? (expanded ? "Mindre forhåndsvisning" : "Forhåndsvisning i fuld skærm") : expanded ? "Vis listen igen" : "Udvid forhåndsvisningen"}
          title={expanded ? "Mindre" : "Udvid"}>
          {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        </button>
        {variant === "panel" && <button type="button" className="mp-icon" onClick={() => openInTab(shortcut.url)} aria-label="Åbn i ny fane" title="Åbn i ny fane">
          <ExternalLink size={16} />
        </button>}
        <button type="button" className="mp-icon" onClick={onClose} aria-label="Luk forhåndsvisningen" title="Luk">
          <X size={16} />
        </button>
      </div>
      <span className="tiny muted" style={{ wordBreak: "break-all" }}>{shortcut.url}</span>
      {error && <div className="mp-blocked"><strong>Kan ikke vises her</strong><span className="small muted">{error}</span>
        {variant === "panel" && <button className="btn btn-primary btn-sm" onClick={() => openInTab(shortcut.url)}>Åbn i ny fane</button>}</div>}
      {!check && !error && <p className="muted small">Tjekker siden …</p>}
      {check && !show && (
        <div className="mp-blocked">
          <strong>Kan ikke vises her</strong>
          <span className="small muted">{check.reason ?? "Siden tillader ikke at blive vist inde i en anden side."}</span>
          <div className="row">
            {variant === "panel" && <button className="btn btn-primary btn-sm" onClick={() => openInTab(shortcut.url)}>Åbn i ny fane</button>}
            {src.startsWith("https://") && <button className="btn btn-ghost btn-sm" onClick={() => setAnyway(true)}>Prøv alligevel</button>}
          </div>
        </div>
      )}
      {show && (check.kind === "pdf" ? (
        <iframe key={src} className="mp-frame" src={src} title={shortcut.title} referrerPolicy="no-referrer" />
      ) : (
        <iframe key={src} className="mp-frame" src={src} title={shortcut.title} referrerPolicy="no-referrer"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox" />
      ))}
      {variant === "sheet" && (
        <div className="mp-sheet-foot">
          <button className="btn btn-primary btn-block mp-touch" onClick={() => openInTab(shortcut.url)}><ExternalLink size={16} /> Åbn i ny fane</button>
          {footer}
        </div>
      )}
    </section>
  );
}
