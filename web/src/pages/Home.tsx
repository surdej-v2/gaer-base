// / — the signed-in dashboard: greeting, key numbers, the assistant, recent activity (a table with the row's
// tools on the left), areas shared with you and the state of your security. Anonymous visitors go to /w/.
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { useAiStatus } from "../ai";
import { FeatureGate } from "../features";
import { PasskeyNudge } from "../security";
import { Icon, useShell } from "../shell";
import { Alert, fmtBytes, fmtDate, fmtNumber, Loading, useLoad } from "../ui";
import { activityText, Avatar, name, type Activity, type Overview } from "./userAreaShared";

function greeting(): string {
  const h = new Date().getHours();
  return h < 10 ? "Godmorgen" : h < 17 ? "Goddag" : "Godaften";
}

const SUGGESTIONS = [
  "Hvad er nyt i mit område?",
  "Opsummer de klip, jeg har gemt i denne uge",
  "Hvor slår jeg «Kræv passkey» til?",
];

/** Where an activity row leads. */
function hrefFor(a: Activity): string {
  if (a.table === "clips") return a.subject ? `/klip?q=${encodeURIComponent(a.subject)}` : "/klip";
  if (a.table === "files" || a.table === "file_folders") return "/filer";
  return "/user/deling";
}

function kindFor(a: Activity): { label: string; className: string } {
  if (a.table === "clips") return { label: "Klip", className: "pill blue" };
  if (a.table === "files" || a.table === "file_folders") return { label: "Fil", className: "pill" };
  return { label: "Deling", className: "pill amber" };
}

function Ask() {
  const { openAi } = useShell();
  const [q, setQ] = useState("");
  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!q.trim()) return;
    openAi({ prompt: q.trim(), send: true });
    setQ("");
  };
  return (
    <section className="card" aria-labelledby="h-ask" style={{ gap: 12 }}>
      <div className="row" style={{ "--gap": "10px" } as React.CSSProperties}>
        <span className="ai-badge"><Icon name="sparkle" size={16} /></span>
        <h2 id="h-ask" style={{ fontSize: 17, letterSpacing: 0 }}>Spørg assistenten</h2>
        <span className="tiny muted" style={{ marginLeft: "auto" }}>Samtalen gemmes ikke</span>
      </div>
      <form className="ask" onSubmit={submit}>
        <label htmlFor="ask-q" className="sr-only">Spørgsmål til assistenten</label>
        <textarea id="ask-q" rows={2} value={q} placeholder="Fx: Find kontrakterne i mine filer"
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
        <button className="btn btn-primary" disabled={!q.trim()} aria-label="Send"><Icon name="chat" size={18} /></button>
      </form>
      <div className="chips">
        {SUGGESTIONS.map((s) => <button key={s} type="button" className="chip" onClick={() => openAi({ prompt: s, send: true })}>{s}</button>)}
      </div>
    </section>
  );
}

