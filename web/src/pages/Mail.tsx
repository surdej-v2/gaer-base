// /user/mail (and /user/:id/mail for your own id): your mailbox, shared mailboxes, calendars, address books, send
// identities, client setup, app passwords and recent sends. API: /api/mail (server/app/routes/mail.py).
// Reading mail, events and contacts happens in the user's own clients; this page manages them.
import { useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { Alert, fmtBytes, fmtDate, Loading, useLoad } from "../ui";
import { OwnHeader } from "./UserArea";

type Mailbox = {
  id: number; address: string; status: "provisioning" | "active" | "suspended"; quota_mb: number; used_bytes: number | null;
  requested_at: string; provisioned_at: string | null; domain: string;
};
type Alias = { id: number; address: string; created_at: string };
type AliasRequest = { id: number; address: string; note: string; status: "pending" | "approved" | "declined"; created_at: string };
type Shared = { id: number; address: string; display_name: string | null; role: SharedRole; role_label: string; status: string };
type SharedRole = "reader" | "sender" | "manager";
type Identity = { id: number; mailbox_id: number; from_address: string; display_name: string; signature: string };
type ClientSettings = {
  configured: boolean; username?: string; imap?: Endpoint; smtp?: Endpoint & { alt_port: number; alt_security: string };
  jmap?: { url: string }; caldav?: { url: string }; carddav?: { url: string }; autoconfig?: string;
};
type Endpoint = { host: string; port: number; security: string };
type Run = { id: number; status: string; expired: number; finished_at: string | null; started_at: string };
type Overview = {
  enabled: boolean; mailbox: Mailbox | null; aliases: Alias[]; alias_requests: AliasRequest[];
  retention: { days: number; applies_to: string; scope: string } | null; shared: Shared[]; identities: Identity[];
  client: ClientSettings; archive_export: boolean; last_expiry_run: Run | null; managed: number[];
};
type CollRole = "owner" | "viewer" | "editor" | "manager";
type Share = { user_id: number; display_name: string | null; email: string; role: Exclude<CollRole, "owner">; added_at: string };
type Collection = {
  id: string; type: "calendar" | "address_book"; kind: "personal" | "shared"; name: string; colour: string | null;
  timezone: string | null; is_default: boolean; freebusy: boolean; entry_count: number | null; role: CollRole;
  role_label: string; shares: Share[];
};
type AppPassword = { id: number; name: string; created_at: string; last_used_at: string | null };
type Activity = { message_id: string | null; from_address: string; recipient_domain: string | null; status: string; sent_at: string; at: string };
type Member = { user_id: number; display_name: string | null; email: string; role: SharedRole; has_mailbox: boolean };
type Availability = { publish: boolean; working_hours: { days: number[]; start: string; end: string }; timezone: string };

const SHARED_ROLES: [SharedRole, string, string][] = [
  ["reader", "Læser", "Læser og arkiverer mail"],
  ["sender", "Afsender", "Kan også sende som adressen"],
  ["manager", "Ansvarlig", "Kan også tilføje og fjerne medlemmer"],
];
const COLL_ROLES: [Exclude<CollRole, "owner">, string][] = [["viewer", "Se"], ["editor", "Redigere"], ["manager", "Ansvarlig"]];
const STATUS: Record<string, [string, string]> = {
  sent: ["Sendt", ""], delivered: ["Leveret", "green"], bounced: ["Afvist", "red"], complained: ["Klage", "amber"],
};
const DAYS = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"];
const COLOURS = ["#2563eb", "#16a34a", "#dc2626", "#d97706", "#7c3aed", "#0891b2", "#475569"];
const css = (v: Record<string, string | number>) => v as React.CSSProperties;

const BASE = "/api/mail/users/me";

function Section({ id, title, aside, children, lead }: { id: string; title: string; aside?: ReactNode; lead?: ReactNode; children: ReactNode }) {
  return (
    <section className="card" aria-labelledby={`h-${id}`} data-deeplink={`mail-${id}`} style={{ gap: 12 }}>
      <div className="spread">
        <h3 id={`h-${id}`}>{title}</h3>
        {aside}
      </div>
      {lead && <p className="muted small" style={{ margin: 0 }}>{lead}</p>}
      {children}
    </section>
  );
}

function Copy({ value, label = "Kopiér" }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="btn btn-outline btn-sm" onClick={() => {
      navigator.clipboard.writeText(value).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); });
    }}>{done ? "Kopieret" : label}</button>
  );
}

