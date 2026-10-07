import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { Alert, Loading, PageHeader, useLoad } from "../ui";

type Data = {
  app_name: string; sms_sender: string; gdpr_email: string; logo_url: string | null;
  environment: { version: string; env: string; sms_provider: string; storage: string; rp_id: string; grace_logins: number; bootstrap_admin: string | null };
};

export default function Settings() {
  const { refresh } = useAuth();
  const data = useLoad(() => api.get<Data>("/api/admin/settings"));
  const [form, setForm] = useState({ app_name: "", sms_sender: "", gdpr_email: "" });
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const logo = useRef<HTMLInputElement>(null);
  const [logoVersion, setLogoVersion] = useState(0);

  useEffect(() => {
    if (data.data) setForm({ app_name: data.data.app_name, sms_sender: data.data.sms_sender, gdpr_email: data.data.gdpr_email });
  }, [data.data]);

  const act = async (fn: () => Promise<unknown>, label: string) => {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(label);
      await data.reload();
      await refresh();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const env = data.data?.environment;

  return (
    <>
      <PageHeader eyebrow="/admin/settings" title="Indstillinger" />
      <Alert kind="success">{notice}</Alert>
      <Alert>{error || data.error}</Alert>
      {!data.data ? <Loading /> : (
        <div className="grid" style={{ "--min": "320px" } as React.CSSProperties}>
          <form className="card" onSubmit={(e) => { e.preventDefault(); act(() => api.put("/api/admin/settings", form), "Indstillingerne er gemt."); }}>
            <span className="eyebrow">Instans</span>
            <label className="field">Navn<input value={form.app_name} onChange={(e) => setForm({ ...form, app_name: e.target.value })} required maxLength={80} /></label>
            <label className="field">SMS-afsender (højst 11 tegn, bogstaver og tal)
              <input value={form.sms_sender} onChange={(e) => setForm({ ...form, sms_sender: e.target.value })} required maxLength={11} pattern="[A-Za-z0-9 ]+" />
            </label>
            <label className="field">E-mail til GDPR-forespørgsler<input type="email" value={form.gdpr_email} onChange={(e) => setForm({ ...form, gdpr_email: e.target.value })} /></label>
            <div><button className="btn btn-primary">Gem</button></div>
          </form>
          <div className="stack" style={{ "--gap": "16px" } as React.CSSProperties}>
            <div className="card">
              <span className="eyebrow">Logo</span>
              {data.data.logo_url ? <img src={`${data.data.logo_url}?v=${logoVersion}`} alt="Logo" style={{ maxHeight: 64, maxWidth: 240, objectFit: "contain", alignSelf: "flex-start" }} /> : <p className="muted small">Intet logo. Navnet vises i stedet.</p>}
              <input ref={logo} type="file" accept="image/*" hidden onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                const body = new FormData();
                body.append("file", file);
                act(async () => { await api.post("/api/admin/settings/logo", body); setLogoVersion((v) => v + 1); }, "Logoet er opdateret.");
              }} />
              <div><button className="btn btn-outline" onClick={() => logo.current?.click()}>Upload logo</button></div>
            </div>
            <div className="card muted-bg">
              <span className="eyebrow">Miljø</span>
              <div className="small stack" style={{ "--gap": "4px" } as React.CSSProperties}>
                <div className="spread"><span>Version</span><span className="mono">{env?.version}</span></div>
                <div className="spread"><span>Miljø</span><span className="mono">{env?.env}</span></div>
                <div className="spread"><span>SMS</span><span className="mono">{env?.sms_provider}</span></div>
                <div className="spread"><span>Lager</span><span className="mono">{env?.storage}</span></div>
                <div className="spread"><span>Passkey-domæne</span><span className="mono">{env?.rp_id}</span></div>
                <div className="spread"><span>Grace logins</span><span className="mono">{env?.grace_logins}</span></div>
                <div className="spread"><span>Første admin</span><span className="mono">{env?.bootstrap_admin ? `låst til ${env.bootstrap_admin}` : "den første, der logger ind"}</span></div>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
