import { useState, type FormEvent } from "react";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { openConsent } from "../ConsentBanner";
import { getConsent } from "../tracker";
import { Alert, download, fmtDay, PageHeader, useLoad } from "../ui";

type Kind = "access" | "rectification" | "erasure";
const KINDS: Record<Kind, string> = { access: "Indsigt", rectification: "Rettelse", erasure: "Sletning" };
const STATUS: Record<string, string> = { received: "Modtaget", in_progress: "Under behandling", completed: "Afsluttet", rejected: "Afvist" };

type MyRequest = { id: number; kind: Kind; status: string; received_at: string; due_at: string };

export default function Privacy() {
  const { user, scope, config } = useAuth();
  const loggedIn = !!user && scope === "full";
  const consent = getConsent();
  const mine = useLoad(() => (loggedIn ? api.get<MyRequest[]>("/api/me/gdpr") : Promise.resolve([])), [loggedIn]);
  const [kind, setKind] = useState<Kind>("access");
  const [contact, setContact] = useState("");
  const [message, setMessage] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const res = loggedIn
        ? await api.post<{ due_at: string }>("/api/me/gdpr", { kind, message })
        : await api.post<{ due_at: string }>("/api/public/gdpr", { kind, contact, message });
      setDone(`Din anmodning er modtaget. Du får svar senest ${fmtDay(res.due_at)}.`);
      setMessage("");
      mine.reload();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <PageHeader eyebrow="Privatliv" title={`Sådan behandler ${config?.app_name ?? "vi"} dine data`}>
        Alle data ligger hos OVHcloud i Europa. Vi bruger ingen analyseværktøjer fra tredjepart. Statistik og heatmaps
        samler vi selv ind, og kun hvis du har sagt ja. Sletter vi dine data, forsvinder de også fra vores
        sikkerhedskopier, når de udløber; gendanner vi en kopi, gentager vi sletningen, før den tages i brug.
      </PageHeader>

      <div className="card">
        <span className="eyebrow">Statistik og cookies</span>
        <p className="small">
          {consent?.statistics
            ? "Du har sagt ja til statistik. Vi gemmer et tilfældigt id i din browser og ser, hvilke sider du besøger, og hvor du klikker."
            : "Du har ikke sagt ja til statistik. Vi tæller kun anonyme sidevisninger uden at gemme noget i din browser."}
        </p>
        <div><button className="btn btn-outline" onClick={openConsent}>Skift valg</button></div>
      </div>

      <form className="card" onSubmit={submit}>
        <span className="eyebrow">Dine rettigheder</span>
        <p className="small">
          Du kan få indsigt i dine data, få dem rettet eller få dem slettet. Vi svarer inden for 30 dage.
          {loggedIn && " Det meste kan du også gøre selv herunder: hente dine data, skifte telefonnummer under Konto og slette din konto."}
        </p>
        <label className="field">
          Hvad vil du?
          <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            {Object.entries(KINDS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
        {!loggedIn && (
          <label className="field">
            Telefonnummer eller e-mail, vi kan kontakte dig på
            <input value={contact} onChange={(e) => setContact(e.target.value)} required minLength={3} />
          </label>
        )}
        <label className="field">
          Uddyb gerne (valgfrit)
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} maxLength={4000} />
        </label>
        <Alert kind="success">{done}</Alert>
        <Alert>{error}</Alert>
        <div><button className="btn btn-primary">Send anmodning</button></div>
      </form>

      {loggedIn && <MyData phone={user!.phone} />}

      {loggedIn && !!mine.data?.length && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Anmodning</th><th>Status</th><th>Modtaget</th><th>Frist</th></tr></thead>
            <tbody>
              {mine.data.map((r) => (
                <tr key={r.id}><td>{KINDS[r.kind]}</td><td>{STATUS[r.status]}</td><td>{fmtDay(r.received_at)}</td><td>{fmtDay(r.due_at)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


/** Self-service: download all own data, or delete the account (the right to be forgotten). */
function MyData({ phone }: { phone: string }) {
  const { refresh } = useAuth();
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const remove = async () => {
    if (!window.confirm("Slet din konto og alle dine data? Det kan ikke fortrydes.")) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/me/delete", { confirm_phone: confirm });
      await refresh().catch(() => undefined);
      window.location.href = "/w/";
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <div className="grid" style={{ "--min": "320px" } as React.CSSProperties}>
      <div className="card">
        <span className="eyebrow">Hent dine data</span>
        <p className="small">Én zip-fil med alt, vi har gemt om dig, og kopier af dine filer. Samtaler med AI'en gemmes ikke.</p>
        <div><button className="btn btn-primary" onClick={() => download("/api/me/export")}>Hent mine data</button></div>
      </div>
      <div className="card" style={{ borderColor: "#fecaca" }}>
        <span className="eyebrow" style={{ color: "#b91c1c" }}>Slet din konto</span>
        <p className="small">
          Sletter din bruger, dine filer, klip, passkeys og log-data om dig. Kun en anonym notits om, at en konto er
          slettet, bliver tilbage. Skriv dit telefonnummer for at bekræfte.
        </p>
        <input type="tel" placeholder={phone} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        <Alert>{error}</Alert>
        <div><button className="btn btn-danger" disabled={busy || confirm.replace(/\D/g, "").length < 6} onClick={remove}>Slet min konto</button></div>
      </div>
    </div>
  );
}