/** Runs an action, shows its error, reloads. */
function useAction(reload: () => void) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, after?: () => void) => {
    setError(null);
    setBusy(true);
    try {
      await fn();
      after?.();
      reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return { error, busy, run, setError };
}

/** /user/:id/mail: only your own id. Anyone else's is "not found", like the API. */
export function UserMailById() {
  const { id } = useParams();
  const { user } = useAuth();
  if (user && (id === "me" || id === String(user.id))) return <UserMail />;
  return (
    <div className="page">
      <h1>Ikke fundet</h1>
      <p className="muted">Siden findes ikke, eller du har ikke adgang til den.</p>
    </div>
  );
}

export default function UserMail() {
  const ov = useLoad(() => api.get<Overview>(`${BASE}`));
  const d = ov.data;
  if (ov.error?.includes("ikke sat op")) return <Navigate to="/user" replace />;
  return (
    <div className="page">
      <OwnHeader title="Mail" path="/user/mail" />
      <Alert>{ov.error}</Alert>
      {!d ? <Loading /> : !d.mailbox ? (
        <div className="card">
          <h3>Du har ingen postkasse endnu</h3>
          <p className="muted small">En administrator opretter postkassen. Spørg din administrator, hvis du mangler den.</p>
        </div>
      ) : d.mailbox.status === "provisioning" ? (
        <div className="card" data-deeplink="mail-provisioning">
          <h3>Din postkasse bliver sat op</h3>
          <p className="muted small">
            {d.mailbox.address} blev bestilt {fmtDate(d.mailbox.requested_at)}. Det tager normalt under et minut; siden
            opdateres, når du åbner den igen.
          </p>
        </div>
      ) : (
        <>
          {d.mailbox.status === "suspended" && (
            <Alert kind="warn">Postkassen er suspenderet: du kan ikke logge ind, og der kommer ikke ny mail ind. Kontakt en administrator.</Alert>
          )}
          <div className="row" style={css({ alignItems: "flex-start", "--gap": "16px" })}>
            <div className="stack" style={css({ flex: "1 1 420px", minWidth: 0, "--gap": "16px" })}>
              <MyMailbox d={d} reload={ov.reload} />
              <SharedMailboxes d={d} reload={ov.reload} />
              <Identities identities={d.identities} reload={ov.reload} />
            </div>
            <div className="stack" style={css({ flex: "1 1 420px", minWidth: 0, "--gap": "16px" })}>
              <ClientSetup client={d.client} />
              <AppPasswords disabled={d.mailbox.status !== "active"} />
              <RecentActivity identities={d.identities} lastRun={d.last_expiry_run} />
              {d.archive_export && <ArchiveExport />}
            </div>
          </div>
          <Collections type="calendar" disabled={d.mailbox.status !== "active"} />
          <Collections type="address_book" disabled={d.mailbox.status !== "active"} />
        </>
      )}
    </div>
  );
}

// ── Min postkasse ───────────────────────────────────────────────────────────

function MyMailbox({ d, reload }: { d: Overview; reload: () => void }) {
  const mb = d.mailbox!;
  const [alias, setAlias] = useState("");
  const [note, setNote] = useState("");
  const [asking, setAsking] = useState(false);
  const act = useAction(reload);
  const used = mb.used_bytes ?? 0;
  const quota = mb.quota_mb * 1024 * 1024;
  const pct = quota ? Math.min(100, Math.round((used / quota) * 100)) : 0;
  const pending = d.alias_requests.filter((r) => r.status === "pending");
  const ask = (e: FormEvent) => {
    e.preventDefault();
    act.run(() => api.post(`${BASE}/alias-requests`, { address: alias.includes("@") ? alias : `${alias}@${mb.domain}`, note }),
      () => { setAlias(""); setNote(""); setAsking(false); });
  };
  return (
    <Section id="mailbox" title="Min postkasse" aside={<span className={`pill ${mb.status === "active" ? "green" : "amber"}`}>{mb.status === "active" ? "Aktiv" : "Suspenderet"}</span>}>
      <div className="row" style={css({ "--gap": "8px" })}>
        <strong className="mono" style={{ fontSize: 18, wordBreak: "break-all" }}>{mb.address}</strong>
        <Copy value={mb.address} />
      </div>
      {d.aliases.length > 0 && (
        <div className="stack" style={css({ "--gap": "4px" })}>
          <span className="eyebrow">Aliasser</span>
          {d.aliases.map((a) => <span key={a.id} className="mono small">{a.address}</span>)}
        </div>
      )}
      <div className="stack" style={css({ "--gap": "6px" })}>
        <div className="spread small"><span>Lager</span><span className="muted">{fmtBytes(used)} af {fmtBytes(quota)}</span></div>
        <div className="bar" role="meter" aria-label="Brugt lager" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div style={{ width: `${pct}%`, height: "100%", background: pct > 90 ? "#dc2626" : "var(--hm-primary)" }} />
        </div>
      </div>
      <Alert kind="info">
        Mail slettes efter {d.retention?.days ?? 30} dage{d.retention?.applies_to === "trash" ? " i papirkurven" : d.retention?.applies_to === "junk" ? " i spam" : ""}.
        Kalendere og kontakter slettes aldrig automatisk.
      </Alert>
      <Alert>{act.error}</Alert>
      {pending.map((r) => <p key={r.id} className="muted small" style={{ margin: 0 }}>Du har bedt om aliasset <span className="mono">{r.address}</span> {fmtDate(r.created_at)}. En administrator svarer.</p>)}
      {asking ? (
        <form className="stack" onSubmit={ask} style={css({ "--gap": "8px" })}>
          <label className="field">
            Alias
            <span className="row" style={css({ "--gap": "6px", flexWrap: "nowrap" })}>
              <input required value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="salg" style={{ flex: 1 }} />
              {!alias.includes("@") && <span className="muted mono small">@{mb.domain}</span>}
            </span>
          </label>
          <label className="field">Hvorfor (valgfrit)<input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} /></label>
          <div className="row"><button className="btn btn-primary btn-sm" disabled={act.busy}>Send anmodning</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAsking(false)}>Annullér</button></div>
        </form>
      ) : <div><button className="btn btn-outline btn-sm" onClick={() => setAsking(true)}>Bed om et alias</button></div>}
    </Section>
  );
}

