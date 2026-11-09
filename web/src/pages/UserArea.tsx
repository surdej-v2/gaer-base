// Mit område: /user (your overview), /user/indstillinger, /user/deling, and /user/:id — an area someone has
// shared with you as reader or author. API: /api/user (server/app/routes/user_area.py, rules in app/delegation.py).
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, NavLink, useParams } from "react-router-dom";
import { api, ApiError, errorText } from "../api";
import { useAuth } from "../auth";
import { FeatureGate } from "../features";
import { Alert, download, fmtBytes, fmtDate, fmtDay, fmtNumber, Loading, useLoad } from "../ui";
import { useMailStatus } from "../mail";
import Account from "./Account";
import { activityText, Avatar, name, ROLE_TEXT, type Overview, type Person, type Role, type Stats } from "./userAreaShared";

type Grant = {
  id: number; email: string; role: Role; role_label: string; user_id: number | null; display_name: string | null;
  created_at: string; expires_at: string | null; accepted_at: string | null; last_used_at: string | null; expired: boolean;
};
type AreaClip = { id: number; source: string; title: string; body: string; url: string | null; created_at: string; updated_at: string | null };
type AreaFile = { id: string; filename: string; path: string | null; size_bytes: number; created_at: string; updated_at: string | null };
type Area = {
  owner: Person; role: Role | "owner"; role_label: string; can_write: boolean; granted_at: string | null; expires_at: string | null;
  stats: Stats; clips: AreaClip[]; files: AreaFile[];
};


const MATRIX: [string, boolean, boolean][] = [
  ["Se overblik, klip og filer", true, true],
  ["Hente filer", true, true],
  ["Oprette og redigere klip, uploade filer", false, true],
  ["Slette klip og filer", false, false],
  ["Se indstillinger og sikkerhed", false, false],
  ["Give andre adgang", false, false],
];


/** updated_at is set on insert too: only show it when the clip was changed later. */
const edited = (c: AreaClip) => !!c.updated_at && new Date(c.updated_at).getTime() - new Date(c.created_at).getTime() > 60_000;

/** The folder in the owner's home, e.g. "/noter/". */
const folderOf = (f: AreaFile) => (f.path ?? "").replace(/^\/users\/\d+/, "").slice(0, -f.filename.length) || "/";

const roleTone = (role: string) => (role === "author" ? "amber" : role === "reader" ? "green" : "blue");

function RolePill({ role, label }: { role: string; label: string }) {
  return <span className={`pill ${role === "author" ? "amber" : ""}`}>{label}</span>;
}

/** The heading and tabs on your own pages (also used by /user/mail). */
export function OwnHeader({ title, path }: { title: string; path: string }) {
  const mail = useMailStatus();
  return (
    <>
      <div className="spread" style={{ alignItems: "flex-end" }}>
        <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
          <span className="eyebrow">Mit område</span>
          <h1>{title}</h1>
        </div>
        <span className="pill mono">{path}</span>
      </div>
      <nav className="subnav" aria-label="Mit område">
        <NavLink to="/user" end>Overblik</NavLink>
        <NavLink to="/user/indstillinger">Indstillinger</NavLink>
        <NavLink to="/user/deling" data-deeplink="user-sharing">Deling</NavLink>
        {mail?.enabled && <FeatureGate feature="mail"><NavLink to="/user/mail" data-deeplink="user-mail">Mail</NavLink></FeatureGate>}
      </nav>
    </>
  );
}

// ── /user ───────────────────────────────────────────────────────────────────


