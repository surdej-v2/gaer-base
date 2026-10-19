// The tracer panel (screen 12), after happy-groups TracerPanel.tsx: page context, recording, select,
// Copy YAML / Download and every call with URL and bodies. Labels are English as in happy-groups.
import { useState } from "react";
import { ChevronDown, ChevronRight, Copy, Download, Square, SquareCheck, Trash2, X, Zap } from "lucide-react";
import { MONO, SANS } from "../theme";
import { IconBtn } from "../ui";
import { toYaml, tracer, useTracer, type TracedRequest } from "./tracerStore";

const METHOD: Record<string, [bg: string, fg: string, bd: string]> = {
  GET: ["rgba(16,185,129,0.15)", "#047857", "rgba(16,185,129,0.3)"],
  POST: ["rgba(59,130,246,0.15)", "#1d4ed8", "rgba(59,130,246,0.3)"],
  PATCH: ["rgba(168,85,247,0.15)", "#7e22ce", "rgba(168,85,247,0.3)"],
  PUT: ["rgba(245,158,11,0.15)", "#b45309", "rgba(245,158,11,0.3)"],
  DELETE: ["rgba(239,68,68,0.15)", "#b91c1c", "rgba(239,68,68,0.3)"],
};
const statusColor = (s: number | null) => (s === null ? "#64748b" : s < 300 ? "#10b981" : s < 400 ? "#3b82f6" : s < 500 ? "#f59e0b" : "#ef4444");
const json = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));
const pre = { margin: 0, font: `10px/1.45 ${MONO}`, background: "#f1f5f9", padding: "6px 8px", borderRadius: 4, overflowX: "auto", color: "#334155" } as const;
const small = { fontSize: 10, fontWeight: 600, color: "#64748b" } as const;

export default function TracerPanel({ context }: { context: [string, string][] }) {
  const { requests, recording, open } = useTracer();
  const [selected, setSelected] = useState<number[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  if (!open) return null;

  const pending = requests.filter((r) => r.pending).length;
  const errors = requests.filter((r) => !r.pending && (r.status ?? 0) >= 400).length;
  const chosen = requests.filter((r) => selected.includes(r.id));
  const all = requests.length > 0 && chosen.length === requests.length;

  const copyYaml = () => {
    navigator.clipboard?.writeText(toYaml(chosen)).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([toYaml(chosen)], { type: "text/yaml;charset=utf-8" }));
    a.download = `tracer-export-${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.yaml`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const ghost = { display: "flex", alignItems: "center", gap: 4, height: 24, padding: "0 8px", border: 0, borderRadius: 6, background: "transparent",
    cursor: "pointer", font: `11px ${SANS}`, color: "#020817" } as const;

  return (
    <div aria-label="API Tracer" style={{ position: "fixed", top: 0, right: 0, bottom: 0, zIndex: 50, width: 420, maxWidth: "100%", boxSizing: "border-box",
      background: "#ffffff", borderLeft: "1px solid #e2e8f0", boxShadow: "var(--hm-shadow-2xl)", display: "flex", flexDirection: "column", fontSize: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 16px", borderBottom: "1px solid #e2e8f0", background: "#f8fafc" }}>
        <Zap size={16} color="#2563eb" strokeWidth={2} />
        <span style={{ flex: 1, fontSize: 14, fontWeight: 600 }}>API Tracer</span>
        {pending > 0 && <span style={{ fontSize: 9, fontWeight: 700, padding: "2px 7px", borderRadius: 9999, background: "#2563eb", color: "#ffffff", animation: "tgpulse 1s infinite" }}>{pending} in-flight</span>}
        {errors > 0 && <span style={{ fontSize: 9, fontWeight: 700, padding: "2px 7px", borderRadius: 9999, background: "#dc2626", color: "#ffffff" }}>{errors} errors</span>}
        <span style={{ width: 1, height: 20, background: "#e2e8f0" }} />
        <IconBtn icon={Trash2} label="Clear all" size={28} iconSize={14} color="#475569" onClick={() => { tracer.clear(); setSelected([]); }} />
        <IconBtn icon={X} label="Luk" size={28} iconSize={14} color="#475569" onClick={() => tracer.setOpen(false)} />
      </div>
      <div style={{ padding: "10px 16px", borderBottom: "1px solid #e2e8f0", display: "grid", gridTemplateColumns: "96px minmax(0,1fr)", gap: "4px 10px", fontSize: 11 }}>
        {context.map(([k, v]) => [
          <span key={k} style={{ color: "#64748b" }}>{k}</span>,
          <span key={k + "v"} style={{ fontFamily: MONO, color: "#020817", overflowWrap: "anywhere" }}>{v}</span>,
        ])}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 16px", borderBottom: "1px solid #e2e8f0" }}>
        <button type="button" onClick={() => tracer.setRecording(!recording)} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 8px", border: 0,
          borderRadius: 6, cursor: "pointer", font: `500 12px ${SANS}`, background: recording ? "rgba(16,185,129,0.15)" : "#f1f5f9", color: recording ? "#047857" : "#64748b" }}>
          <span style={{ width: 8, height: 8, borderRadius: 9999, background: recording ? "#10b981" : "#cbd5e1", animation: recording ? "tgpulse 1.6s infinite" : "none" }} />
          {recording ? "Recording" : "Paused"}
        </button>
        <span style={{ flex: 1, color: "#64748b" }}>{requests.length} requests captured</span>
      </div>
      {requests.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 16px", borderBottom: "1px solid #e2e8f0", color: "#64748b" }}>
          <button type="button" className="tg-hover" onClick={() => setSelected(all ? [] : requests.map((r) => r.id))} style={{ ...ghost, height: "auto", padding: "4px 8px", font: `12px ${SANS}`, color: "#475569", gap: 6 }}>
            {all ? <SquareCheck size={14} color="#2563eb" /> : <Square size={14} color="#94a3b8" />}{all ? "Deselect all" : "Select all"}
          </button>
          {chosen.length > 0 && (
            <>
              <span>· {chosen.length} selected</span>
              <span style={{ flex: 1 }} />
              <button type="button" className="tg-hover" onClick={copyYaml} style={ghost}><Copy size={12} />{copied ? "Copied!" : "Copy YAML"}</button>
              <button type="button" className="tg-hover" onClick={download} style={ghost}><Download size={12} />Download</button>
            </>
          )}
        </div>
      )}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {requests.length === 0 && (
          <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 6, padding: "0 32px",
            textAlign: "center", color: "#94a3b8" }}>
            <Zap size={32} color="#cbd5e1" strokeWidth={2} />
            <span style={{ fontSize: 14, fontWeight: 500 }}>No requests captured</span>
            <span>{recording ? "API calls will appear here as they are made" : "Enable the tracer to start capturing API calls"}</span>
          </div>
        )}
        {requests.map((r) => (
          <Row key={r.id} r={r} selected={selected.includes(r.id)} open={expanded === r.id}
            onSelect={() => setSelected((s) => (s.includes(r.id) ? s.filter((x) => x !== r.id) : [...s, r.id]))}
            onToggle={() => setExpanded(expanded === r.id ? null : r.id)} />
        ))}
      </div>
    </div>
  );
}