// ── Fælles postkasser ───────────────────────────────────────────────────────

function SharedMailboxes({ d, reload }: { d: Overview; reload: () => void }) {
  const { user } = useAuth();
  const [open, setOpen] = useState<number | null>(null);
  const act = useAction(reload);
  return (
    <Section id="shared" title="Fælles postkasser" lead="Postkasser du er medlem af. De ligger under „Delte mapper“ i din mailklient.">
      <Alert>{act.error}</Alert>
      {d.shared.length === 0 && <p className="muted small" style={{ margin: 0 }}>Du er ikke medlem af nogen fælles postkasser.</p>}
      {d.shared.map((s) => (
        <div key={s.id} className="stack" style={css({ "--gap": "8px" })}>
          <div className="list-row" style={{ flexWrap: "nowrap" }}>
            <span className="stack" style={css({ flex: "1 1 auto", minWidth: 0, "--gap": "0" })}>
              <strong className="small">{s.display_name || s.address}</strong>
              <span className="mono tiny muted">{s.address}</span>
            </span>
            <span className={`pill ${s.role === "manager" ? "blue" : s.role === "sender" ? "amber" : ""}`}>{s.role_label}</span>
            {s.role === "manager" && (
              <button className="btn btn-outline btn-sm" onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? "Luk" : "Indstillinger"}</button>
            )}
            <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => {
              if (confirm(`Forlad ${s.address}? Du mister adgangen til postkassen.`)) {
                act.run(() => api.del(`/api/mail/shared/${s.id}/members/${user!.id}`));
              }
            }}>Forlad</button>
          </div>
          {open === s.id && <ManageShared id={s.id} onChange={reload} />}
        </div>
      ))}
    </Section>
  );
}

type SharedDetail = { id: number; address: string; display_name: string | null; members: Member[] | null };

