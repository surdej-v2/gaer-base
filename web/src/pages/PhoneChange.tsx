// Change the account's phone number: a code is sent to the new number to prove it is yours.

import { useState } from "react";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { Alert } from "../ui";

export default function PhoneChange() {
  const { user, refresh } = useAuth();
  const [open, setOpen] = useState(false);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState<{ devCode?: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  return (
    <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
      <label className="field">
        Telefonnummer
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input value={user?.phone ?? ""} disabled style={{ flex: 1 }} />
          {!open && <button className="btn btn-outline btn-sm" type="button" onClick={() => { setOpen(true); setNotice(null); }}>Skift</button>}
        </div>
      </label>
      {open && !sent && (
        <div className="row">
          <input type="tel" placeholder="Nyt nummer" value={phone} onChange={(e) => setPhone(e.target.value)} style={{ flex: 1 }} />
          <button className="btn btn-primary btn-sm" disabled={busy || phone.trim().length < 6} onClick={() => run(async () => {
            const r = await api.post<{ dev_code?: string }>("/api/me/phone/start", { phone });
            setSent({ devCode: r.dev_code });
          })}>Send kode</button>
          <button className="btn btn-ghost btn-sm" onClick={() => { setOpen(false); setError(null); }}>Annullér</button>
        </div>
      )}
      {open && sent && (
        <>
          <p className="small muted">Vi har sendt en kode til det nye nummer. Når du bekræfter, bliver du logget ud på dine andre enheder.</p>
          {sent.devCode && <Alert kind="info">Udvikling: koden er <strong className="mono">{sent.devCode}</strong></Alert>}
          <div className="row">
            <input className="mono" inputMode="numeric" maxLength={6} placeholder="Kode" value={code}
                   onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} style={{ width: 120 }} />
            <button className="btn btn-primary btn-sm" disabled={busy || code.length !== 6} onClick={() => run(async () => {
              await api.post("/api/me/phone/verify", { phone, code });
              await refresh();
              setOpen(false); setSent(null); setPhone(""); setCode("");
              setNotice("Dit telefonnummer er skiftet");
            })}>Bekræft</button>
            <button className="btn btn-ghost btn-sm" onClick={() => { setSent(null); setCode(""); }}>Tilbage</button>
          </div>
        </>
      )}
      <Alert kind="success">{notice}</Alert>
      <Alert>{error}</Alert>
    </div>
  );
}
