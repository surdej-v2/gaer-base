// /admin/clickhouse → ClickHouse. Monitoring of the ClickHouse server in the stack, read through /api/admin/clickhouse:
// fixed, read-only queries (no SQL from the browser) plus the results of the job clickhouse.monitor (every 5 min).
// Tabs: Overblik · Tjek · Tabeller · Forespørgsler · Mutationer · Fejl · Langsomme. ?tab=… is the deep link.
import { useState, type CSSProperties, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Database, RefreshCw } from "lucide-react";
import { api } from "../../api";
import { card, fmt, MONO, sectionLabel, type Tone } from "../theme";
import { Btn, Empty, ErrorBox, fmtBytes, fmtDate, PageTitle, Pill, useLoad } from "../ui";

type Status = "ok" | "warn" | "error";
type Check = { key: string; label: string; status: Status; detail: string };
type CheckRow = { id: number; checked_at: string; status: Status; version?: string | null; checks?: Check[] };
type Row = Record<string, unknown>;
type Overview = {
  configured: boolean; url: string | null; database: string; user: string; reachable: boolean; error: string | null;
  fetched_at: string; views: { key: string; title: string }[];
  latest_check: CheckRow | null; history: CheckRow[];
  server?: { version: string; uptime: string; timezone: string; database: string } | null;
  disks?: { name: string; path: string; free_space: number; total_space: number }[] | null;
  metrics?: Record<string, number>;
  tables?: Row[] | null;
  pending_migrations?: string[] | null;
};
type ViewResult = { view: string; title: string; fetched_at: string; rows: Row[] };

type Tab = "overblik" | "tjek" | "tables" | "queries" | "mutations" | "errors" | "slow";
const TABS: [Tab, string][] = [
  ["overblik", "Overblik"], ["tjek", "Tjek"], ["tables", "Tabeller"], ["queries", "Forespørgsler"],
  ["mutations", "Mutationer"], ["errors", "Fejl"], ["slow", "Langsomme"],
];

const STATUS: Record<Status, [Tone, string]> = { ok: ["green", "OK"], warn: ["orange", "Advarsel"], error: ["red", "Fejl"] };
const BAR: Record<Status, string> = { ok: "#16a34a", warn: "#f59e0b", error: "#dc2626" };
const BYTES = new Set(["bytes", "memory_usage", "free_space", "total_space"]);
const cardTitle: CSSProperties = { fontSize: 15, fontWeight: 600 };
const muted: CSSProperties = { fontSize: 13, color: "#64748b" };

export default function ClickHouse() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some(([k]) => k === params.get("tab")) ? params.get("tab") : "overblik") as Tab;
  const [tick, setTick] = useState(0);
  // Each request is an admin.read line in the audit log, so the page refreshes on demand, not on a timer.
  const overview = useLoad(() => api.get<Overview>("/api/admin/clickhouse"), [tab === "overblik" || tab === "tjek" ? tick : -1]);
  const o = overview.data;

  const title = (
    <PageTitle title="ClickHouse" icon={<Database size={24} />} aside={o?.configured && (
      <Btn icon={RefreshCw} height={32} onClick={() => setTick((t) => t + 1)}>Opdater</Btn>
    )}>
      Kolonnedatabasen i stakken. Tallene læses med faste forespørgsler gennem appen; siden ændrer ikke noget i ClickHouse.
      Jobbet <span style={{ fontFamily: MONO }}>clickhouse.monitor</span> tjekker serveren hvert 5. minut.
    </PageTitle>
  );

  if (overview.error) return <>{title}<ErrorBox>{overview.error}</ErrorBox></>;
  if (!o) return title;
  if (!o.configured) return <>{title}<NotConfigured /></>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {title}
      <div role="tablist" aria-label="ClickHouse" style={{ display: "flex", gap: 22, borderBottom: "1px solid #e2e8f0", overflowX: "auto" }}>
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} data-deeplink={`clickhouse-tab-${key}`}
            onClick={() => setParams(key === "overblik" ? {} : { tab: key }, { replace: true })}
            style={{ all: "unset", cursor: "pointer", padding: "10px 0", fontSize: 14, whiteSpace: "nowrap", fontWeight: tab === key ? 600 : 500,
              color: tab === key ? "#020817" : "#64748b", borderBottom: tab === key ? "2px solid #2563eb" : "2px solid transparent", marginBottom: -1 }}>
            {label}
          </button>
        ))}
      </div>
      {tab === "overblik" && <Overblik o={o} />}
      {tab === "tjek" && <Checks o={o} tick={tick} />}
      {tab !== "overblik" && tab !== "tjek" && <View view={tab} tick={tick} />}
    </div>
  );
}

