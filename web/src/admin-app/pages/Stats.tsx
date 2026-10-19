// /admin → Statistik (screen 01). Data from /api/admin/analytics, period from the top bar.
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import { useLoad } from "../../ui";
import { useAdmin } from "../context";
import { card, fmt, MONO, RING, SANS, TONE } from "../theme";
import { ErrorBox, PageTitle } from "../ui";

type Stats = {
  days: number;
  totals: { pageviews: number; anonymous_pageviews: number };
  per_day: { day: string; pageviews: number }[];
  pages: { path: string; pageviews: number }[];
  users: { total: number; new: number; active: number; with_passkey: number; must_passkey: number };
  logins: { passkey: number; otp: number; failed: number; failed_24h: number };
  logins_per_day: { day: string; logins: number }[];
  features?: { key: string; title: string; ring: number; uses: number }[];
};

const dayFmt = new Intl.DateTimeFormat("da-DK", { day: "numeric", month: "short" });
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const cardTitle = { fontSize: 15, fontWeight: 600 } as const;
const linkBtn = { border: 0, background: "transparent", cursor: "pointer", color: "#2563eb", font: `500 12px ${SANS}`, padding: 0 } as const;

export default function Stats() {
  const { range } = useAdmin();
  const navigate = useNavigate();
  const { data, error } = useLoad(() => api.get<Stats>(`/api/admin/analytics?days=${range}`), [range]);

  const title = (
    <PageTitle title="Statistik">
      Opsamlet af containerens egen server og gemt i dens Postgres. Ingen tredjepart. Besøgende tælles kun med samtykke.
    </PageTitle>
  );
  if (error) return <>{title}<ErrorBox>{error}</ErrorBox></>;
  if (!data) return title;

  const R = data.days;
  const pvBy = new Map(data.per_day.map((d) => [String(d.day).slice(0, 10), d.pageviews]));
  const lgBy = new Map(data.logins_per_day.map((d) => [String(d.day).slice(0, 10), d.logins]));
  const days = Array.from({ length: R }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (R - 1 - i));
    return { date: d, pv: pvBy.get(isoDay(d)) ?? 0, lg: lgBy.get(isoDay(d)) ?? 0 };
  });
  const maxPv = Math.max(1, ...days.map((d) => d.pv));
  const maxLg = Math.max(1, ...days.map((d) => d.lg));

  const u = data.users, l = data.logins;
  const logins = l.passkey + l.otp;
  const kpis = [
    { label: "Brugere", value: fmt(u.total), sub: `+${u.new} nye på ${R} dage`, fg: "#166534" },
    { label: "Aktive brugere", value: fmt(u.active), sub: `${u.total ? Math.round((u.active / u.total) * 100) : 0} % logget ind på ${R} dage`, fg: "#64748b" },
    { label: "Sidevisninger", value: fmt(data.totals.pageviews), sub: `${fmt(data.totals.anonymous_pageviews)} anonyme uden samtykke`, fg: "#64748b" },
    { label: "Logins", value: fmt(logins), sub: `${l.failed} fejlede · ${l.failed_24h} seneste døgn`, fg: "#b91c1c" },
  ];

  const features = (data.features ?? []).slice(0, 5);
  const featMax = Math.max(1, ...features.map((f) => f.uses));
  const pages = data.pages.slice(0, 6);
  const pageMax = Math.max(1, ...pages.map((p) => p.pageviews));
  const all = logins + l.failed || 1;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {title}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
        {kpis.map((k) => (
          <div key={k.label} style={{ ...card, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "#64748b" }}>{k.label}</span>
            <span style={{ fontSize: 30, fontWeight: 700, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>{k.value}</span>
            <span style={{ fontSize: 12, color: k.fg }}>{k.sub}</span>
          </div>
        ))}
      </div>

      <div style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <span style={cardTitle}>Aktivitet pr. dag</span>
          <Legend color="#2563eb">Sidevisninger</Legend>
          <Legend color="#f59e0b">Logins</Legend>
        </div>
        <div style={{ height: 180, display: "flex", alignItems: "flex-end", gap: R > 60 ? 1 : R > 20 ? 3 : 8, borderBottom: "1px solid #e2e8f0" }}>
          {days.map((d) => (
            <div key={d.date.toISOString()} title={`${dayFmt.format(d.date)}: ${fmt(d.pv)} sidevisninger, ${d.lg} logins`}
              style={{ flex: 1, minWidth: 0, height: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: 2 }}>
              <div style={{ height: `${Math.round((d.pv / maxPv) * 72)}%`, background: "#2563eb", borderRadius: "3px 3px 0 0", opacity: 0.85 }} />
              <div style={{ height: `${d.lg ? Math.max(3, Math.round((d.lg / maxLg) * 22)) : 0}%`, background: "#f59e0b", borderRadius: 2 }} />
            </div>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#64748b", fontVariantNumeric: "tabular-nums" }}>
          <span>{dayFmt.format(days[0].date)}</span><span>I dag</span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 14 }}>
        <div style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center" }}>
            <span style={cardTitle}>Mest brugte features</span>
            <button type="button" onClick={() => navigate("/features")} style={{ ...linkBtn, marginLeft: "auto" }}>Features og ringe →</button>
          </div>
          {features.length === 0 && <span style={{ fontSize: 13, color: "#64748b" }}>Ingen brug registreret endnu.</span>}
          {features.map((f) => {
            const [bg, fg] = TONE[RING[f.ring]?.[1] ?? "gray"];
            return (
              <div key={f.key} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                  <span style={{ fontWeight: 500 }}>{f.title}</span>
                  <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 7px", borderRadius: 9999, background: bg, color: fg }}>R{f.ring}</span>
                  <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums", color: "#64748b" }}>{fmt(f.uses)}</span>
                </div>
                <Bar pct={(f.uses / featMax) * 100} fill="linear-gradient(90deg,#f97316,#f59e0b)" />
              </div>
            );
          })}
        </div>

        <div style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
          <span style={cardTitle}>Sider</span>
          {pages.length === 0 && <span style={{ fontSize: 13, color: "#64748b" }}>Ingen sidevisninger i perioden.</span>}
          {pages.map((p) => (
            <div key={p.path} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              <div style={{ display: "flex", fontSize: 13 }}>
                <span style={{ fontFamily: MONO }}>{p.path}</span>
                <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums", color: "#64748b" }}>{fmt(p.pageviews)}</span>
              </div>
              <Bar pct={(p.pageviews / pageMax) * 100} fill="#2563eb" />
            </div>
          ))}
        </div>

        <div style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
          <span style={cardTitle}>Login</span>
          <div style={{ display: "flex", height: 10, borderRadius: 9999, overflow: "hidden", background: "#f1f5f9" }}>
            <div style={{ width: `${(l.passkey / all) * 100}%`, background: "#16a34a" }} />
            <div style={{ width: `${(l.otp / all) * 100}%`, background: "#2563eb" }} />
            <div style={{ width: `${(l.failed / all) * 100}%`, background: "#dc2626" }} />
          </div>
          {([["#16a34a", "Passkey", l.passkey], ["#2563eb", "Engangskode på SMS", l.otp], ["#dc2626", "Fejlede", l.failed]] as const).map(([c, label, v]) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
              <span style={{ width: 10, height: 10, borderRadius: 3, background: c }} />
              <span>{label}</span>
              <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums", color: "#64748b" }}>{fmt(v)}</span>
            </div>
          ))}
          <div style={{ paddingTop: 12, borderTop: "1px solid #e2e8f0", display: "flex", flexDirection: "column", gap: 8, fontSize: 13 }}>
            <div style={{ display: "flex" }}>
              <span style={{ color: "#64748b" }}>Brugere med passkey</span>
              <span style={{ marginLeft: "auto", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{u.with_passkey} af {u.total}</span>
            </div>
            <div style={{ display: "flex" }}>
              <span style={{ color: "#64748b" }}>Skal oprette passkey</span>
              <span style={{ marginLeft: "auto", fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "#9a3412" }}>{u.must_passkey}</span>
            </div>
            <button type="button" onClick={() => navigate("/users?filter=nopk")} style={{ ...linkBtn, alignSelf: "flex-start" }}>Se brugere uden passkey →</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Legend({ color, children }: { color: string; children: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "#64748b" }}>
      <span style={{ width: 10, height: 10, borderRadius: 3, background: color }} />{children}
    </span>
  );
}

function Bar({ pct, fill }: { pct: number; fill: string }) {
  return (
    <div style={{ height: 6, borderRadius: 9999, background: "#f1f5f9", overflow: "hidden" }}>
      <div style={{ height: "100%", width: `${Math.round(pct)}%`, background: fill, borderRadius: 9999 }} />
    </div>
  );
}
