// Small building blocks shared by the /admin pages. Values follow the prototype in specs/admin-app/design.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { X, type LucideIcon } from "lucide-react";
import { SANS, TONE, type Tone } from "./theme";

export function Pill({ tone, children, size = 11, style }: { tone: Tone; children: ReactNode; size?: number; style?: CSSProperties }) {
  const [bg, fg] = TONE[tone];
  return (
    <span style={{ fontSize: size, fontWeight: 700, padding: size === 10 ? "1px 7px" : "2px 8px", borderRadius: 9999, background: bg, color: fg, whiteSpace: "nowrap", ...style }}>
      {children}
    </span>
  );
}

type BtnKind = "primary" | "outline" | "ghost" | "dark";
const BTN: Record<BtnKind, CSSProperties> = {
  primary: { border: 0, background: "#2563eb", color: "#f8fafc" },
  outline: { border: "1px solid #e2e8f0", background: "#ffffff", color: "#020817" },
  ghost: { border: 0, background: "transparent", color: "#020817" },
  dark: { border: 0, background: "#78350f", color: "#ffffff" },
};

export function Btn({ kind = "outline", icon: Icon, iconSize = 14, height = 36, onClick, disabled, children, style, title, type = "button" }: {
  kind?: BtnKind; icon?: LucideIcon; iconSize?: number; height?: number; onClick?: () => void; disabled?: boolean;
  children?: ReactNode; style?: CSSProperties; title?: string; type?: "button" | "submit";
}) {
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled} className={kind === "ghost" ? "tg-hover" : undefined}
      style={{ display: "inline-flex", alignItems: "center", gap: 6, height, padding: "0 14px", borderRadius: 8, cursor: "pointer",
        font: `500 13px ${SANS}`, whiteSpace: "nowrap", ...BTN[kind], ...(disabled ? { opacity: 0.45, pointerEvents: "none" } : {}), ...style }}>
      {Icon && <Icon size={iconSize} strokeWidth={2} />}
      {children}
    </button>
  );
}

export function IconBtn({ icon: Icon, label, onClick, size = 32, iconSize = 16, color = "#64748b", style }: {
  icon: LucideIcon; label: string; onClick: () => void; size?: number; iconSize?: number; color?: string; style?: CSSProperties;
}) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} className="tg-hover"
      style={{ width: size, height: size, flex: "none", border: 0, borderRadius: 8, background: "transparent", cursor: "pointer", color,
        display: "flex", alignItems: "center", justifyContent: "center", padding: 0, ...style }}>
      <Icon size={iconSize} strokeWidth={2} />
    </button>
  );
}

export function PageTitle({ title, icon, children, aside }: { title: ReactNode; icon?: ReactNode; children?: ReactNode; aside?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 12, flexWrap: "wrap" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1, minWidth: 280 }}>
        <h1 style={{ margin: 0, display: "flex", alignItems: "center", gap: 10, fontSize: 26, lineHeight: 1.2, letterSpacing: "-0.02em", fontWeight: 700 }}>
          {icon}{title}
        </h1>
        {children && <p style={{ margin: 0, color: "#64748b", maxWidth: 720, textWrap: "pretty" }}>{children}</p>}
      </div>
      {aside}
    </div>
  );
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: () => void; label: string }) {
  return (
    <button type="button" aria-label={label} aria-pressed={on} onClick={onChange}
      style={{ width: 36, height: 20, borderRadius: 9999, border: 0, padding: 2, cursor: "pointer", background: on ? "#2563eb" : "#cbd5e1", display: "flex" }}>
      <span style={{ width: 16, height: 16, borderRadius: 9999, background: "#ffffff", boxShadow: "0 1px 2px rgba(0,0,0,0.2)",
        transform: `translateX(${on ? 16 : 0}px)`, transition: "transform .15s" }} />
    </button>
  );
}

/** Panel sliding in from the right with a dimmed backdrop. */
export function Drawer({ width, z = 40, onClose, label, children }: { width: number; z?: number; onClose: () => void; label: string; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: z, background: "rgba(2,8,23,0.35)" }} />
      <aside aria-label={label} style={{ position: "fixed", top: 0, right: 0, bottom: 0, zIndex: z + 1, width, maxWidth: "100vw", boxSizing: "border-box",
        background: "#ffffff", boxShadow: "var(--hm-shadow-2xl)", display: "flex", flexDirection: "column" }}>
        {children}
      </aside>
    </>
  );
}

export function CloseBtn({ onClick }: { onClick: () => void }) {
  return <IconBtn icon={X} label="Luk" onClick={onClick} />;
}

export function Modal({ width = 440, z = 50, gap = 14, onClose, children }: { width?: number; z?: number; gap?: number; onClose: () => void; children: ReactNode }) {
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: z, background: "rgba(2,8,23,0.4)" }} />
      <div role="dialog" style={{ position: "fixed", zIndex: z + 1, top: "50%", left: "50%", transform: "translate(-50%,-50%)", width,
        maxWidth: "calc(100vw - 32px)", boxSizing: "border-box", background: "#ffffff", borderRadius: 16, boxShadow: "var(--hm-shadow-2xl)",
        padding: 22, display: "flex", flexDirection: "column", gap }}>
        {children}
      </div>
    </>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div style={{ padding: "28px 16px", textAlign: "center", color: "#64748b", fontSize: 13 }}>{children}</div>;
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return <div role="alert" style={{ padding: "10px 14px", borderRadius: 10, background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.25)", color: "#b91c1c", fontSize: 13 }}>{children}</div>;
}

// ── Toast: bottom centre, #0f172a, 2.6 s ────────────────────────────────────

const ToastContext = createContext<(message: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState("");
  const timer = useRef<number>(0);
  const say = useCallback((m: string) => {
    window.clearTimeout(timer.current);
    setMessage(m);
    timer.current = window.setTimeout(() => setMessage(""), 2600);
  }, []);
  return (
    <ToastContext.Provider value={say}>
      {children}
      {message && (
        <div role="status" style={{ position: "fixed", zIndex: 80, bottom: 24, left: "50%", transform: "translateX(-50%)", padding: "10px 16px",
          borderRadius: 10, background: "#0f172a", color: "#ffffff", fontSize: 13, boxShadow: "var(--hm-shadow-lg)", maxWidth: "calc(100vw - 48px)" }}>
          {message}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

export function copyText(value: string): void {
  navigator.clipboard?.writeText(value).catch(() => {});
}