function NotConfigured() {
  return (
    <div style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 10, maxWidth: 760 }}>
      <span style={cardTitle}>ClickHouse er ikke sat op</span>
      <p style={{ margin: 0, ...muted }}>
        Appen kender ikke ClickHouse. I produktion sætter <code>deploy/compose.yml</code> forbindelsen selv. Lokalt: start
        ClickHouse med <code>docker compose up -d</code>, sæt følgende i <code>.env</code> og kør <code>python -m app.migrate</code>:
      </p>
      <pre style={{ margin: 0, padding: 12, borderRadius: 8, background: "#f8fafc", font: `13px ${MONO}`, overflowX: "auto" }}>
        CLICKHOUSE_URL=http://localhost:8123{"\n"}CLICKHOUSE_USER=gaer{"\n"}CLICKHOUSE_PASSWORD=gaer{"\n"}CLICKHOUSE_DATABASE=gaer
      </pre>
    </div>
  );
}

// ── Overblik ────────────────────────────────────────────────────────────────

function Overblik({ o }: { o: Overview }) {
  const latest = o.latest_check;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {!o.reachable && (
        <ErrorBox>{o.error ?? "ClickHouse svarer ikke."} Adresse: <span style={{ fontFamily: MONO }}>{o.url}</span></ErrorBox>
      )}

      <div data-deeplink="clickhouse-status" style={{ ...card, padding: "14px 20px", display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        {latest ? <Pill tone={STATUS[latest.status][0]}>{STATUS[latest.status][1]}</Pill> : <Pill tone="gray">Ikke tjekket endnu</Pill>}
        <span style={{ fontWeight: 600 }}>{o.server ? `ClickHouse ${o.server.version}` : "ClickHouse"}</span>
        {o.server && <span style={muted}>oppe {o.server.uptime} · tidszone {o.server.timezone} · database {o.database} · bruger {o.user}</span>}
        <span style={{ ...muted, marginLeft: "auto", fontFamily: MONO }}>{o.url}</span>
      </div>

      {o.reachable && <Kpis o={o} />}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 14 }}>
        <Panel title="Seneste tjek" deeplink="clickhouse-latest-check">
          {!latest ? <span style={muted}>Jobbet clickhouse.monitor har ikke kørt endnu. Det kører hvert 5. minut (se <Link to="/jobs">Jobs og oprydning</Link>).</span> : (
            <>
              <span style={muted}>{fmtDate(latest.checked_at)}</span>
              {(latest.checks ?? []).map((c) => <CheckLine key={c.key} c={c} />)}
            </>
          )}
        </Panel>

        <Panel title="Diske" deeplink="clickhouse-disks">
          {!o.disks?.length ? <span style={muted}>Ingen data.</span> : o.disks.map((d) => (
            <Usage key={d.name} label={`${d.name} · ${d.path}`} used={d.total_space - d.free_space} max={d.total_space} />
          ))}
        </Panel>

        <Panel title="Skema og oprydning" deeplink="clickhouse-schema">
          <Line label="Migrationer">
            {o.pending_migrations == null ? "—" : o.pending_migrations.length
              ? <Pill tone="orange">{o.pending_migrations.length} mangler</Pill> : <Pill tone="green">Alle kørt</Pill>}
          </Line>
          {!!o.pending_migrations?.length && <span style={{ ...muted, fontFamily: MONO }}>{o.pending_migrations.join(", ")}</span>}
          <span style={muted}>
            Gamle rækker i <span style={{ fontFamily: MONO }}>events</span>, ClickHouses systemlogge og tjek-historikken slettes efter
            opbevaringsreglerne <span style={{ fontFamily: MONO }}>clickhouse_*</span> under <Link to="/jobs">Jobs og oprydning</Link>.
          </span>
        </Panel>
      </div>

      <History rows={o.history} />

      {!!o.tables?.length && (
        <Panel title="Største tabeller" deeplink="clickhouse-tables-top">
          <DataTable rows={o.tables} />
        </Panel>
      )}
    </div>
  );
}

