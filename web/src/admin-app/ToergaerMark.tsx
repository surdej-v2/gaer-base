// The Tørgær mark (specs/admin-app/design/ToergaerMark.dc.html), variant a: the smiling dough ball.
import { useId } from "react";

export default function ToergaerMark({ size = 28, wordmark = true, byline = false, dark = false }: {
  size?: number; wordmark?: boolean; byline?: boolean; dark?: boolean;
}) {
  const gradient = useId();
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: Math.round(size * 0.28), fontFamily: "var(--hm-font-sans)" }}>
      <svg viewBox="0 0 100 100" width={size} height={size} style={{ display: "block", flex: "none" }} aria-hidden="true">
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#E63946" /><stop offset="0.25" stopColor="#F77F00" /><stop offset="0.5" stopColor="#FCBF49" />
            <stop offset="0.75" stopColor="#06D6A0" /><stop offset="1" stopColor="#118AB2" />
          </linearGradient>
        </defs>
        <circle cx="44" cy="56" r="38" fill={`url(#${gradient})`} />
        <circle cx="44" cy="56" r="25" fill="#ffffff" />
        <circle cx="38" cy="50" r="3.6" fill="#020817" />
        <circle cx="50" cy="47" r="3.6" fill="#020817" />
        <path d="M 35 62 Q 44 70 53 62" stroke="#020817" strokeWidth="3.6" fill="none" strokeLinecap="round" />
        <circle cx="86" cy="22" r="7" fill="#F77F00" />
        <circle cx="73" cy="9" r="4.5" fill="#FCBF49" />
        <circle cx="94" cy="7" r="3" fill="#E63946" />
      </svg>
      {wordmark && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, lineHeight: 1 }}>
          <span className="tg-rainbow-text" style={{ fontSize: Math.round(size * 0.5), fontWeight: 800, letterSpacing: "0.04em" }}>TØRGÆR</span>
          {byline && (
            <span style={{ fontSize: Math.max(10, Math.round(size * 0.26)), fontWeight: 600, color: dark ? "#cbd5e1" : "#64748b", letterSpacing: "0.01em" }}>
              by Happy Mates
            </span>
          )}
        </div>
      )}
    </div>
  );
}