function RecentActivity({ activity }: { activity: Activity[] }) {
  const { aiEnabled, openAi } = useShell();
  const rows = activity.slice(0, 8);
  return (
    <section className="data-card" aria-labelledby="h-recent">
      <div className="data-bar">
        <h2 id="h-recent">Seneste aktivitet</h2>
        <Link to="/user" className="small end" style={{ fontWeight: 600 }}>Alt i Mit område</Link>
      </div>
      {rows.length === 0 ? <p className="muted small" style={{ padding: 16 }}>Intet endnu. Gem et klip eller upload en fil.</p> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th className="tools"><span className="sr-only">Værktøjer</span></th><th>Hvad</th><th className="hide-sm">Type</th><th className="hide-sm">Hvornår</th></tr></thead>
            <tbody>
              {rows.map((a, i) => {
                const who = a.mine ? "Du" : a.actor?.name || "En bruger";
                const kind = kindFor(a);
                const label = a.subject ?? kind.label;
                return (
                  <tr key={i}>
                    <td className="tools"><span>
                      <Link to={hrefFor(a)} className="icon-btn" aria-label={`Åbn ${label}`} title="Åbn"><Icon name="eye" size={16} /></Link>
                      {aiEnabled && (
                        <button className="icon-btn" aria-label={`Spørg assistenten om ${label}`} title="Spørg assistenten"
                                onClick={() => openAi({ prompt: `Fortæl mig om ${kind.label.toLowerCase()} «${label}».` })}>
                          <Icon name="sparkle" size={16} />
                        </button>
                      )}
                    </span></td>
                    <td className="wrap"><span className="title">{who}</span> {activityText(a)}<div className="tiny muted show-sm">{fmtDate(a.ts)}</div></td>
                    <td className="hide-sm"><span className={kind.className}>{kind.label}</span></td>
                    <td className="muted num hide-sm" style={{ whiteSpace: "nowrap" }}>{fmtDate(a.ts)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function Home() {
  const { user, scope, loading } = useAuth();
  const { aiEnabled } = useShell();
  const ai = useAiStatus();
  const ov = useLoad(() => api.get<Overview>("/api/user/overview"));

  if (loading) return null;
  if (!user || scope !== "full") {
    // Anonymous visitors belong on the public website (the server redirects / there as well).
    window.location.replace("/w/");
    return null;
  }

  const first = (user.display_name || "").split(" ")[0];
  const today = new Date().toLocaleDateString("da-DK", { weekday: "long", day: "numeric", month: "long" });
  const d = ov.data;
  const pct = ai.status?.limit ? Math.min(100, Math.round(((ai.status.used ?? 0) / ai.status.limit) * 100)) : null;

  return (
    <div className="page" style={{ maxWidth: 1240 }}>
      <div className="spread" style={{ alignItems: "flex-end" }}>
        <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
          <span className="eyebrow">{today}</span>
          <h1 style={{ fontSize: "clamp(26px, 3.4vw, 34px)" }}>{greeting()}{first ? `, ${first}` : ""}</h1>
        </div>
      </div>
      <PasskeyNudge />
      <Alert>{ov.error}</Alert>
      {!d ? <Loading /> : (
        <>
          <div className="kpis">
            <FeatureGate feature="klip">
              <Link to="/klip" className="kpi">
                <span className="label">Klip</span>
                <span className="stat">{fmtNumber(d.stats.clips)}</span>
                <span className="small" style={{ color: d.stats.clips_week ? "#15803d" : "#475569" }}>{fmtNumber(d.stats.clips_week)} nye de sidste 7 dage</span>
              </Link>
            </FeatureGate>
            <FeatureGate feature="filer">
              <Link to="/filer" className="kpi">
                <span className="label">Filer</span>
                <span className="stat">{fmtNumber(d.stats.files)}</span>
                <span className="small muted">{fmtBytes(d.stats.bytes)} i dit hjem</span>
              </Link>
            </FeatureGate>
            <Link to="/user" className="kpi">
              <span className="label">Delt med dig</span>
              <span className="stat">{fmtNumber(d.shared_with_me.length)}</span>
              <span className="small muted">{d.shared_with_me.length === 1 ? "område" : "områder"} du har adgang til</span>
            </Link>
            {aiEnabled && pct !== null ? (
              <Link to="/konto/forbrug" className="kpi">
                <span className="label">AI-forbrug denne måned</span>
                <span className="stat">{pct} %</span>
                <span className="bar" aria-hidden><div style={{ width: `${pct}%` }} /></span>
              </Link>
            ) : (
              <Link to="/user/deling" className="kpi">
                <span className="label">Har adgang til dit område</span>
                <span className="stat">{fmtNumber(d.access.readers + d.access.authors)}</span>
                <span className="small muted">{d.access.pending ? `${d.access.pending} afventer` : "læsere og forfattere"}</span>
              </Link>
            )}
          </div>

          <div className="dash-cols">
            <div className="dash-main">
              {aiEnabled && <Ask />}
              <RecentActivity activity={d.activity} />
            </div>

            <aside className="dash-rail">
              <section className="card" aria-labelledby="h-shared" style={{ gap: 0 }}>
                <div className="spread" style={{ paddingBottom: 6 }}>
                  <h3 id="h-shared">Delt med dig</h3>
                  <Link to="/user" className="small" style={{ fontWeight: 600 }}>Alle</Link>
                </div>
                {d.shared_with_me.length === 0 && <p className="muted small">Ingen har delt deres område med dig.</p>}
                {d.shared_with_me.slice(0, 5).map((s) => (
                  <Link key={s.id} to={`/user/${s.owner.id}`} className="list-row" style={{ flexWrap: "nowrap", padding: "10px 0" }}>
                    <Avatar text={name(s.owner)} tone="green" />
                    <span className="stack" style={{ flex: "1 1 auto", minWidth: 0, "--gap": "0" } as React.CSSProperties}>
                      <strong className="small">{name(s.owner)}</strong>
                      <span className="tiny muted">Du er {s.role_label.toLowerCase()}{s.expires_at ? ` · udløber ${fmtDate(s.expires_at)}` : ""}</span>
                    </span>
                  </Link>
                ))}
              </section>

              <section className="card" aria-labelledby="h-sec" style={{ gap: 10 }}>
                <h3 id="h-sec">Sikkerhed</h3>
                <div className="row small" style={{ "--gap": "8px", flexWrap: "nowrap" } as React.CSSProperties}>
                  <span style={{ color: user.passkeys ? "#15803d" : "#b45309", display: "inline-flex" }}><Icon name={user.passkeys ? "check" : "alert"} size={16} /></span>
                  <span style={{ flex: 1 }}>{user.passkeys ? `${user.passkeys} ${user.passkeys === 1 ? "passkey" : "passkeys"}` : "Ingen passkey endnu"}</span>
                  {!user.passkeys && <Link to="/konto#sikkerhed">Opret</Link>}
                </div>
                <div className="row small" style={{ "--gap": "8px", flexWrap: "nowrap" } as React.CSSProperties}>
                  <span style={{ color: user.mfa.required ? "#15803d" : "#b45309", display: "inline-flex" }}><Icon name={user.mfa.required ? "check" : "alert"} size={16} /></span>
                  <span style={{ flex: 1 }}>«Kræv passkey» er {user.mfa.required ? "slået til" : "slået fra"}</span>
                  {!user.mfa.required && <Link to="/konto#sikkerhed">Slå til</Link>}
                </div>
              </section>

              <section className="card" aria-labelledby="h-more" style={{ gap: 2 }}>
                <h3 id="h-more" style={{ paddingBottom: 6 }}>Brug Gær andre steder</h3>
                <a href="/m" className="side-link" style={{ padding: "0 6px", margin: "0 -6px", width: "auto" }}>
                  <Icon name="phone" size={18} /><span style={{ flex: 1 }}>Mobil-app</span><span className="mono tiny muted">/m</span>
                </a>
                <Link to="/develop" className="side-link" style={{ padding: "0 6px", margin: "0 -6px", width: "auto" }}>
                  <Icon name="code" size={18} /><span style={{ flex: 1 }}>Udvikling — promptkatalog</span><span className="mono tiny muted">/develop</span>
                </Link>
                <a href="/w/" className="side-link" style={{ padding: "0 6px", margin: "0 -6px", width: "auto" }}>
                  <Icon name="globe" size={18} /><span style={{ flex: 1 }}>Den offentlige side</span><span className="mono tiny muted">/w</span>
                </a>
                {user.role === "admin" && (
                  <a href="/admin" className="side-link" style={{ padding: "0 6px", margin: "0 -6px", width: "auto" }}>
                    <Icon name="lock" size={18} /><span style={{ flex: 1 }}>Administration</span>
                  </a>
                )}
              </section>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