function Kpis({ o }: { o: Overview }) {
  const m = o.metrics ?? {};
  const memTotal = m.OSMemoryTotal ?? 0;
  const kpis = [
    { label: "Kørende forespørgsler", value: fmt(m.Query ?? 0), sub: `${fmt(m.BackgroundMergesAndMutationsPoolTask ?? 0)} merges og mutationer i baggrunden` },
    { label: "Forbindelser", value: fmt((m.TCPConnection ?? 0) + (m.HTTPConnection ?? 0)), sub: `${fmt(m.HTTPConnection ?? 0)} HTTP · ${fmt(m.TCPConnection ?? 0)} native` },
    { label: "Hukommelse", value: fmtBytes(m.MemoryTracking ?? 0), sub: memTotal ? `af ${fmtBytes(memTotal)} på maskinen · ${fmtBytes(m.OSMemoryAvailable ?? 0)} fri` : "brugt af ClickHouse" },
    { label: "Tabeller", value: fmt(m.NumberOfTables ?? 0), sub: `${fmt(m.NumberOfDatabases ?? 0)} databaser · højst ${fmt(m.MaxPartCountForPartition ?? 0)} parts i én partition` },
  ];
  return (
    <div data-deeplink="clickhouse-kpis" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
      {kpis.map((k) => (
        <div key={k.label} style={{ ...card, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={sectionLabel}>{k.label}</span>
          <span style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>{k.value}</span>
          <span style={{ fontSize: 12, color: "#64748b" }}>{k.sub}</span>
        </div>
      ))}
    </div>
  );
}

/** One bar per run of the monitor, oldest first; colour = status. */
function History({ rows }: { rows: CheckRow[] }) {
  const runs = [...rows].reverse();
  const counts = { ok: 0, warn: 0, error: 0 };
  runs.forEach((r) => { counts[r.status] += 1; });
  return (
    <Panel title="Status de seneste 24 timer" deeplink="clickhouse-history">
      {!runs.length ? <span style={muted}>Ingen tjek endnu.</span> : (
        <>
          <div role="img" aria-label={`${counts.ok} ok, ${counts.warn} advarsler, ${counts.error} fejl`}
            style={{ display: "flex", gap: 2, height: 28, alignItems: "stretch" }}>
            {runs.map((r) => (
              <span key={r.id} title={`${fmtDate(r.checked_at)} · ${STATUS[r.status][1]}`}
                style={{ flex: "1 1 0", minWidth: 2, maxWidth: 10, borderRadius: 2, background: BAR[r.status] }} />
            ))}
          </div>
          <span style={muted}>{counts.ok} ok · {counts.warn} advarsler · {counts.error} fejl · {runs.length} tjek</span>
        </>
      )}
    </Panel>
  );
}

// ── Tjek ────────────────────────────────────────────────────────────────────

function Checks({ o, tick }: { o: Overview; tick: number }) {
  const res = useLoad(() => api.get<ViewResult>("/api/admin/clickhouse/checks"), [tick]);
  if (res.error) return <ErrorBox>{res.error}</ErrorBox>;
  const rows = (res.data?.rows ?? []) as CheckRow[];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <History rows={o.history} />
      {!rows.length ? <Empty>Ingen tjek endnu.</Empty> : rows.map((r) => (
        <div key={r.id} style={{ ...card, padding: "14px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <Pill tone={STATUS[r.status][0]}>{STATUS[r.status][1]}</Pill>
            <span style={{ fontWeight: 600 }}>{fmtDate(r.checked_at)}</span>
            {r.version && <span style={muted}>ClickHouse {r.version}</span>}
          </div>
          {(r.checks ?? []).filter((c) => c.status !== "ok" || r.status === "ok").map((c) => <CheckLine key={c.key} c={c} />)}
        </div>
      ))}
    </div>
  );
}

function CheckLine({ c }: { c: Check }) {
  return (
    <div style={{ display: "flex", gap: 10, fontSize: 13, alignItems: "baseline" }}>
      <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 9999, background: BAR[c.status], flex: "none", alignSelf: "center" }} />
      <span style={{ fontWeight: 600, flex: "none" }}>{c.label}</span>
      <span style={{ color: "#64748b", minWidth: 0 }}>{c.detail}</span>
    </div>
  );
}

// ── Fixed views ─────────────────────────────────────────────────────────────

function View({ view, tick }: { view: Tab; tick: number }) {
  const res = useLoad(() => api.get<ViewResult>(`/api/admin/clickhouse/${view}`), [view, tick]);
  if (res.error) return <ErrorBox>{res.error}</ErrorBox>;
  if (!res.data) return null;
  return (
    <Panel title={res.data.title} deeplink={`clickhouse-view-${view}`}>
      <span style={muted}>Hentet {fmtDate(res.data.fetched_at)} · {fmt(res.data.rows.length)} rækker</span>
      {res.data.rows.length ? <DataTable rows={res.data.rows} /> : <Empty>Intet at vise.</Empty>}
    </Panel>
  );
}

function cell(key: string, value: unknown): ReactNode {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return BYTES.has(key) ? fmtBytes(value) : value.toLocaleString("da-DK", { maximumFractionDigits: 1 });
  if (typeof value === "boolean") return value ? "ja" : "nej";
  return String(value);
}

function DataTable({ rows }: { rows: Row[] }) {
  const cols = Object.keys(rows[0] ?? {});
  return (
    <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>{cols.map((c) => (
            <th key={c} style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, letterSpacing: "0.05em", textTransform: "uppercase",
              color: "#64748b", background: "#f8fafc", whiteSpace: "nowrap" }}>{c}</th>
          ))}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => (
              <td key={c} style={{ padding: "8px 12px", borderTop: "1px solid #e2e8f0", verticalAlign: "top",
                fontFamily: c === "query" || c === "command" ? MONO : undefined, fontVariantNumeric: "tabular-nums",
                whiteSpace: c === "query" || c === "command" || c === "message" || c === "latest_fail_reason" ? "normal" : "nowrap",
                wordBreak: "break-word", maxWidth: 520 }}>{cell(c, r[c])}</td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Small parts ─────────────────────────────────────────────────────────────

function Panel({ title, deeplink, children }: { title: string; deeplink?: string; children: ReactNode }) {
  return (
    <div data-deeplink={deeplink} style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
      <span style={cardTitle}>{title}</span>
      {children}
    </div>
  );
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 12, fontSize: 13, alignItems: "baseline" }}>
      <span style={{ color: "#64748b", flex: "none" }}>{label}</span>
      <span style={{ marginLeft: "auto", textAlign: "right", minWidth: 0 }}>{children}</span>
    </div>
  );
}

function Usage({ label, used, max }: { label: string; used: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0;
  const color = pct > 90 ? "#dc2626" : pct > 80 ? "#f59e0b" : "#16a34a";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <div style={{ display: "flex", fontSize: 13, gap: 8 }}>
        <span style={{ color: "#64748b", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
          {fmtBytes(used)} af {fmtBytes(max)} · {pct.toLocaleString("da-DK", { maximumFractionDigits: 0 })} %
        </span>
      </div>
      <div style={{ height: 6, borderRadius: 9999, background: "#f1f5f9", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 9999 }} />
      </div>
    </div>
  );
}