function ManageShared({ id, onChange }: { id: number; onChange: () => void }) {
  const detail = useLoad(() => api.get<SharedDetail>(`/api/mail/shared/${id}`), [id]);
  const act = useAction(() => { detail.reload(); onChange(); });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<SharedRole>("reader");
  const [name, setName] = useState<string | null>(null);
  const m = detail.data;
  if (!m) return <Loading />;
  return (
    <div className="card muted-bg" style={{ gap: 12 }}>
      <Alert>{act.error || detail.error}</Alert>
      <form className="row" onSubmit={(e) => { e.preventDefault(); act.run(() => api.patch(`/api/mail/shared/${id}`, { display_name: name ?? m.display_name }), () => setName(null)); }}>
        <label className="field" style={{ flex: "1 1 220px" }}>Visningsnavn
          <input value={name ?? m.display_name ?? ""} onChange={(e) => setName(e.target.value)} required maxLength={120} /></label>
        <button className="btn btn-outline btn-sm" style={{ alignSelf: "flex-end" }} disabled={act.busy || name === null}>Gem navn</button>
      </form>
      <div className="stack" style={css({ "--gap": "0" })}>
        <span className="eyebrow">Medlemmer</span>
        {(m.members ?? []).map((x) => (
          <div key={x.user_id} className="list-row">
            <span className="stack" style={css({ flex: "1 1 180px", minWidth: 0, "--gap": "0" })}>
              <strong className="small">{x.display_name || x.email}</strong>
              <span className="tiny muted">{x.email}{x.has_mailbox ? "" : " · har ingen postkasse endnu"}</span>
            </span>
            <select aria-label={`Rolle for ${x.email}`} value={x.role} disabled={act.busy}
              onChange={(e) => act.run(() => api.put(`/api/mail/shared/${id}/members/${x.user_id}`, { role: e.target.value }))}>
              {SHARED_ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(() => api.del(`/api/mail/shared/${id}/members/${x.user_id}`))}>Fjern</button>
          </div>
        ))}
      </div>
      <form className="row" style={{ alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); act.run(() => api.post(`/api/mail/shared/${id}/members`, { email, role }), () => setEmail("")); }}>
        <label className="field" style={{ flex: "1 1 200px" }}>Tilføj medlem
          <input type="email" required placeholder="navn@firma.dk" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <select aria-label="Rolle" value={role} onChange={(e) => setRole(e.target.value as SharedRole)}>
          {SHARED_ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button className="btn btn-primary btn-sm" disabled={act.busy}>Tilføj</button>
      </form>
      <p className="muted tiny" style={{ margin: 0 }}>{SHARED_ROLES.map(([, l, desc]) => `${l}: ${desc.toLowerCase()}`).join(" · ")}.</p>
    </div>
  );
}

// ── Afsenderidentiteter ─────────────────────────────────────────────────────

function Identities({ identities, reload }: { identities: Identity[]; reload: () => void }) {
  return (
    <Section id="identities" title="Afsenderidentiteter" lead="De adresser, du kan sende fra: din egen, dine aliasser og fælles postkasser, hvor du er afsender eller ansvarlig.">
      {identities.map((i) => <IdentityRow key={i.id} identity={i} reload={reload} />)}
    </Section>
  );
}

function IdentityRow({ identity, reload }: { identity: Identity; reload: () => void }) {
  const [name, setName] = useState(identity.display_name);
  const [signature, setSignature] = useState(identity.signature);
  const act = useAction(reload);
  const dirty = name !== identity.display_name || signature !== identity.signature;
  return (
    <form className="stack list-row" style={css({ "--gap": "8px", alignItems: "stretch" })}
      onSubmit={(e) => { e.preventDefault(); act.run(() => api.patch(`${BASE}/identities/${identity.id}`, { display_name: name, signature })); }}>
      <strong className="mono small">{identity.from_address}</strong>
      <label className="field">Visningsnavn<input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Signatur<textarea value={signature} maxLength={4000} onChange={(e) => setSignature(e.target.value)} style={{ minHeight: 64 }} /></label>
      <Alert>{act.error}</Alert>
      <div><button className="btn btn-outline btn-sm" disabled={!dirty || act.busy}>Gem</button></div>
    </form>
  );
}