function Row({ r, selected, open, onSelect, onToggle }: { r: TracedRequest; selected: boolean; open: boolean; onSelect: () => void; onToggle: () => void }) {
  const [bg, fg, bd] = METHOD[r.method] ?? METHOD.GET;
  const Chev = open ? ChevronDown : ChevronRight;
  return (
    <div style={{ borderBottom: "1px solid #f1f5f9", background: selected ? "rgba(37,99,235,0.06)" : r.pending ? "rgba(37,99,235,0.03)" : "#ffffff" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 16px" }}>
        <button type="button" aria-label="Vælg" onClick={onSelect} style={{ width: 14, height: 14, flex: "none", padding: 0, border: 0, background: "transparent", cursor: "pointer", display: "flex" }}>
          {selected ? <SquareCheck size={14} color="#2563eb" /> : <Square size={14} color="#cbd5e1" />}
        </button>
        <button type="button" onClick={onToggle} style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 8, padding: 0, border: 0, background: "transparent", cursor: "pointer", textAlign: "left" }}>
          <Chev size={12} color="#64748b" style={{ flex: "none" }} />
          <span style={{ flex: "none", padding: "1px 6px", borderRadius: 4, border: `1px solid ${bd}`, background: bg, color: fg, font: `600 9px ${MONO}` }}>{r.method}</span>
          <span title={r.path} style={{ flex: 1, minWidth: 0, font: `12px ${MONO}`, color: "#020817", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.path}</span>
          <span style={{ flex: "none", font: `600 10px ${MONO}`, color: statusColor(r.status) }}>{r.pending ? "…" : String(r.status)}</span>
          <span style={{ flex: "none", font: `10px ${MONO}`, color: "#64748b" }}>{r.pending ? "" : r.ms < 1000 ? `${r.ms}ms` : `${(r.ms / 1000).toFixed(1)}s`}</span>
          <span style={{ flex: "none", fontSize: 9, color: "#94a3b8" }}>{new Date(r.ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
        </button>
      </div>
      {open && (
        <div style={{ padding: "0 16px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ font: `10px ${MONO}`, color: "#64748b", background: "#f1f5f9", padding: "4px 8px", borderRadius: 4, overflowWrap: "anywhere" }}>{window.location.origin + r.path}</div>
          <div style={{ fontSize: 10, color: "#64748b", fontFamily: MONO }}>↳ side: {r.page}</div>
          {r.requestBody !== undefined && (
            <>
              <span style={small}>Request Body</span>
              <pre style={{ ...pre, maxHeight: 160 }}>{json(r.requestBody)}</pre>
            </>
          )}
          <span style={small}>Response Body</span>
          <pre style={{ ...pre, maxHeight: 190 }}>{r.pending ? "…" : json(r.responseBody ?? null)}</pre>
        </div>
      )}
    </div>
  );
}
