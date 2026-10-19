import { useState } from "react";
import { api } from "../../api";
import { Alert, fmtDay, fmtNumber, Loading, PageHeader, useLoad } from "../../ui";

type Stats = {
  days: number;
  totals: { pageviews: number; visitors: number; visits: number; anonymous_pageviews: number };
  per_day: { day: string; pageviews: number; visits: number }[];
  pages: { path: string; pageviews: number; visitors: number }[];
  sources: { source: string; pageviews: number }[];
  devices: { device: string; pageviews: number }[];
  browsers: { browser: string; pageviews: number }[];
  flows: { from: string; to: string; n: number }[];
  entries: { path: string; visits: number }[];
  consent: { accepted: number; declined: number };
};

const DEVICE: Record<string, string> = { desktop: "Computer", mobile: "Mobil", tablet: "Tablet", ukendt: "Ukendt" };

function Breakdown<T>({ title, rows, label, value }: { title: string; rows: T[]; label: (r: T) => string; value: (r: T) => number }) {
  const max = Math.max(1, ...rows.map(value));
  return (
    <div className="card">
      <span className="eyebrow">{title}</span>
      {rows.length === 0 && <p className="muted small">Ingen data endnu.</p>}
      {rows.map((r, i) => (
        <div key={i} className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
          <div className="spread small"><span className="mono" style={{ wordBreak: "break-all" }}>{label(r)}</span><span className="num">{fmtNumber(value(r))}</span></div>
          <div className="bar"><div style={{ width: `${(value(r) / max) * 100}%` }} /></div>
        </div>
      ))}
    </div>
  );
}

export default function Analytics() {
  const [days, setDays] = useState(30);
  const stats = useLoad(() => api.get<Stats>(`/api/admin/analytics?days=${days}`), [days]);
  const s = stats.data;
  const maxDay = Math.max(1, ...(s?.per_day.map((d) => d.pageviews) ?? [0]));
  const consentTotal = (s?.consent.accepted ?? 0) + (s?.consent.declined ?? 0);

  return (
    <>
      <div className="spread">
        <PageHeader eyebrow="/admin/analytics" title="Analytics">
          Opsamlet af vores egen server og gemt i containerens Postgres. Ingen Google Analytics og ingen tredjepart.
          Besøg, forløb og besøgende tælles kun for dem, der har givet samtykke. Øvrige sidevisninger er anonyme.
        </PageHeader>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Periode">
          <option value={7}>7 dage</option><option value={30}>30 dage</option><option value={90}>90 dage</option><option value={365}>12 måneder</option>
        </select>
      </div>
      <Alert>{stats.error}</Alert>
      {!s ? <Loading /> : (
        <>
          <div className="grid" style={{ "--min": "180px" } as React.CSSProperties}>
            <div className="card"><span className="eyebrow">Sidevisninger</span><span className="stat">{fmtNumber(s.totals.pageviews)}</span>
              <span className="muted tiny">{fmtNumber(s.totals.anonymous_pageviews)} anonyme</span></div>
            <div className="card"><span className="eyebrow">Besøg</span><span className="stat">{fmtNumber(s.totals.visits)}</span></div>
            <div className="card"><span className="eyebrow">Besøgende</span><span className="stat">{fmtNumber(s.totals.visitors)}</span></div>
            <div className="card"><span className="eyebrow">Samtykke</span>
              <span className="stat">{consentTotal ? `${Math.round((s.consent.accepted / consentTotal) * 100)} %` : "—"}</span>
              <span className="muted tiny">{fmtNumber(s.consent.accepted)} ja · {fmtNumber(s.consent.declined)} nej</span></div>
          </div>
          <div className="card">
            <span className="eyebrow">Sidevisninger pr. dag</span>
            <div className="chart">
              {s.per_day.map((d) => (
                <div key={d.day} style={{ height: `${(d.pageviews / maxDay) * 100}%` }} title={`${fmtDay(d.day)}: ${d.pageviews} sidevisninger, ${d.visits} besøg`} />
              ))}
            </div>
            {s.per_day.length > 0 && <div className="spread tiny muted"><span>{fmtDay(s.per_day[0].day)}</span><span>{fmtDay(s.per_day[s.per_day.length - 1].day)}</span></div>}
          </div>
          <div className="grid" style={{ "--min": "320px" } as React.CSSProperties}>
            <Breakdown title="Sider" rows={s.pages} label={(r) => r.path} value={(r) => r.pageviews} />
            <Breakdown title="Kilder" rows={s.sources} label={(r) => r.source} value={(r) => r.pageviews} />
            <Breakdown title="Enheder" rows={s.devices} label={(r) => DEVICE[r.device] ?? r.device} value={(r) => r.pageviews} />
            <Breakdown title="Browsere" rows={s.browsers} label={(r) => r.browser} value={(r) => r.pageviews} />
            <Breakdown title="Indgangssider" rows={s.entries} label={(r) => r.path} value={(r) => r.visits} />
            <Breakdown title="Forløb" rows={s.flows} label={(r) => `${r.from} → ${r.to}`} value={(r) => r.n} />
          </div>
        </>
      )}
    </>
  );
}
