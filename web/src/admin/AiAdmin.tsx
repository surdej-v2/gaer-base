import { useEffect, useState } from "react";
import { api, errorText } from "../api";
import { Alert, fmtNumber, Loading, PageHeader, useLoad } from "../ui";

type Data = {
  enabled: boolean; base_url: string; model: string; default_monthly_tokens: number;
  users: { id: number; phone: string; display_name: string | null; ai_monthly_tokens: number | null; used: number; requests: number }[];
  per_surface: { surface: string; requests: number; tokens: number }[];
};

const SURFACE: Record<string, string> = { app: "Web-app", extension: "Browserudvidelse", word: "Word", outlook: "Outlook", document: "Excel/PowerPoint", mobile: "Mobil" };

export default function AiAdmin() {
  const data = useLoad(() => api.get<Data>("/api/admin/ai"));
  const [def, setDef] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (data.data) setDef(String(data.data.default_monthly_tokens)); }, [data.data]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null); setNotice(null);
    try { await fn(); setNotice(ok); await data.reload(); } catch (e) { setError(errorText(e)); }
  };
  const d = data.data;

  return (
    <>
      <PageHeader eyebrow="/admin/ai" title="AI">
        Valgfri chat i browserudvidelsen, Word, Outlook og mobil-appen. Den bruger OVHcloud AI Endpoints (eller en anden
        OpenAI-kompatibel tjeneste), så teksten behandles i Europa. Samtaler gemmes ikke — kun forbruget i tokens.
      </PageHeader>
      <Alert kind="success">{notice}</Alert>
      <Alert>{error || data.error}</Alert>
      {!d ? <Loading /> : (
        <>
          {!d.enabled && <Alert kind="info">AI er slået fra. Sæt <code>AI_API_KEY</code> (og evt. <code>AI_MODEL</code>, <code>AI_BASE_URL</code>) i instansens .env for at slå chatten til. Nøglen laves i OVHcloud Manager under Public Cloud → AI Endpoints.</Alert>}
          <div className="grid" style={{ "--min": "220px" } as React.CSSProperties}>
            <div className="card"><span className="eyebrow">Status</span><span className="stat" style={{ fontSize: 24 }}>{d.enabled ? "Slået til" : "Slået fra"}</span><span className="muted tiny mono" style={{ wordBreak: "break-all" }}>{d.base_url}</span></div>
            <div className="card"><span className="eyebrow">Model</span><span className="mono small">{d.model}</span></div>
            <div className="card"><span className="eyebrow">Forbrug denne måned</span><span className="stat">{fmtNumber(d.users.reduce((n, u) => n + Number(u.used), 0))}</span><span className="muted tiny">tokens</span></div>
          </div>
          <div className="card">
            <span className="eyebrow">Standardkvote pr. bruger pr. måned</span>
            <div className="row">
              <input type="number" min={0} value={def} onChange={(e) => setDef(e.target.value)} style={{ width: 180 }} />
              <span className="muted small">tokens · 0 slår AI fra for brugere uden egen kvote</span>
              <button className="btn btn-primary btn-sm" onClick={() => act(() => api.put("/api/admin/ai", { default_monthly_tokens: Number(def) }), "Standardkvoten er gemt")}>Gem</button>
            </div>
          </div>
          {d.per_surface.length > 0 && (
            <div className="row">{d.per_surface.map((p) => <span key={p.surface} className="pill">{SURFACE[p.surface] ?? p.surface}: {fmtNumber(p.tokens)} tokens · {fmtNumber(p.requests)} kald</span>)}</div>
          )}
          <div className="table-wrap">
            <table>
              <thead><tr><th>Bruger</th><th>Brugt denne måned</th><th>Kvote</th><th /></tr></thead>
              <tbody>
                {d.users.map((u) => {
                  const limit = u.ai_monthly_tokens ?? d.default_monthly_tokens;
                  return (
                    <tr key={u.id}>
                      <td><strong>{u.display_name || u.phone}</strong><div className="muted tiny">#{u.id} · {fmtNumber(u.requests)} kald</div></td>
                      <td style={{ minWidth: 180 }}>
                        <div className="small num">{fmtNumber(u.used)} / {fmtNumber(limit)}</div>
                        <div className="bar"><div style={{ width: `${limit ? Math.min(100, (u.used / limit) * 100) : 0}%`, background: u.used >= limit ? "var(--hm-destructive)" : undefined }} /></div>
                      </td>
                      <td>{u.ai_monthly_tokens === null ? <span className="muted small">Standard</span> : <span className="pill">{fmtNumber(u.ai_monthly_tokens)}</span>}</td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button className="btn btn-outline btn-sm" onClick={() => {
                          const v = prompt("Månedlig kvote i tokens (tom = standard, 0 = slået fra)", u.ai_monthly_tokens?.toString() ?? "");
                          if (v === null) return;
                          act(() => api.put(`/api/admin/ai/users/${u.id}`, { monthly_tokens: v.trim() === "" ? null : Number(v) }), "Kvoten er gemt");
                        }}>Sæt kvote</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
