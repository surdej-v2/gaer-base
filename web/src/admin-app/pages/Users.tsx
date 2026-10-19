// /admin/users → Brugere og roller (screen 02). Clicking a row opens the drawer (screen 03, ?user=<id>).
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { api } from "../../api";
import { useLoad } from "../../ui";
import { card, SANS, tableHead } from "../theme";
import { Empty, ErrorBox, PageTitle, Pill } from "../ui";
import { isTest, userView, type AdminUser, type UsersResponse } from "../users";
import UserDrawer from "./UserDrawer";

const FILTERS: [key: string, label: string, test: (u: AdminUser) => boolean][] = [
  ["all", "Alle", () => true],
  ["active", "Aktive", (u) => !u.blocked_at],
  ["blocked", "Spærrede", (u) => !!u.blocked_at],
  ["nopk", "Uden passkey", (u) => u.passkeys === 0 && !isTest(u)],
  ["admins", "Admins", (u) => u.role === "admin"],
  ["test", "Testkonti", isTest],
];
const COLS = "minmax(240px,1.6fr) 110px 170px 130px 100px 40px";

export default function Users() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const { data, error, reload } = useLoad(() => api.get<UsersResponse>("/api/admin/users?limit=500"), []);
  const filter = params.get("filter") ?? "all";
  const selected = Number(params.get("user")) || null;

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: key === "filter" });
  };

  const users = data?.items ?? [];
  const grace = data?.grace_logins ?? 3;
  const fn = (FILTERS.find((f) => f[0] === filter) ?? FILTERS[0])[2];
  const needle = q.trim().toLowerCase().replace(/^\+?45\s?/, "");
  const rows = users.filter(fn).filter((u) => !needle || `${u.display_name ?? ""} ${u.phone}`.toLowerCase().replace(/\s/g, "").includes(needle.replace(/\s/g, "")));
  const sel = users.find((u) => u.id === selected) ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageTitle title="Brugere og roller">
        Den første bruger på en ny instans bliver admin. Der skal altid være mindst én admin. Alt her skrives i audit-loggen.
      </PageTitle>
      {error && <ErrorBox>{error}</ErrorBox>}
      <div style={{ ...card, overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "10px 12px", borderBottom: "1px solid #e2e8f0" }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Søg på navn eller telefonnummer"
            style={{ flex: "1 1 240px", minWidth: 0, height: 34, padding: "0 10px", border: "1px solid #e2e8f0", borderRadius: 8, font: `14px ${SANS}`, boxSizing: "border-box" }} />
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {FILTERS.map(([key, label, f]) => {
              const on = filter === key;
              return (
                <button key={key} type="button" onClick={() => setParam("filter", key === "all" ? null : key)}
                  style={{ cursor: "pointer", height: 28, padding: "0 10px", borderRadius: 9999, border: `1px solid ${on ? "#0f172a" : "#e2e8f0"}`,
                    background: on ? "#0f172a" : "#ffffff", color: on ? "#ffffff" : "#475569", font: `500 12px ${SANS}`, whiteSpace: "nowrap" }}>
                  {label} · {users.filter(f).length}
                </button>
              );
            })}
          </div>
        </div>
        <div style={{ overflowX: "auto" }}>
          <div style={{ minWidth: 860 }}>
            <div style={{ ...tableHead, gridTemplateColumns: COLS }}>
              <span>Bruger</span><span>Rolle</span><span>Login</span><span>Sidst logget ind</span><span>Status</span><span />
            </div>
            {rows.map((u) => {
              const v = userView(u, grace);
              return (
                <div key={u.id} role="button" tabIndex={0} className="tg-row" onClick={() => setParam("user", String(u.id))}
                  onKeyDown={(e) => e.key === "Enter" && setParam("user", String(u.id))}
                  style={{ display: "grid", gridTemplateColumns: COLS, gap: 10, alignItems: "center", padding: "9px 16px", borderBottom: "1px solid #f1f5f9",
                    fontSize: 13, cursor: "pointer", background: selected === u.id ? "#f8fafc" : "#ffffff" }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                    <span style={{ width: 30, height: 30, flex: "none", borderRadius: 9999, background: v.avatar, color: "#ffffff", display: "flex",
                      alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700 }}>{v.initials}</span>
                    <span style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{ fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{v.name}</span>
                        {isTest(u) && <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 7px", borderRadius: 9999, background: "rgba(245,158,11,0.15)", color: "#92400e" }}>Testkonto</span>}
                      </span>
                      <span style={{ fontSize: 12, color: "#64748b", fontVariantNumeric: "tabular-nums" }}>{v.sub}</span>
                    </span>
                  </span>
                  <span><Pill tone={v.role[1]}>{v.role[0]}</Pill></span>
                  <span style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "flex-start" }}>
                    <Pill tone={v.login[1]}>{v.login[0]}</Pill>
                    <span style={{ fontSize: 11, color: "#64748b" }}>{v.sessions}</span>
                  </span>
                  <span style={{ fontSize: 12, color: "#64748b" }}>{v.last}</span>
                  <span><Pill tone={v.status[1]}>{v.status[0]}</Pill></span>
                  <ChevronRight size={16} color="#94a3b8" strokeWidth={2} />
                </div>
              );
            })}
            {data && rows.length === 0 && <Empty>Ingen brugere matcher.</Empty>}
          </div>
        </div>
        <div style={{ padding: "10px 16px", fontSize: 12, color: "#64748b" }}>{rows.length} af {users.length} brugere</div>
      </div>
      {sel && <UserDrawer user={sel} users={users} grace={grace} onClose={() => setParam("user", null)} onChanged={reload} />}
    </div>
  );
}
