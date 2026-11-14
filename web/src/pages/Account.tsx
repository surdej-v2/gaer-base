import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { Alert, fmtDate, fmtNumber, PageHeader, useLoad } from "../ui";
import AccountSecurity from "./AccountSecurity";
import LinkedLogins from "./LinkedLogins";
import ContactChange from "./ContactChange";
import McpAccess from "./McpAccess";

type Profile = { locale: string | null; time_zone: string | null };
type Session = {
  id: number; created_at: string; last_seen_at: string; ip: string | null; user_agent: string; current: boolean;
  mcp_client: string | null; mcp_scope: string | null;
};

/** inArea: shown inside /user/indstillinger, which has its own page frame and heading. */
export default function Account({ inArea = false }: { inArea?: boolean }) {
  const { user, refresh, config } = useAuth();
  const [name, setName] = useState(user?.display_name ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const profile = useLoad(() => api.get<Profile>("/api/me/profile"));
  const [locale, setLocale] = useState("");
  const [timeZone, setTimeZone] = useState("");
  const [avatarVersion, setAvatarVersion] = useState(0);
  const sessions = useLoad(() => api.get<Session[]>("/api/me/sessions"));
  const level = useLoad(() => api.get<{ level: number }>("/api/features"));
  const devices = sessions.data?.filter((s) => !s.mcp_client) ?? [];
  const mcpClients = sessions.data?.filter((s) => s.mcp_client) ?? [];

  useEffect(() => setName(user?.display_name ?? ""), [user?.display_name]);
  useEffect(() => {
    setLocale(profile.data?.locale ?? "");
    setTimeZone(profile.data?.time_zone ?? "");
  }, [profile.data]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setError(null);
    setMessage(null);
    try {
      await fn();
      if (ok) setMessage(ok);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div className={inArea ? "stack" : "page"} style={inArea ? ({ "--gap": "24px" } as React.CSSProperties) : undefined}>
      {!inArea && <PageHeader eyebrow="Konto" title="Din konto" />}
      <Alert kind="success">{message}</Alert>
      <Alert>{error}</Alert>

      <div className="grid" style={{ "--min": "320px" } as React.CSSProperties}>
        <div className="card" data-deeplink="account-profile">
          <span className="eyebrow">Profil</span>
          <ContactChange kind="email" />
          {(config?.sms_login || user?.phone) && <ContactChange kind="phone" />}
          <div className="row" style={{ "--gap": "12px" } as React.CSSProperties}>
            {user?.avatar_url
              ? <img src={`${user.avatar_url}?v=${avatarVersion}`} alt="" width={56} height={56} style={{ borderRadius: "50%", objectFit: "cover" }} />
              : <span className="pill" aria-hidden>{(user?.display_name || user?.email || "?").slice(0, 1).toUpperCase()}</span>}
            <label className="btn btn-outline btn-sm">
              Skift billede
              <input type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) act(async () => {
                  const form = new FormData();
                  form.append("file", file);
                  await api.put("/api/me/avatar", form);
                  setAvatarVersion(Date.now());
                  await refresh();
                }, "Billedet er gemt");
              }} />
            </label>
            {user?.avatar_url && <button className="btn btn-ghost btn-sm" onClick={() => act(async () => {
              await api.del("/api/me/avatar");
              await refresh();
            }, "Billedet er fjernet")}>Fjern</button>}
          </div>
          <label className="field">
            Navn
            <input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            Sprog
            <select value={locale} onChange={(e) => setLocale(e.target.value)}>
              <option value="">Automatisk</option>
              <option value="da">Dansk</option>
              <option value="en">English</option>
            </select>
          </label>
          <label className="field">
            Tidszone
            <input list="time-zones" value={timeZone} placeholder={Intl.DateTimeFormat().resolvedOptions().timeZone}
                   onChange={(e) => setTimeZone(e.target.value)} />
            <datalist id="time-zones">{timeZones().map((z) => <option key={z} value={z} />)}</datalist>
          </label>
          <div>
            <button className="btn btn-primary" onClick={() => act(async () => {
              await api.patch("/api/me/profile", { display_name: name, locale, time_zone: timeZone });
              await refresh();
            }, "Profilen er gemt")}>Gem</button>
          </div>
        </div>

        <AccountSecurity />
      </div>

      <LinkedLogins />

      <AiUsageCard />

      <div className="card" data-deeplink="account-sessions">
        <span className="eyebrow">Aktive logins</span>
        {devices.map((s) => (
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

      <div className="card" data-deeplink="account-mcp">
        <span className="eyebrow">MCP-klienter</span>
        <p className="small muted">
          AI-assistenter som Claude, Copilot og ChatGPT, du har forbundet til Gær. De ser kun dine egne data, og sletning kræver din bekræftelse.
          Fjerner du en klient, mister den adgangen med det samme.
        </p>
        {mcpClients.length === 0 && <p className="small muted">Ingen forbundne klienter.</p>}
        {mcpClients.map((s) => (
          <div key={s.id} className="spread" style={{ borderTop: "1px solid var(--hm-border)", paddingTop: 10 }}>
            <div className="stack" style={{ "--gap": "2px", minWidth: 0 } as React.CSSProperties}>
              <strong className="small">{s.mcp_client}</strong>
              <span className="muted tiny">
                {s.mcp_scope === "mcp:admin" ? "Admin (/mcp/admin)" : "Appen (/mcp)"} · Forbundet {fmtDate(s.created_at)} · Sidst brugt {fmtDate(s.last_seen_at)}
              </span>
            </div>
            <button className="btn btn-outline btn-sm" onClick={() => {
              if (confirm(`Fjern ${s.mcp_client}? Klienten skal logge ind igen for at få adgang.`)) act(async () => {
                await api.del(`/api/me/sessions/${s.id}`);
                await sessions.reload();
              }, "Klienten er fjernet");
            }}>Fjern</button>
          </div>
        ))}
      </div>

      <McpAccess />

      <div className="card" data-deeplink="account-ring">
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

function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  return intl.supportedValuesOf?.("timeZone") ?? ["Europe/Copenhagen", "Europe/London", "UTC"];
}

type AiStatus = { enabled: boolean; used?: number; limit?: number; warning?: number | null; blocked?: boolean; status?: string };

/** Short view of this month's AI use; the full page is /konto/forbrug. Hidden when AI is off. */
function AiUsageCard() {
  const status = useLoad(() => api.get<AiStatus>("/api/ai/status"));
  const s = status.data;
  if (!s?.enabled || !s.limit) return null;
  const pct = Math.min(100, ((s.used ?? 0) / s.limit) * 100);
  const color = s.blocked || pct >= 100 ? "#dc2626" : s.warning ? "#f97316" : "#2563eb";
  return (
    <div className="card" data-deeplink="account-ai-usage">
      <div className="spread">
        <span className="eyebrow">AI-forbrug denne måned</span>
        <Link to="/konto/forbrug" className="small">Se dit forbrug →</Link>
      </div>
      <div className="row" style={{ alignItems: "baseline" }}>
        <span className="num" style={{ fontSize: 24, fontWeight: 700 }}>{fmtNumber(s.used ?? 0)}</span>
        <span className="muted small">af {fmtNumber(s.limit)} tokens</span>
      </div>
      <div role="meter" aria-label="Brugt af månedens AI-loft" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}
        style={{ height: 8, background: "#f1f5f9", borderRadius: 9999, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color }} />
      </div>
      {s.blocked && <span className="small" style={{ color: "#991b1b" }}>Du har brugt månedens AI-budget.</span>}
      {!s.blocked && s.warning && <span className="small" style={{ color: "#92400e" }}>Du har passeret {s.warning} % af månedens budget.</span>}
    </div>
  );
}