export function UserHome() {
  const ov = useLoad(() => api.get<Overview>("/api/user/overview"));
  const { user } = useAuth();
  const d = ov.data;
  const first = (user?.display_name || "").split(" ")[0];
  return (
    <div className="page">
      <OwnHeader title={`Hej${first ? `, ${first}` : ""}`} path="/user" />
      <Alert>{ov.error}</Alert>
      {!d ? <Loading /> : (
        <>
          <div className="grid" style={{ "--min": "220px" } as React.CSSProperties}>
            <FeatureGate feature="klip">
              <Link to="/klip" className="card">
                <span className="eyebrow">Klip</span>
                <span className="stat">{fmtNumber(d.stats.clips)}</span>
                <span className="muted small">{fmtNumber(d.stats.clips_week)} nye de sidste 7 dage</span>
              </Link>
            </FeatureGate>
            <FeatureGate feature="filer">
              <Link to="/filer" className="card">
                <span className="eyebrow">Filer</span>
                <span className="stat">{fmtNumber(d.stats.files)}</span>
                <span className="muted small">{fmtBytes(d.stats.bytes)} i dit hjem</span>
              </Link>
            </FeatureGate>
            <Link to="/user/deling" className="card">
              <span className="eyebrow">Delt med</span>
              <span className="stat">{fmtNumber(d.access.readers + d.access.authors)}</span>
              <span className="muted small">{d.access.readers + d.access.authors === 1 ? "person har" : "personer har"} adgang</span>
            </Link>
            <div className="card">
              <span className="eyebrow">Delt med dig</span>
              <span className="stat">{fmtNumber(d.shared_with_me.length)}</span>
              <span className="muted small">{d.shared_with_me.length === 1 ? "område" : "områder"} du har adgang til</span>
            </div>
          </div>

          <div className="row" style={{ alignItems: "flex-start", "--gap": "16px" } as React.CSSProperties}>
            <section className="card" aria-labelledby="h-activity" style={{ flex: "999 1 480px", gap: 0 }} data-deeplink="user-activity">
              <h3 id="h-activity" style={{ paddingBottom: 8 }}>Seneste aktivitet</h3>
              {d.activity.length === 0 && <p className="muted small">Intet endnu.</p>}
              {d.activity.map((a, i) => {
                const who = a.mine ? "Du" : a.actor?.name || "En bruger";
                return (
                  <div key={i} className="list-row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
                    <Avatar text={a.mine ? name(user) : who} tone={a.mine ? "blue" : "amber"} />
                    <div className="stack" style={{ "--gap": "2px", minWidth: 0 } as React.CSSProperties}>
                      <span className="small" style={{ wordBreak: "break-word" }}><strong>{who}</strong> {activityText(a)}</span>
                      <span className="muted tiny">{fmtDate(a.ts)}</span>
                    </div>
                  </div>
                );
              })}
            </section>

            <div className="stack" style={{ flex: "1 1 320px", minWidth: 0, "--gap": "16px" } as React.CSSProperties}>
              <section className="card" aria-labelledby="h-shared" style={{ gap: 0 }} data-deeplink="user-shared">
                <h3 id="h-shared">Delt med dig</h3>
                <p className="muted small" style={{ paddingBottom: 8 }}>Områder andre har givet dig adgang til.</p>
                {d.shared_with_me.length === 0 && <p className="muted small">Ingen har delt deres område med dig.</p>}
                {d.shared_with_me.map((s) => (
                  <Link key={s.id} to={`/user/${s.owner.id}`} className="list-row" style={{ flexWrap: "nowrap" }}>
                    <Avatar text={name(s.owner)} />
                    <span className="stack" style={{ flex: "1 1 auto", minWidth: 0, "--gap": "0" } as React.CSSProperties}>
                      <strong className="small">{name(s.owner)}</strong>
                      <span className="mono tiny muted">/user/{s.owner.id}</span>
                    </span>
                    <RolePill role={s.role} label={s.role_label} />
                  </Link>
                ))}
              </section>

              <section className="card" aria-labelledby="h-access">
                <h3 id="h-access">Adgang til dit område</h3>
                <div className="row" style={{ "--gap": "8px" } as React.CSSProperties}>
                  {d.access.people.map((p, i) => (
                    <span key={i} style={{ marginLeft: i ? -14 : 0, borderRadius: 9999, border: "2px solid #fff", display: "inline-flex" }}>
                      <Avatar text={p.display_name || p.email} tone={roleTone(p.role)} />
                    </span>
                  ))}
                  <span className="small" style={{ color: "#334155" }}>
                    {d.access.authors} {d.access.authors === 1 ? "forfatter" : "forfattere"} · {d.access.readers} {d.access.readers === 1 ? "læser" : "læsere"}
                    {d.access.pending ? ` · ${d.access.pending} afventer` : ""}
                  </span>
                </div>
                <div><Link to="/user/deling" className="btn btn-outline btn-sm">Administrér deling</Link></div>
              </section>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── /user/indstillinger ─────────────────────────────────────────────────────

export function UserSettings() {
  return (
    <div className="page">
      <OwnHeader title="Indstillinger" path="/user/indstillinger" />
      <Alert kind="info">
        Kun du ser denne side. Personer, du har delt dit område med, kan hverken se eller ændre dine indstillinger.
      </Alert>
      <Account inArea />
    </div>
  );
}

// ── /user/deling ────────────────────────────────────────────────────────────

export function UserSharing() {
  const grants = useLoad(() => api.get<Grant[]>("/api/user/delegations"));
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("reader");
  const [days, setDays] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    setMessage(null);
    try {
      await fn();
      setMessage(ok);
      await grants.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const give = (e: FormEvent) => {
    e.preventDefault();
    act(async () => {
      await api.post("/api/user/delegations", { email, role, expires_days: days ? Number(days) : null });
      setEmail("");
    }, `${email} har fået adgang som ${ROLE_TEXT[role].lower}`);
  };

  const list = grants.data ?? [];
  return (
    <div className="page">
      <OwnHeader title="Deling" path="/user/deling" />
      <Alert kind="success">{message}</Alert>
      <Alert>{error || grants.error}</Alert>

      <div className="row" style={{ alignItems: "flex-start", "--gap": "16px" } as React.CSSProperties}>
        <form className="card" onSubmit={give} style={{ flex: "1 1 360px", gap: 16 }} aria-labelledby="h-grant" data-deeplink="user-grant">
          <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
            <h3 id="h-grant">Giv adgang</h3>
            <p className="muted small">
              Personen logger ind som sig selv med sin egen e-mail og finder dit område under „Delt med dig“.
              Har personen ingen bruger endnu, får de adgang, første gang de logger ind.
            </p>
          </div>
          <label className="field">
            E-mail
            <input type="email" required maxLength={254} placeholder="navn@firma.dk" value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <fieldset style={{ border: 0, margin: 0, padding: 0 }} className="stack">
            <legend className="small" style={{ fontWeight: 600, paddingBottom: 6 }}>Rolle</legend>
            {(["reader", "author"] as Role[]).map((r) => (
              <button key={r} type="button" className="choice" aria-pressed={role === r} onClick={() => setRole(r)}>
                <span className="dot" aria-hidden />
                <span className="stack" style={{ "--gap": "2px" } as React.CSSProperties}>
                  <strong>{ROLE_TEXT[r].label}</strong>
                  <span className="muted small">{ROLE_TEXT[r].desc}</span>
                </span>
              </button>
            ))}
          </fieldset>
          <label className="field">
            Udløber
            <select value={days} onChange={(e) => setDays(e.target.value)}>
              <option value="">Aldrig</option>
              <option value="7">Om 7 dage</option>
              <option value="30">Om 30 dage</option>
              <option value="90">Om 90 dage</option>
            </select>
          </label>
          <div><button className="btn btn-primary">Giv adgang som {ROLE_TEXT[role].lower}</button></div>
        </form>

        <section className="card" aria-labelledby="h-matrix" style={{ flex: "1 1 400px" }}>
          <h3 id="h-matrix">Hvad rollerne må</h3>
          <div style={{ overflowX: "auto" }}>
            <table className="matrix">
              <thead><tr><th scope="col"><span className="sr-only">Handling</span></th><th scope="col">Læser</th><th scope="col">Forfatter</th><th scope="col">Dig</th></tr></thead>
              <tbody>
                {MATRIX.map(([what, r, a]) => (
                  <tr key={what}>
                    <th scope="row">{what}</th>
                    <td className={r ? "yes" : "no"}>{r ? "Ja" : "Nej"}</td>
                    <td className={a ? "yes" : "no"}>{a ? "Ja" : "Nej"}</td>
                    <td className="yes">Ja</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted tiny">Alt, hvad andre ændrer eller henter i dit område, står under Seneste aktivitet med deres navn.</p>
        </section>
      </div>

      <section className="card" aria-labelledby="h-list" style={{ gap: 0 }} data-deeplink="user-grants">
        <div className="spread" style={{ paddingBottom: 8 }}>
          <h3 id="h-list">Har adgang</h3>
          <span className="muted small">{list.length} {list.length === 1 ? "person" : "personer"}</span>
        </div>
        {grants.loading && !grants.data && <Loading />}
        {grants.data && list.length === 0 && <p className="muted small">Du har ikke delt dit område med nogen.</p>}
        {list.map((g) => (
          <div key={g.id} className="list-row">
            <Avatar text={g.display_name || g.email} tone={roleTone(g.role)} />
            <div className="stack" style={{ flex: "1 1 240px", minWidth: 0, "--gap": "2px" } as React.CSSProperties}>
              <strong style={{ wordBreak: "break-word" }}>{g.display_name || g.email}</strong>
              <span className="muted small" style={{ wordBreak: "break-word" }}>
                {g.display_name ? `${g.email} · ` : ""}Siden {fmtDay(g.created_at)}
                {g.last_used_at ? ` · Sidst brugt ${fmtDate(g.last_used_at)}` : ""}
                {g.expires_at ? ` · ${g.expired ? "Udløb" : "Udløber"} ${fmtDay(g.expires_at)}` : ""}
              </span>
            </div>
            {g.expired ? <span className="pill red">Udløbet</span> : !g.accepted_at && <span className="pill">Afventer første besøg</span>}
            <label className="row muted small" style={{ "--gap": "8px" } as React.CSSProperties}>
              Rolle
              <select aria-label={`Rolle for ${g.email}`} value={g.role} onChange={(e) => {
                const next = e.target.value as Role;
                act(() => api.patch(`/api/user/delegations/${g.id}`, { role: next }), `${g.email} er nu ${ROLE_TEXT[next].lower}`);
              }}>
                <option value="reader">Læser</option>
                <option value="author">Forfatter</option>
              </select>
            </label>
            {g.expires_at && (
              <button className="btn btn-outline btn-sm" onClick={() => act(
                () => api.patch(`/api/user/delegations/${g.id}`, { never_expires: true }), `${g.email} har nu adgang uden udløb`)}>
                Fjern udløb
              </button>
            )}
            <button className="btn btn-danger btn-sm" onClick={() => {
              if (confirm(`Fjern ${g.email}s adgang til dit område?`)) {
                act(() => api.del(`/api/user/delegations/${g.id}`), `${g.email} har ikke længere adgang`);
              }
            }}>Fjern adgang</button>
          </div>
        ))}
      </section>
    </div>
  );
}

// ── /user/:id — an area shared with you ─────────────────────────────────────

export function DelegatedArea() {
  const { id } = useParams();
  const { user } = useAuth();
  const ownerId = Number(id);
  const area = useLoad(() => api.get<Area>(`/api/user/${ownerId}`), [ownerId]);
  const clips = useLoad(() => api.get<AreaClip[]>(`/api/user/${ownerId}/clips`), [ownerId]);
  const files = useLoad(() => api.get<AreaFile[]>(`/api/user/${ownerId}/files`), [ownerId]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");

  if (!Number.isInteger(ownerId) || ownerId <= 0) return <Navigate to="/user" replace />;
  if (user && ownerId === user.id) return <Navigate to="/user" replace />;

  const act = async (fn: () => Promise<unknown>, ok: string, reload: () => Promise<void>) => {
    setError(null);
    setMessage(null);
    try {
      await fn();
      setMessage(ok);
      await reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  if (area.error) {
    return (
      <div className="page">
        <div className="card" style={{ maxWidth: 560 }}>
          <h3>Området findes ikke</h3>
          <p className="muted small">{area.error}</p>
          <div><Link to="/user" className="btn btn-outline btn-sm">Tilbage til mit område</Link></div>
        </div>
      </div>
    );
  }
  const a = area.data;
  if (!a) return <div className="page"><Loading /></div>;
  const who = name(a.owner);
  const author = a.can_write;
  const role = a.role === "owner" ? "reader" : a.role;

  const startEdit = (c: AreaClip) => {
    setAdding(false);
    setEditing(c.id);
    setTitle(c.title);
    setBody(c.body);
  };

  const saveClip = (e: FormEvent) => {
    e.preventDefault();
    const edit = editing;
    act(async () => {
      if (edit) await api.patch(`/api/user/${ownerId}/clips/${edit}`, { title, body });
      else await api.post(`/api/user/${ownerId}/clips`, { title, body, source: "manual" });
      setEditing(null);
      setAdding(false);
      setTitle("");
      setBody("");
    }, edit ? "Klippet er gemt" : "Klippet er oprettet", async () => { await Promise.all([clips.reload(), area.reload()]); });
  };

  const clipForm: ReactNode = (
    <form className="card muted-bg" onSubmit={saveClip} style={{ marginBottom: 8 }}>
      <label className="field">Titel<input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} /></label>
      <label className="field">Tekst<textarea value={body} onChange={(e) => setBody(e.target.value)} rows={5} /></label>
      <div className="row">
        <button className="btn btn-primary btn-sm">Gem klip</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setEditing(null); setAdding(false); }}>Annullér</button>
      </div>
    </form>
  );

  return (
    <>
      <div className={`banner ${role}`} role="status">
        <div className="banner-inner">
          <span style={{ flex: "1 1 320px" }}>
            Du er i <strong>{who}s</strong> område som <strong>{ROLE_TEXT[role].lower}</strong>.{" "}
            {author ? "Du kan oprette og redigere klip og uploade filer, men ikke slette." : "Du kan se og hente, men ikke ændre noget."}
            {a.expires_at ? ` Adgangen udløber ${fmtDay(a.expires_at)}.` : ""}
          </span>
          <Link to="/user" className="btn btn-outline btn-sm">Tilbage til mit område</Link>
        </div>
      </div>
      <div className="page">
        <div className="spread" style={{ alignItems: "flex-end" }}>
          <div className="row" style={{ "--gap": "16px" } as React.CSSProperties}>
            <Avatar text={who} tone="amber" large />
            <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
              <span className="eyebrow">Delt med dig</span>
              <h1>{who}</h1>
            </div>
          </div>
          <div className="row" style={{ "--gap": "8px" } as React.CSSProperties}>
            <RolePill role={role} label={a.role_label} />
            <span className="pill mono">/user/{ownerId}</span>
          </div>
        </div>
        <Alert kind="success">{message}</Alert>
        <Alert>{error}</Alert>

        <div className="row" style={{ alignItems: "flex-start", "--gap": "16px" } as React.CSSProperties}>
          <FeatureGate feature="klip">
            <section className="card" aria-labelledby="h-clips" style={{ flex: "1 1 420px", gap: 0 }}>
              <div className="spread" style={{ paddingBottom: 8 }}>
                <h3 id="h-clips">Klip <span className="muted small">({fmtNumber(a.stats.clips)})</span></h3>
                {author && <button className="btn btn-primary btn-sm" onClick={() => { setEditing(null); setTitle(""); setBody(""); setAdding(!adding); }}>
                  {adding ? "Luk" : "Nyt klip"}
                </button>}
              </div>
              {adding && clipForm}
              <Alert>{clips.error}</Alert>
              {clips.data?.length === 0 && <p className="muted small">Ingen klip.</p>}
              {clips.data?.map((c) => editing === c.id ? <div key={c.id}>{clipForm}</div> : (
                <div key={c.id} className="list-row">
                  <div className="stack" style={{ flex: "1 1 220px", minWidth: 0, "--gap": "2px" } as React.CSSProperties}>
                    <strong className="small" style={{ wordBreak: "break-word" }}>{c.title}</strong>
                    {c.body && <span className="small" style={{ color: "#334155", whiteSpace: "pre-wrap", maxHeight: 60, overflow: "hidden" }}>{c.body}</span>}
                    <span className="muted tiny">Gemt {fmtDate(c.created_at)}{edited(c) ? ` · ændret ${fmtDate(c.updated_at)}` : ""}</span>
                  </div>
                  {author && <button className="btn btn-outline btn-sm" onClick={() => startEdit(c)}>Redigér</button>}
                  <button className="btn btn-outline btn-sm" onClick={() => navigator.clipboard.writeText(c.body)}>Kopiér</button>
                </div>
              ))}
            </section>
          </FeatureGate>

          <FeatureGate feature="filer">
            <section className="card" aria-labelledby="h-files" style={{ flex: "1 1 420px", gap: 0 }}>
              <div className="spread" style={{ paddingBottom: 8 }}>
                <h3 id="h-files">Filer <span className="muted small">({fmtNumber(a.stats.files)} · {fmtBytes(a.stats.bytes)})</span></h3>
                {author && (
                  <label className="btn btn-outline btn-sm">
                    Upload
                    <input type="file" hidden onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      if (file) act(async () => {
                        const form = new FormData();
                        form.append("file", file);
                        await api.post(`/api/user/${ownerId}/files`, form);
                      }, `${file.name} er uploadet til ${who}s hjem`, async () => { await Promise.all([files.reload(), area.reload()]); });
                    }} />
                  </label>
                )}
              </div>
              <Alert>{files.error}</Alert>
              {files.data?.length === 0 && <p className="muted small">Ingen filer.</p>}
              {files.data?.map((f) => (
                <div key={f.id} className="list-row" style={{ flexWrap: "nowrap" }}>
                  <div className="stack" style={{ flex: "1 1 auto", minWidth: 0, "--gap": "2px" } as React.CSSProperties}>
                    <strong className="small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.filename}</strong>
                    <span className="muted tiny">{fmtBytes(f.size_bytes)} · {fmtDay(f.updated_at ?? f.created_at)}{folderOf(f) !== "/" ? ` · ${folderOf(f)}` : ""}</span>
                  </div>
                  <button className="btn btn-outline btn-sm" onClick={async () => {
                    try {
                      download((await api.get<{ url: string }>(`/api/user/${ownerId}/files/${f.id}/link`)).url);
                    } catch (e) {
                      setError(e instanceof ApiError ? e.message : errorText(e));
                    }
                  }}>Hent</button>
                </div>
              ))}
            </section>
          </FeatureGate>
        </div>

        <Alert kind="info">
          {who}s indstillinger, sikkerhed og deling er ikke en del af det, du har adgang til.
          Det, du ændrer og henter her, kan {who} se under Seneste aktivitet.
        </Alert>
      </div>
    </>
  );
}
