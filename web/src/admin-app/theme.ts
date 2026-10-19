// Design values for the /admin app (Happy Mates design system, see specs/admin-app/README.md).
import type { CSSProperties } from "react";

export type Tone = "green" | "red" | "orange" | "blue" | "dark" | "gray";

export const TONE: Record<Tone, [bg: string, fg: string]> = {
  green: ["rgba(22,163,74,0.12)", "#166534"],
  red: ["rgba(220,38,38,0.1)", "#b91c1c"],
  orange: ["rgba(249,115,22,0.12)", "#9a3412"],
  blue: ["rgba(37,99,235,0.1)", "#1d4ed8"],
  dark: ["#0f172a", "#ffffff"],
  gray: ["#f1f5f9", "#475569"],
};

export const RING: Record<number, [label: string, tone: Tone, who: string]> = {
  1: ["Internt", "red", "Kun brugere, der har sagt ja til tidlige features, eller har niveau 1."],
  2: ["Beta", "orange", "Som ring 1, eller brugere med niveau 2 eller lavere."],
  3: ["Preview", "blue", "Alle. Én info-besked pr. browser."],
  4: ["Stabilt", "green", "Alle, uden besked."],
};

export const AREA: Record<string, string> = { bruger: "Bruger", integration: "Integration", admin: "Admin", platform: "Platform" };

export const AVATAR = ["#E63946", "#F77F00", "#06D6A0", "#118AB2", "#2563eb", "#7c3aed"];

export const SANS = "var(--hm-font-sans)";
export const MONO = "var(--hm-font-mono)";

export const card: CSSProperties = { background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 16 };

export const input: CSSProperties = {
  height: 36, padding: "0 10px", border: "1px solid #e2e8f0", borderRadius: 8, font: `14px ${SANS}`, boxSizing: "border-box",
};

export const select: CSSProperties = { ...input, padding: "0 8px", background: "#ffffff" };

export const label: CSSProperties = { display: "flex", flexDirection: "column", gap: 6, fontSize: 13, fontWeight: 500 };

export const sectionLabel: CSSProperties = {
  fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "#64748b",
};

export const tableHead: CSSProperties = {
  display: "grid", gap: 10, alignItems: "center", padding: "8px 16px", borderBottom: "1px solid #e2e8f0", fontSize: 11,
  letterSpacing: "0.05em", textTransform: "uppercase", color: "#64748b",
};

export const fmt = (n: number) => new Intl.NumberFormat("da-DK").format(Math.round(n));

export function initials(name: string | null | undefined): string {
  return (name || "Uden navn").split(" ").filter(Boolean).map((s) => s[0]).slice(0, 2).join("").toUpperCase();
}

export function lastLabel(iso: string | null | undefined): string {
  if (!iso) return "Aldrig";
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((start(new Date()) - start(new Date(iso))) / 86_400_000);
  return days <= 0 ? "I dag" : days === 1 ? "I går" : `For ${days} dage siden`;
}

const MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

/** "3. mar 2026" */
export function dateLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getDate()}. ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/** +4522411890 → +45 22 41 18 90. Other numbers are shown as stored. */
export function phoneLabel(phone: string | null | undefined): string {
  if (!phone) return "";
  const m = /^\+45(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(phone);
  return m ? `+45 ${m[1]} ${m[2]} ${m[3]} ${m[4]}` : phone;
}

export const disabled =(off: boolean): CSSProperties => (off ? { opacity: 0.45, pointerEvents: "none" } : {});