// ── Klienter og app-adgangskoder ────────────────────────────────────────────

function ClientSetup({ client }: { client: ClientSettings }) {
  if (!client.configured) {
    return <Section id="clients" title="Opsætning af klienter"><p className="muted small" style={{ margin: 0 }}>Serverens adresse er ikke sat op endnu.</p></Section>;
  }
  const rows: [string, string][] = [
    ["Brugernavn", client.username!],
    ["IMAP", `${client.imap!.host}:${client.imap!.port} (${client.imap!.security})`],
    ["SMTP", `${client.smtp!.host}:${client.smtp!.port} (${client.smtp!.security}) eller ${client.smtp!.alt_port} (${client.smtp!.alt_security})`],
    ["JMAP", client.jmap!.url],
    ["CalDAV", client.caldav!.url],
    ["CardDAV", client.carddav!.url],
  ];
  return (
    <Section id="clients" title="Opsætning af klienter" lead="Brug en app-adgangskode som adgangskode. Thunderbird finder selv indstillingerne ud fra din adresse; på Android bruger du DAVx⁵ til kalender og kontakter.">
      <div className="stack" style={css({ "--gap": "0" })}>
        {rows.map(([k, v]) => (
          <div key={k} className="list-row" style={{ flexWrap: "nowrap" }}>
            <span className="small" style={{ width: 92, flex: "none", fontWeight: 600 }}>{k}</span>
            <span className="mono tiny" style={{ flex: 1, minWidth: 0, wordBreak: "break-all" }}>{v}</span>
            <Copy value={k === "IMAP" ? client.imap!.host : k === "SMTP" ? client.smtp!.host : v} />
          </div>
        ))}
      </div>
      <div className="row">
        <a className="btn btn-outline btn-sm" href={`${BASE}/client-profile`} download>Hent profil til iPhone, iPad og Mac</a>
      </div>
      <p className="muted tiny" style={{ margin: 0 }}>Profilen sætter mail, kalender og kontakter op på én gang og indeholder ingen adgangskode.</p>
    </Section>
  );
}

function AppPasswords({ disabled }: { disabled: boolean }) {
  const list = useLoad(() => api.get<AppPassword[]>(`${BASE}/app-passwords`));
  const act = useAction(list.reload);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<{ name: string; password: string; username: string } | null>(null);
  return (
    <Section id="app-passwords" title="App-adgangskoder" lead="Én pr. enhed eller program. Tilbagekalder du en, logges kun den enhed ud.">
      <Alert>{act.error || list.error}</Alert>
      {created && (
        <div className="alert success stack" style={css({ "--gap": "8px" })} role="status">
          <strong>App-adgangskode til {created.name}</strong>
          <div className="row" style={css({ "--gap": "8px" })}>
            <code className="mono" style={{ fontSize: 18, letterSpacing: "0.05em" }}>{created.password}</code>
            <Copy value={created.password} />
          </div>
          <span className="small">Brugernavn: <span className="mono">{created.username}</span>. Den vises kun nu; skriv den ind i programmet med det samme.</span>
          <div><button className="btn btn-ghost btn-sm" onClick={() => setCreated(null)}>Skjul</button></div>
        </div>
      )}
      {(list.data ?? []).map((p) => (
        <div key={p.id} className="list-row">
          <span className="stack" style={css({ flex: "1 1 160px", "--gap": "0" })}>
            <strong className="small">{p.name}</strong>
            <span className="tiny muted">Oprettet {fmtDate(p.created_at)} · brugt {p.last_used_at ? fmtDate(p.last_used_at) : "—"}</span>
          </span>
          <button className="btn btn-danger btn-sm" disabled={act.busy} onClick={() => {
            if (confirm(`Tilbagekald „${p.name}“? Programmet, der bruger den, bliver logget ud.`)) act.run(() => api.del(`${BASE}/app-passwords/${p.id}`));
          }}>Tilbagekald</button>
        </div>
      ))}
      {list.data?.length === 0 && <p className="muted small" style={{ margin: 0 }}>Du har ingen app-adgangskoder.</p>}
      <form className="row" style={{ alignItems: "flex-end" }} onSubmit={(e) => {
        e.preventDefault();
        act.run(async () => setCreated(await api.post(`${BASE}/app-passwords`, { name })), () => setName(""));
      }}>
        <label className="field" style={{ flex: "1 1 200px" }}>Navn<input required maxLength={60} placeholder="Thunderbird på arbejds-pc" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <button className="btn btn-primary btn-sm" disabled={disabled || act.busy}>Opret</button>
      </form>
    </Section>
  );
}

