// Building blocks shared by /admin/mail and /admin/mail/users/:id.
import { useState, type CSSProperties, type ReactNode } from "react";
import { api, errorText } from "../../../api";
import { card, input, sectionLabel } from "../../theme";
import { useToast } from "../../ui";

export const muted: CSSProperties = { fontSize: 13, color: "#64748b", margin: 0 };
export const mono: CSSProperties = { fontFamily: "var(--hm-font-mono)", fontSize: 12.5 };
export const th: CSSProperties = { textAlign: "left", fontSize: 11, letterSpacing: "0.05em", textTransform: "uppercase", color: "#64748b", padding: "8px 12px", fontWeight: 600, whiteSpace: "nowrap" };
export const td: CSSProperties = { padding: "10px 12px", borderTop: "1px solid #e2e8f0", fontSize: 13, verticalAlign: "top" };

export type Person = { id: number; display_name: string | null; email: string | null; mailbox: string | null; mailbox_status: string | null };

export function Card({ title, aside, children, deeplink, style }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; deeplink?: string; style?: CSSProperties }) {
  return (
    <section data-deeplink={deeplink} style={{ ...card, padding: 18, display: "flex", flexDirection: "column", gap: 12, minWidth: 0, ...style }}>
      {(title || aside) && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          {title && <span style={{ fontSize: 15, fontWeight: 600 }}>{title}</span>}
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

export function Label({ children }: { children: ReactNode }) {
  return <span style={sectionLabel}>{children}</span>;
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead><tr>{head.map((h) => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** Runs a change, toasts the result, reloads. */
export function useRun(reload?: () => void) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (done) toast(done);
      reload?.();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run, setError };
}

/** Asks for the reason destructive actions need (at least a few words). */
export function askReason(what: string): string | null {
  const reason = window.prompt(`${what}\n\nSkriv en begrundelse (mindst et par ord). Den gemmes i audit-loggen.`);
  return reason && reason.trim() ? reason.trim() : null;
}

/** Search users by name, e-mail or mailbox address. */
export function PersonPicker({ onPick, placeholder = "Søg efter navn eller e-mail", needMailbox }: {
  onPick: (p: Person) => void; placeholder?: string; needMailbox?: boolean;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Person[]>([]);
  const search = async (value: string) => {
    setQ(value);
    if (value.trim().length < 2) return setHits([]);
    try {
      setHits(await api.get<Person[]>(`/api/admin/mail/people?q=${encodeURIComponent(value.trim())}`));
    } catch {
      setHits([]);
    }
  };
  return (
    <div style={{ position: "relative", flex: "1 1 240px", minWidth: 0 }}>
      <input aria-label={placeholder} placeholder={placeholder} value={q} onChange={(e) => search(e.target.value)} style={{ ...input, width: "100%" }} />
      {hits.length > 0 && (
        <div role="listbox" style={{ position: "absolute", zIndex: 20, top: 40, left: 0, right: 0, background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, boxShadow: "var(--hm-shadow-lg)", maxHeight: 260, overflowY: "auto" }}>
          {hits.map((p) => {
            const off = needMailbox && p.mailbox_status !== "active";
            return (
              <button key={p.id} type="button" role="option" aria-selected={false} disabled={off} className="tg-hover"
                onClick={() => { onPick(p); setQ(""); setHits([]); }}
                style={{ all: "unset", display: "flex", flexDirection: "column", padding: "8px 12px", cursor: off ? "not-allowed" : "pointer", opacity: off ? 0.5 : 1, width: "calc(100% - 24px)" }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{p.display_name || p.email}</span>
                <span style={{ fontSize: 12, color: "#64748b" }}>{p.mailbox ?? "ingen postkasse"} · {p.email}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export const SHARED_ROLES: [string, string][] = [["reader", "Læser"], ["sender", "Afsender"], ["manager", "Ansvarlig"]];
export const COLL_ROLES: [string, string][] = [["viewer", "Se"], ["editor", "Redigere"], ["manager", "Ansvarlig"]];
export const STATUS_LABEL: Record<string, [string, "green" | "orange" | "red" | "gray" | "blue"]> = {
  active: ["Aktiv", "green"], provisioning: ["Sættes op", "blue"], suspended: ["Suspenderet", "orange"], deleted: ["Slettet", "gray"],
  pending: ["Afventer DNS", "orange"], verified: ["Verificeret", "green"],
};
