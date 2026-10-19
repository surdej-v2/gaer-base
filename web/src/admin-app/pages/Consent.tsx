import { useEffect, useState } from "react";
import { api, errorText } from "../../api";
import { Alert, fmtDate, fmtNumber, Loading, PageHeader, Pager, useLoad } from "../../ui";

type Texts = { consent_title: string; consent_text: string; consent_accept: string; consent_decline: string; consent_version: number };
type Record_ = { id: number; ts: string; visitor_id: string | null; user_id: number | null; user_phone: string | null; statistics: boolean; version: number; device: string | null };
type Data = { texts: Texts; summary: { accepted: number; declined: number }; records: Record_[]; total: number };

const LIMIT = 100;

export default function Consent() {
  const [offset, setOffset] = useState(0);
  const data = useLoad(() => api.get<Data>(`/api/admin/consent?limit=${LIMIT}&offset=${offset}`), [offset]);
  const [texts, setTexts] = useState<Texts | null>(null);
  const [askAgain, setAskAgain] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (data.data && !texts) setTexts(data.data.texts);
  }, [data.data, texts]);

  const save = async () => {
    if (!texts) return;
    setError(null);
    try {
      const { consent_version: _, ...body } = texts;
      await api.put("/api/admin/consent", { ...body, ask_again: askAgain });
      setNotice(askAgain ? "Teksterne er gemt. Alle besøgende bliver spurgt igen." : "Teksterne er gemt.");
      setAskAgain(false);
      setTexts(null);
      await data.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const total = (data.data?.summary.accepted ?? 0) + (data.data?.summary.declined ?? 0);

  return (
    <>
      <PageHeader eyebrow="/admin/consent" title="Samtykke og cookies">
        Vi sætter ingen cookies fra tredjepart. Den eneste cookie er login-sessionen. Siger den besøgende ja til statistik,
        gemmes et tilfældigt id i browseren, så besøg, forløb og heatmaps kan opsamles.
      </PageHeader>
      <Alert kind="success">{notice}</Alert>
      <Alert>{error || data.error}</Alert>
      {!data.data || !texts ? <Loading /> : (
        <>
          <div className="grid" style={{ "--min": "200px" } as React.CSSProperties}>
            <div className="card"><span className="eyebrow">Har sagt ja</span><span className="stat">{fmtNumber(data.data.summary.accepted)}</span></div>
            <div className="card"><span className="eyebrow">Har sagt nej</span><span className="stat">{fmtNumber(data.data.summary.declined)}</span></div>
            <div className="card"><span className="eyebrow">Andel ja</span><span className="stat">{total ? `${Math.round((data.data.summary.accepted / total) * 100)} %` : "—"}</span></div>
          </div>
          <div className="grid" style={{ "--min": "320px" } as React.CSSProperties}>
            <div className="card">
              <span className="eyebrow">Tekster i banneret · version {texts.consent_version}</span>
              <label className="field">Overskrift<input value={texts.consent_title} onChange={(e) => setTexts({ ...texts, consent_title: e.target.value })} /></label>
              <label className="field">Tekst<textarea value={texts.consent_text} onChange={(e) => setTexts({ ...texts, consent_text: e.target.value })} /></label>
              <div className="filters">
                <label className="field">Knap: ja<input value={texts.consent_accept} onChange={(e) => setTexts({ ...texts, consent_accept: e.target.value })} /></label>
                <label className="field">Knap: nej<input value={texts.consent_decline} onChange={(e) => setTexts({ ...texts, consent_decline: e.target.value })} /></label>
              </div>
              <label className="row small"><input type="checkbox" checked={askAgain} onChange={(e) => setAskAgain(e.target.checked)} /> Spørg alle besøgende igen (ny version)</label>
              <div><button className="btn btn-primary" onClick={save}>Gem tekster</button></div>
            </div>
            <div className="card muted-bg">
              <span className="eyebrow">Forhåndsvisning</span>
              <div className="card">
                <h3>{texts.consent_title}</h3>
                <p className="small muted">{texts.consent_text}</p>
                <div className="row"><span className="btn btn-primary">{texts.consent_accept}</span><span className="btn btn-outline">{texts.consent_decline}</span></div>
              </div>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Tidspunkt</th><th>Svar</th><th>Besøgende</th><th>Bruger</th><th>Enhed</th><th>Version</th></tr></thead>
              <tbody>
                {data.data.records.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtDate(r.ts)}</td>
                    <td><span className={`pill ${r.statistics ? "green" : ""}`}>{r.statistics ? "Statistik: ja" : "Kun nødvendige"}</span></td>
                    <td className="mono tiny">{r.visitor_id ?? "—"}</td>
                    <td>{r.user_phone ?? "—"}</td>
                    <td>{r.device ?? "—"}</td>
                    <td className="num">{r.version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={data.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </>
  );
}