// ── Seneste aktivitet og arkiv ──────────────────────────────────────────────

function RecentActivity({ identities, lastRun }: { identities: Identity[]; lastRun: Run | null }) {
  const [identity, setIdentity] = useState("");
  const data = useLoad(() => api.get<{ items: Activity[] }>(`${BASE}/activity${identity ? `?identity=${encodeURIComponent(identity)}` : ""}`), [identity]);
  return (
    <Section id="activity" title="Seneste aktivitet" aside={identities.length > 1 && (
      <select aria-label="Afsender" value={identity} onChange={(e) => setIdentity(e.target.value)} style={{ padding: "5px 8px", fontSize: 13 }}>
        <option value="">Alle afsendere</option>
        {identities.map((i) => <option key={i.id} value={i.from_address}>{i.from_address}</option>)}
      </select>
    )} lead="De sidste 20 sendte mails og invitationer med status fra Lettermint. Vi gemmer ikke indhold eller modtagerens adresse.">
      <Alert>{data.error}</Alert>
      {data.data?.items.length === 0 && <p className="muted small" style={{ margin: 0 }}>Ingen sendte mails endnu.</p>}
      {(data.data?.items ?? []).map((a, i) => {
        const [label, tone] = STATUS[a.status] ?? [a.status, ""];
        return (
          <div key={a.message_id ?? i} className="list-row" style={{ flexWrap: "nowrap" }}>
            <span className="stack" style={css({ flex: 1, minWidth: 0, "--gap": "0" })}>
              <span className="small">Til <strong>{a.recipient_domain ?? "ukendt domæne"}</strong> fra <span className="mono">{a.from_address}</span></span>
              <span className="tiny muted">{fmtDate(a.sent_at)}</span>
            </span>
            <span className={`pill ${tone}`}>{label}</span>
          </div>
        );
      })}
      <p className="muted tiny" style={{ margin: 0 }}>
        Sidste oprydning: {lastRun ? `${fmtDate(lastRun.finished_at ?? lastRun.started_at)} (${lastRun.expired} mails slettet i alt)` : "har ikke kørt endnu"}.
      </p>
    </Section>
  );
}

function ArchiveExport() {
  const act = useAction(() => undefined);
  const [queued, setQueued] = useState(false);
  return (
    <Section id="archive" title="Arkiveksport" lead="Hent dine mails, før de bliver slettet. Du får en zip med .eml-filer i mappen Mail-arkiv under Filer.">
      <Alert>{act.error}</Alert>
      {queued ? <Alert kind="success">Eksporten er i gang. Den ligger under Filer → Mail-arkiv om lidt.</Alert> : (
        <div><button className="btn btn-outline btn-sm" disabled={act.busy} onClick={() => act.run(() => api.post(`${BASE}/archive-export`), () => setQueued(true))}>Bestil eksport</button></div>
      )}
    </Section>
  );
}

// ── Kalendere og adressebøger ───────────────────────────────────────────────

