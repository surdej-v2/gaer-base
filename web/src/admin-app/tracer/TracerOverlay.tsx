// The floating tracer badge (screen 12), after happy-groups TracerOverlay.tsx: bottom left, draggable
// (4 px threshold, kept inside the viewport), click opens the panel, × asks how long to hide it.
import { useRef, useState, type PointerEvent } from "react";
import { X, Zap } from "lucide-react";
import { SANS } from "../theme";
import { Modal } from "../ui";
import { tracer, useTracer } from "./tracerStore";

type Drag = { id: number; sx: number; sy: number; ox: number; oy: number; w: number; h: number; moved: boolean };

export default function TracerOverlay() {
  const { requests, recording, open, hidden } = useTracer();
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [asking, setAsking] = useState(false);
  const drag = useRef<Drag | null>(null);

  const pending = requests.filter((r) => r.pending).length;
  const errors = requests.filter((r) => !r.pending && (r.status ?? 0) >= 400).length;

  const down = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: r.left, oy: r.top, w: r.width, h: r.height, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    setPos({ x: Math.min(Math.max(d.ox + dx, 8), window.innerWidth - d.w - 8), y: Math.min(Math.max(d.oy + dy, 8), window.innerHeight - d.h - 8) });
  };
  const up = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    if (!d.moved) tracer.toggleOpen();
  };

  const option = { height: 38, padding: "0 14px", borderRadius: 8, border: "1px solid #e2e8f0", background: "#ffffff", cursor: "pointer",
    textAlign: "left", font: `500 13px ${SANS}`, color: "#020817" } as const;

  return (
    <>
      {!hidden && (
        <div role="button" tabIndex={0} aria-label="API-tracer — åbn fejlsøgningspanelet" title="Klik: fejlsøgningspanel · træk: flyt · ×: skjul"
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onKeyDown={(e) => e.key === "Enter" && tracer.toggleOpen()}
          style={{ position: "fixed", zIndex: 60, left: pos ? pos.x : 16, top: pos ? pos.y : "auto", bottom: pos ? "auto" : 80, display: "inline-flex",
            alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 9999, border: `1px solid ${open ? "rgba(16,185,129,0.4)" : "#e2e8f0"}`,
            background: open ? "rgba(236,253,245,0.95)" : "rgba(255,255,255,0.9)", color: open ? "#047857" : "#64748b", font: `11px ${SANS}`,
            boxShadow: "var(--hm-shadow-lg)", backdropFilter: "blur(8px)", cursor: "grab", userSelect: "none", touchAction: "none" }}>
          <span style={{ width: 8, height: 8, borderRadius: 9999, background: recording ? "#10b981" : "#cbd5e1", animation: recording ? "tgpulse 1.6s infinite" : "none" }} />
          <Zap size={12} strokeWidth={2} />
          <span style={{ fontWeight: 500 }}>Tracer</span>
          {recording && requests.length > 0 && (
            <span style={{ padding: "0 4px", borderRadius: 4, font: "9px var(--hm-font-mono)", background: errors ? "rgba(239,68,68,0.15)" : "rgba(16,185,129,0.15)",
              color: errors ? "#dc2626" : "#047857" }}>{pending ? `${pending}⇄` : requests.length}</span>
          )}
          <button type="button" aria-label="Skjul tracer" title="Skjul tracer" className="tg-hover" onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); setAsking(true); }}
            style={{ margin: "0 -4px 0 2px", width: 16, height: 16, padding: 0, border: 0, borderRadius: 9999, background: "transparent", cursor: "pointer",
              color: "inherit", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <X size={10} strokeWidth={2} />
          </button>
        </div>
      )}
      {asking && (
        <Modal width={384} z={70} gap={12} onClose={() => setAsking(false)}>
          <span style={{ fontSize: 17, fontWeight: 700 }}>Skjul API-traceren</span>
          <span style={{ fontSize: 13, color: "#64748b" }}>Hvor længe skal mærket være skjult?</span>
          <button type="button" className="tg-row" style={option} onClick={() => { tracer.hide(false); setAsking(false); }}>Indtil siden genindlæses</button>
          <button type="button" className="tg-row" style={option} onClick={() => { tracer.hide(true); setAsking(false); }}>Resten af denne browsersession</button>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button type="button" className="tg-hover" onClick={() => setAsking(false)} style={{ height: 34, padding: "0 12px", border: 0, borderRadius: 8,
              background: "transparent", cursor: "pointer", font: `500 13px ${SANS}`, color: "#020817" }}>Annullér</button>
          </div>
          <span style={{ fontSize: 12, color: "#64748b", textWrap: "pretty" }}>Hent den frem igen med „Vis API-tracer“ i udvidelsens menu i topbaren.</span>
        </Modal>
      )}
    </>
  );
}
