// The user drawer on /admin/users (screen 03): facts and actions for one user.
import { useNavigate } from "react-router-dom";
import { Ban, KeyRound, LockOpen, LogOut, Phone, ScrollText, ShieldCheck, ShieldOff, type LucideIcon } from "lucide-react";
import { api, errorText } from "../../api";
import { useAuth } from "../../auth";
import { useAdmin } from "../context";
import { dateLabel, lastLabel, phoneLabel, RING, sectionLabel, SANS } from "../theme";
import { CloseBtn, Drawer, Pill, useToast } from "../ui";
import { isTest, userView, type AdminUser } from "../users";

type Action = { label: string; hint: string; icon: LucideIcon; run: () => void; danger?: boolean; disabled?: boolean };

export default function UserDrawer({ user: u, users, grace, onClose, onChanged }: {
  user: AdminUser; users: AdminUser[]; grace: number; onClose: () => void; onChanged: () => void;
}) {
  const { user: me } = useAuth();
  const { impersonating, refreshImpersonation } = useAdmin();
  const navigate = useNavigate();
  const say = useToast();
  const v = userView(u, grace);
  const test = isTest(u);
  const isMe = u.id === me?.id;
  const admins = users.filter((x) => x.role === "admin" && !isTest(x) && !x.blocked_at).length;
  const lastAdmin = admins <= 1 && !test;

  const run = (call: () => Promise<unknown>, done: string) => async () => {
    try {
      await call();
      say(done);
      onChanged();
    } catch (e) {
      say(errorText(e));
    }
  };

  const actions: Action[] = [
    u.role === "admin"
      ? { label: "Fjern admin", hint: lastAdmin ? "Der skal altid være mindst én admin" : "Brugeren mister adgang til /admin", icon: ShieldOff,
          run: run(() => api.patch(`/api/admin/users/${u.id}/role`, { role: "user" }), "Admin-rollen er fjernet"), disabled: lastAdmin }
      : { label: "Gør til admin", hint: "Giver adgang til /admin", icon: ShieldCheck,
          run: run(() => api.patch(`/api/admin/users/${u.id}/role`, { role: "admin" }), "Brugeren er nu admin") },
    u.blocked_at
      ? { label: "Ophæv spærring", hint: "Brugeren kan logge ind igen", icon: LockOpen,
          run: run(() => api.post(`/api/admin/users/${u.id}/unblock`), "Spærringen er ophævet") }
      : { label: "Spær", hint: isMe ? "Du kan ikke spærre dig selv" : "Logges ud alle steder og kan ikke logge ind", icon: Ban, danger: true, disabled: isMe,
          run: run(() => api.post(`/api/admin/users/${u.id}/block`), "Brugeren er spærret og logget ud") },
    { label: "Nulstil passkeys", hint: u.passkeys ? "Skal oprette ny passkey efter login med engangskode" : "Ingen passkeys", icon: KeyRound, disabled: !u.passkeys,
      run: run(() => api.post(`/api/admin/users/${u.id}/reset-passkeys`), "Passkeys er nulstillet") },
    { label: "Log ud alle steder", hint: u.active_sessions ? `${u.active_sessions} aktive sessioner` : "Ingen aktive sessioner", icon: LogOut,
      disabled: !u.active_sessions, run: run(() => api.post(`/api/admin/users/${u.id}/revoke-sessions`), "Brugeren er logget ud alle steder") },
    { label: "Ret telefonnummer", hint: "Rettelse efter GDPR. Brugeren logges ud alle steder", icon: Phone, disabled: test,
      run: () => {
        const phone = prompt(`Nyt telefonnummer for ${phoneLabel(u.phone)}`, "");
        if (phone) void run(() => api.patch(`/api/admin/users/${u.id}/phone`, { phone }), "Telefonnummeret er rettet")();
      } },
    { label: "Se auth-log", hint: `/admin/logs/auth?user_id=${u.id}`, icon: ScrollText, run: () => navigate(`/logs/auth?user_id=${u.id}`) },
  ];

  const ring = u.feature_ring;
  const facts: [string, string][] = [
    ["Telefon", test ? "—" : phoneLabel(u.phone)],
    ["Oprettet", dateLabel(u.created_at)],
    ["Sidst logget ind", lastLabel(u.last_login_at)],
    ["Engangskoder brugt", test ? "—" : `${u.grace_logins_used} af ${grace}`],
    ["Sessioner", String(u.active_sessions)],
    ["Feature-niveau", ring ? `Ring ${ring} · ${RING[ring][0]}` : "Ring 4 · standard"],
  ];

  const active = impersonating?.id === u.id;
  const toggleImpersonation = async () => {
    try {
      if (active) {
        await api.post("/api/impersonation/stop");
        say("Efterligning stoppet");
      } else {
        await api.post("/api/impersonation/start", { user_id: u.id });
        say(`Efterligner ${v.name}. Appen på / viser nu testkontoen.`);
      }
      await refreshImpersonation();
    } catch (e) {
      say(errorText(e));
    }
  };

  return (
    <Drawer width={420} onClose={onClose} label="Bruger">
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "18px 20px", borderBottom: "1px solid #e2e8f0" }}>
        <span style={{ width: 40, height: 40, flex: "none", borderRadius: 9999, background: v.avatar, color: "#ffffff", display: "flex", alignItems: "center",
          justifyContent: "center", fontSize: 14, fontWeight: 700 }}>{v.initials}</span>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <span style={{ fontSize: 17, fontWeight: 700 }}>{v.name}</span>
          <span style={{ fontSize: 12, color: "#64748b", fontVariantNumeric: "tabular-nums" }}>{v.sub}</span>
        </div>
        <CloseBtn onClick={onClose} />
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 20 }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <Pill tone={v.role[1]}>{v.role[0]}</Pill>
          <Pill tone={v.status[1]}>{v.status[0]}</Pill>
          <Pill tone={v.login[1]}>{v.login[0]}</Pill>
        </div>
        <div style={{ display: "flex", flexDirection: "column", border: "1px solid #e2e8f0", borderRadius: 12, overflow: "hidden", fontSize: 13 }}>
          {facts.map(([k, val]) => (
            <div key={k} style={{ display: "flex", gap: 10, padding: "10px 14px", borderBottom: "1px solid #f1f5f9" }}>
              <span style={{ width: 130, flex: "none", color: "#64748b" }}>{k}</span>
              <span style={{ fontWeight: 500, fontVariantNumeric: "tabular-nums" }}>{val}</span>
            </div>
          ))}
        </div>
        {test && (
          <div style={{ padding: "12px 14px", borderRadius: 12, background: "#fffbeb", border: "1px solid rgba(245,158,11,0.35)", display: "flex", alignItems: "center",
            gap: 10, fontSize: 13, color: "#78350f" }}>
            <span style={{ flex: 1, textWrap: "pretty" }}>Testkonto. Kan kun bruges gennem efterligning.</span>
            <button type="button" onClick={toggleImpersonation} style={{ height: 32, padding: "0 12px", borderRadius: 8, border: 0, cursor: "pointer",
              background: "#78350f", color: "#ffffff", font: `500 13px ${SANS}`, whiteSpace: "nowrap" }}>
              {active ? "Stop efterligning" : "Skift til"}
            </button>
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={sectionLabel}>Handlinger</span>
          {actions.map((a) => (
            <button key={a.label} type="button" onClick={a.run} disabled={a.disabled} className="tg-row"
              style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", minHeight: 40, padding: "8px 12px", boxSizing: "border-box", borderRadius: 8,
                border: `1px solid ${a.danger ? "rgba(220,38,38,0.3)" : "#e2e8f0"}`, background: "#ffffff", cursor: "pointer", textAlign: "left",
                font: `500 13px ${SANS}`, color: a.danger ? "#b91c1c" : "#020817", ...(a.disabled ? { opacity: 0.45, pointerEvents: "none" } : {}) }}>
              <a.icon size={16} strokeWidth={2} style={{ flex: "none" }} />
              <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: 1 }}>
                <span>{a.label}</span>
                <span style={{ fontSize: 12, fontWeight: 400, color: "#64748b" }}>{a.hint}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </Drawer>
  );
}