function Collections({ type, disabled }: { type: "calendar" | "address_book"; disabled: boolean }) {
  const path = type === "calendar" ? "calendars" : "address-books";
  const list = useLoad(() => api.get<Collection[]>(`${BASE}/${path}`), [type]);
  const act = useAction(list.reload);
  const [name, setName] = useState("");
  const [colour, setColour] = useState(COLOURS[0]);
  const cal = type === "calendar";
  return (
    <Section id={path} title={cal ? "Kalendere" : "Adressebøger"}
      lead={cal ? "Dine kalendere og dem, andre har delt med dig. Del en med en kollega, så de kan se eller redigere den."
        : "Dine adressebøger og dem, andre har delt med dig. Importér og eksportér som vCard (.vcf)."}>
      <Alert>{act.error || list.error}</Alert>
      {(list.data ?? []).map((c) => <CollectionRow key={c.id} c={c} path={path} reload={list.reload} />)}
      <form className="row" style={{ alignItems: "flex-end" }} onSubmit={(e) => {
        e.preventDefault();
        act.run(() => api.post(`${BASE}/${path}`, cal ? { name, colour } : { name }), () => setName(""));
      }}>
        <label className="field" style={{ flex: "1 1 220px" }}>{cal ? "Ny kalender" : "Ny adressebog"}
          <input required maxLength={120} value={name} placeholder={cal ? "Projekt" : "Leverandører"} onChange={(e) => setName(e.target.value)} /></label>
        {cal && <ColourPicker value={colour} onChange={setColour} />}
        <button className="btn btn-primary btn-sm" disabled={disabled || act.busy}>Opret</button>
      </form>
      {cal && <WorkingHours />}
    </Section>
  );
}

function ColourPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="row" role="radiogroup" aria-label="Farve" style={css({ "--gap": "4px" })}>
      {COLOURS.map((c) => (
        <button key={c} type="button" role="radio" aria-checked={value === c} aria-label={c} onClick={() => onChange(c)}
          style={{ width: 22, height: 22, borderRadius: 9999, background: c, border: value === c ? "3px solid #0f172a" : "2px solid #fff", boxShadow: "0 0 0 1px #cbd5e1", cursor: "pointer" }} />
      ))}
    </div>
  );
}

function CollectionRow({ c, path, reload }: { c: Collection; path: string; reload: () => void }) {
  const { user } = useAuth();
  const act = useAction(reload);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(c.name);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Exclude<CollRole, "owner">>("viewer");
  const canManage = c.role === "owner" || c.role === "manager";
  const cal = c.type === "calendar";
  const importFile = (file: File) => {
    const form = new FormData();
    form.append("file", file);
    act.run(() => api.post(`${BASE}/address-books/${c.id}/import`, form));
  };
  return (
    <div className="list-row" style={{ alignItems: "flex-start" }}>
      <span aria-hidden style={{ width: 12, height: 12, borderRadius: 9999, marginTop: 5, background: c.colour ?? "#cbd5e1", flex: "none" }} />
      <div className="stack" style={css({ flex: "1 1 260px", minWidth: 0, "--gap": "6px" })}>
        {editing ? (
          <form className="row" onSubmit={(e) => { e.preventDefault(); act.run(() => api.patch(`${BASE}/${path}/${c.id}`, { name }), () => setEditing(false)); }}>
            <input aria-label="Navn" value={name} required maxLength={120} onChange={(e) => setName(e.target.value)} />
            <button className="btn btn-outline btn-sm" disabled={act.busy}>Gem</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>Annullér</button>
          </form>
        ) : (
          <span className="row" style={css({ "--gap": "8px" })}>
            <strong className="small">{c.name}</strong>
            {c.is_default && <span className="pill">Standard</span>}
            {c.kind === "shared" && <span className="pill blue">Fælles</span>}
            {c.role !== "owner" && <span className="pill">{c.role_label}</span>}
          </span>
        )}
        <span className="tiny muted">
          {cal ? (c.timezone ?? "") : `${c.entry_count ?? 0} kontakter`}
          {cal && !c.freebusy ? " · tæller ikke med i ledig/optaget" : ""}
          {c.shares.length > 0 && ` · delt med ${c.shares.map((s) => `${s.display_name || s.email} (${COLL_ROLES.find(([k]) => k === s.role)?.[1].toLowerCase()})`).join(", ")}`}
        </span>
        {canManage && c.shares.length > 0 && (
          <div className="row" style={css({ "--gap": "6px" })}>
            {c.shares.map((s) => (
              <button key={s.user_id} className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(() => api.del(`/api/mail/collections/${c.id}/shares/${s.user_id}`))}>
                Fjern {s.display_name || s.email}
              </button>
            ))}
          </div>
        )}
        {canManage && (
          <form className="row" onSubmit={(e) => { e.preventDefault(); act.run(() => api.post(`/api/mail/collections/${c.id}/shares`, { email, role }), () => setEmail("")); }}>
            <input type="email" aria-label="Del med (e-mail)" placeholder="Del med navn@firma.dk" required value={email} onChange={(e) => setEmail(e.target.value)} style={{ flex: "1 1 200px", padding: "5px 10px", fontSize: 14 }} />
            <select aria-label="Rolle" value={role} onChange={(e) => setRole(e.target.value as typeof role)} style={{ padding: "5px 8px", fontSize: 14 }}>
              {COLL_ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <button className="btn btn-outline btn-sm" disabled={act.busy}>Del</button>
          </form>
        )}
        <Alert>{act.error}</Alert>
      </div>
      <div className="row" style={css({ "--gap": "6px" })}>
        <a className="btn btn-ghost btn-sm" href={`/api/mail/collections/${c.id}/export`} download>Eksportér</a>
        {!cal && (c.role === "owner" || c.role === "editor" || c.role === "manager") && (
          <label className="btn btn-ghost btn-sm" style={{ cursor: "pointer" }}>Importér
            <input type="file" accept=".vcf,text/vcard" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ""; }} />
          </label>
        )}
        {canManage && cal && (
          <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(() => api.patch(`${BASE}/${path}/${c.id}`, { freebusy: !c.freebusy }))}>
            {c.freebusy ? "Skjul i ledig/optaget" : "Vis i ledig/optaget"}
          </button>
        )}
        {canManage && !editing && <button className="btn btn-ghost btn-sm" onClick={() => { setName(c.name); setEditing(true); }}>Omdøb</button>}
        {c.role === "owner" && !c.is_default && (
          <button className="btn btn-danger btn-sm" disabled={act.busy} onClick={() => {
            if (confirm(`Slet „${c.name}“ med alt indhold? Det kan ikke fortrydes.`)) act.run(() => api.del(`${BASE}/${path}/${c.id}`));
          }}>Slet</button>
        )}
        {c.role !== "owner" && user && (
          <button className="btn btn-ghost btn-sm" disabled={act.busy} onClick={() => act.run(() => api.del(`/api/mail/collections/${c.id}/shares/${user.id}`))}>Forlad</button>
        )}
      </div>
    </div>
  );
}

