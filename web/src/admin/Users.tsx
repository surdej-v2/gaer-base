import { useState } from "react";
import { Link } from "react-router-dom";
import { api, errorText, query } from "../api";
import { useAuth } from "../auth";
import { Alert, fmtBytes, fmtDate, Loading, PageHeader, Pager, useLoad } from "../ui";

type UserRow = {
  id: number; phone: string; display_name: string | null; role: "user" | "admin"; grace_logins_used: number;
  blocked_at: string | null; created_at: string; last_login_at: string | null; passkeys: number;
  active_sessions: number; file_bytes: number;
};

const LIMIT = 50;

export default function Users() {
  const { user: me } = useAuth();
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const users = useLoad(() => api.get<{ items: UserRow[]; total: number; grace_logins: number }>(
    `/api/admin/users${query({ q, limit: LIMIT, offset })}`), [q, offset]);

  const act = async (label: string, fn: () => Promise<unknown>, confirmText?: string) => {
    if (confirmText && !confirm(confirmText)) return;
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(label);
      await users.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <PageHeader eyebrow="/admin/users" title="Brugere og roller">
        Den første bruger på en ny instans bliver admin. Der skal altid være mindst én admin.
      </PageHeader>
      <div className="row">
        <input placeholder="Søg på telefonnummer eller navn" value={q} style={{ flex: 1, maxWidth: 360 }}
               onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
      </div>
      <Alert kind="success">{notice}</Alert>
      <Alert>{error}</Alert>
      {users.loading && !users.data ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Bruger</th><th>Rolle</th><th>Login</th><th>Sidst logget ind</th><th>Filer</th><th /></tr>
            </thead>
            <tbody>
              {users.data?.items.map((u) => (
                <tr key={u.id}>
                  <td>
                    <strong>{u.display_name || u.phone}</strong>
                    <div className="muted tiny">{u.display_name ? u.phone : ""} #{u.id} · oprettet {fmtDate(u.created_at)}</div>
                  </td>
                  <td>
                    <div className="row" style={{ "--gap": "4px" } as React.CSSProperties}>
                      <span className={`pill ${u.role === "admin" ? "dark" : ""}`}>{u.role === "admin" ? "Admin" : "Bruger"}</span>
                      {u.blocked_at && <span className="pill red">Spærret</span>}
                    </div>
                  </td>
                  <td>
                    {u.passkeys > 0 ? <span className="pill green">{u.passkeys} passkey{u.passkeys > 1 ? "s" : ""}</span>
                      : <span className="pill blue">{u.grace_logins_used}/{users.data?.grace_logins} engangskoder</span>}
                    <div className="muted tiny">{u.active_sessions} aktive sessioner</div>
                  </td>
                  <td>{fmtDate(u.last_login_at)}</td>
                  <td className="num">{fmtBytes(u.file_bytes)}</td>
                  <td>
                    <div className="row" style={{ "--gap": "4px", justifyContent: "flex-end" } as React.CSSProperties}>
                      {u.role === "admin" ? (
                        <button className="btn btn-outline btn-sm" onClick={() => act("Admin-rollen er fjernet",
                          () => api.patch(`/api/admin/users/${u.id}/role`, { role: "user" }), `Fjern admin-rollen fra ${u.phone}?`)}>Fjern admin</button>
                      ) : (
                        <button className="btn btn-outline btn-sm" onClick={() => act("Brugeren er nu admin",
                          () => api.patch(`/api/admin/users/${u.id}/role`, { role: "admin" }), `Gør ${u.phone} til admin?`)}>Gør til admin</button>
                      )}
                      {u.blocked_at ? (
                        <button className="btn btn-outline btn-sm" onClick={() => act("Spærringen er ophævet",
                          () => api.post(`/api/admin/users/${u.id}/unblock`))}>Ophæv spærring</button>
                      ) : u.id !== me?.id && (
                        <button className="btn btn-danger btn-sm" onClick={() => act("Brugeren er spærret og logget ud",
                          () => api.post(`/api/admin/users/${u.id}/block`), `Spær ${u.phone}? Brugeren logges ud alle steder.`)}>Spær</button>
                      )}
                      <button className="btn btn-outline btn-sm" disabled={!u.passkeys} onClick={() => act("Passkeys er nulstillet",
                        () => api.post(`/api/admin/users/${u.id}/reset-passkeys`),
                        `Nulstil alle passkeys for ${u.phone}? Brugeren logges ud og skal oprette en ny passkey efter login med engangskode.`)}>Nulstil passkeys</button>
                      <button className="btn btn-ghost btn-sm" disabled={!u.active_sessions} onClick={() => act("Brugeren er logget ud alle steder",
                        () => api.post(`/api/admin/users/${u.id}/revoke-sessions`))}>Log ud</button>
                      <button className="btn btn-ghost btn-sm" onClick={() => {
                        const phone = prompt(`Nyt telefonnummer for ${u.phone} (rettelse). Brugeren logges ud.`, "");
                        if (phone) act("Telefonnummeret er rettet", () => api.patch(`/api/admin/users/${u.id}/phone`, { phone }));
                      }}>Ret nummer</button>
                      <Link className="btn btn-ghost btn-sm" to={`/admin/logs/auth?user_id=${u.id}`}>Log</Link>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {users.data && <Pager total={users.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />}
    </>
  );
}
