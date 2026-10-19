import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorText } from "../api";
import { passkeyErrorText, registerPasskey, useAuth } from "../auth";
import { Alert, fmtDate, Loading, PageHeader, useLoad } from "../ui";
import PhoneChange from "./PhoneChange";

type Passkey = { id: number; name: string; created_at: string; last_used_at: string | null };
type Session = { id: number; created_at: string; last_seen_at: string; ip: string | null; user_agent: string; current: boolean };

export default function Account() {
  const { user, refresh } = useAuth();
  const [name, setName] = useState(user?.display_name ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const passkeys = useLoad(() => api.get<Passkey[]>("/api/me/passkeys"));
  const sessions = useLoad(() => api.get<Session[]>("/api/me/sessions"));
  const level = useLoad(() => api.get<{ level: number }>("/api/features"));

  useEffect(() => setName(user?.display_name ?? ""), [user?.display_name]);

  const act = async (fn: () => Promise<unknown>, ok?: string, passkey = false) => {
    setError(null);
    setMessage(null);
    try {
      await fn();
      if (ok) setMessage(ok);
    } catch (e) {
      setError(passkey ? passkeyErrorText(e) : errorText(e));
    }
  };

  return (
    <div className="page">
      <PageHeader eyebrow="Konto" title="Din konto" />
      <Alert kind="success">{message}</Alert>
      <Alert>{error}</Alert>

      <div className="grid" style={{ "--min": "320px" } as React.CSSProperties}>
        <div className="card">
          <span className="eyebrow">Profil</span>
          <PhoneChange />
          <label className="field">
            Navn
            <input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
          </label>
          <div>
            <button className="btn btn-primary" onClick={() => act(async () => {
              await api.patch("/api/me/profile", { display_name: name });
              await refresh();
            }, "Navnet er gemt")}>Gem</button>
          </div>
        </div>

        <div className="card">
          <div className="spread">
            <span className="eyebrow">Passkeys</span>
            <button className="btn btn-outline btn-sm" onClick={() => act(async () => {
              await registerPasskey();
              await Promise.all([passkeys.reload(), refresh()]);
            }, "Passkey oprettet", true)}>Tilføj passkey</button>
          </div>
          {passkeys.loading && !passkeys.data ? <Loading /> : passkeys.data?.length === 0 ? (
            <p className="muted small">Ingen passkeys endnu. Du har {user?.grace_left ?? 0} logins med engangskode tilbage.</p>
          ) : (
            passkeys.data?.map((p) => (
              <div key={p.id} className="spread" style={{ borderTop: "1px solid var(--hm-border)", paddingTop: 10 }}>
                <div className="stack" style={{ "--gap": "2px" } as React.CSSProperties}>
                  <strong>{p.name}</strong>
                  <span className="muted tiny">Oprettet {fmtDate(p.created_at)} · Sidst brugt {fmtDate(p.last_used_at)}</span>
                </div>
                <div className="row" style={{ "--gap": "4px" } as React.CSSProperties}>
                  <button className="btn btn-ghost btn-sm" onClick={() => {
                    const n = prompt("Nyt navn", p.name);
                    if (n) act(async () => { await api.patch(`/api/me/passkeys/${p.id}`, { name: n }); await passkeys.reload(); });
                  }}>Omdøb</button>
                  <button className="btn btn-danger btn-sm" onClick={() => {
                    if (confirm(`Slet passkey "${p.name}"?`)) act(async () => {
                      await api.del(`/api/me/passkeys/${p.id}`);
                      await Promise.all([passkeys.reload(), refresh()]);
                    }, "Passkey slettet");
                  }}>Slet</button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="card">
        <span className="eyebrow">Aktive logins</span>
        {sessions.data?.map((s) => (
          <div key={s.id} className="spread" style={{ borderTop: "1px solid var(--hm-border)", paddingTop: 10 }}>
            <div className="stack" style={{ "--gap": "2px", minWidth: 0 } as React.CSSProperties}>
              <strong className="small">{s.user_agent || "Ukendt enhed"}</strong>
              <span className="muted tiny">
                {s.ip ?? "—"} · Logget ind {fmtDate(s.created_at)} · Sidst aktiv {fmtDate(s.last_seen_at)}
              </span>
            </div>
            {s.current ? <span className="pill green">Denne enhed</span> : (
              <button className="btn btn-outline btn-sm" onClick={() => act(async () => {
                await api.del(`/api/me/sessions/${s.id}`);
                await sessions.reload();
              }, "Logget ud på enheden")}>Log ud</button>
            )}
          </div>
        ))}
      </div>

      <div className="card">
        <span className="eyebrow">Dit niveau</span>
        <p className="small muted">
          Vælg hvor tidligt du vil se nye features. Ring 4 er kun det stabile. Lavere niveau giver adgang til features, der stadig er under udvikling.
        </p>
        <label className="field">
          Niveau
          <select value={String(level.data?.level ?? 4)} onChange={(e) => act(async () => {
            await api.put("/api/features/level", { ring: Number(e.target.value) === 4 ? null : Number(e.target.value) });
            await level.reload();
          }, "Dit niveau er gemt")}>
            <option value="1">Ring 1 · Internt</option>
            <option value="2">Ring 2 · Beta</option>
            <option value="3">Ring 3 · Preview</option>
            <option value="4">Ring 4 · Stabilt</option>
          </select>
        </label>
      </div>

      <div className="card muted-bg">
        <span className="eyebrow">Dine data</span>
        <p className="small">
          Du kan bede om indsigt i, rettelse af eller sletning af dine data, og ændre dit samtykke til statistik.{" "}
          <Link to="/privatliv">Gå til privatliv</Link>
        </p>
      </div>
    </div>
  );
}