function WorkingHours() {
  const data = useLoad(() => api.get<Availability>(`${BASE}/availability`));
  const [draft, setDraft] = useState<Availability | null>(null);
  const act = useAction(data.reload);
  const v = draft ?? data.data;
  if (!v) return null;
  const set = (patch: Partial<Availability>) => setDraft({ ...v, ...patch });
  const hours = v.working_hours;
  return (
    <form className="card muted-bg" style={{ gap: 12 }} data-deeplink="mail-working-hours"
      onSubmit={(e) => { e.preventDefault(); act.run(() => api.put(`${BASE}/availability`, v), () => setDraft(null)); }}>
      <strong className="small">Arbejdstid og ledig/optaget</strong>
      <label className="row small" style={css({ "--gap": "8px" })}>
        <input type="checkbox" checked={v.publish} onChange={(e) => set({ publish: e.target.checked })} />
        Kolleger kan se, hvornår jeg er ledig eller optaget (ikke hvad aftalerne handler om)
      </label>
      <div className="row" role="group" aria-label="Arbejdsdage" style={css({ "--gap": "6px" })}>
        {DAYS.map((label, i) => {
          const day = i + 1;
          const on = hours.days.includes(day);
          return (
            <button key={day} type="button" aria-pressed={on} className={`btn btn-sm ${on ? "btn-dark" : "btn-outline"}`}
              onClick={() => set({ working_hours: { ...hours, days: on ? hours.days.filter((d) => d !== day) : [...hours.days, day].sort() } })}>{label}</button>
          );
        })}
      </div>
      <div className="row">
        <label className="field">Fra<input type="time" value={hours.start} onChange={(e) => set({ working_hours: { ...hours, start: e.target.value } })} /></label>
        <label className="field">Til<input type="time" value={hours.end} onChange={(e) => set({ working_hours: { ...hours, end: e.target.value } })} /></label>
      </div>
      <Alert>{act.error}</Alert>
      <div><button className="btn btn-outline btn-sm" disabled={!draft || act.busy}>Gem arbejdstid</button></div>
    </form>
  );
}
