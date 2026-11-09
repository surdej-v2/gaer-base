// /admin/mail/users/:id → one user's mail: the same panels the user sees on /user/mail plus provisioning, admin actions,
// offboarding and the audit trail. Never message content, events or contacts. API: /api/admin/mail/users/<id>.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Mail } from "lucide-react";
import { api } from "../../../api";
import { input, label, select } from "../../theme";
import { Btn, Empty, ErrorBox, fmtBytes, fmtDate, PageTitle, Pill, useLoad } from "../../ui";
import { askReason, Card, Label, mono, muted, PersonPicker, SHARED_ROLES, STATUS_LABEL, Table, td, useRun, type Person } from "./shared";

type Detail = {
  user: { id: number; email: string | null; display_name: string | null; role: string; blocked_at: string | null };
  mailbox: { id: number; address: string; status: string; quota_mb: number; used_bytes: number | null; requested_at: string; provisioned_at: string | null; last_login_at: string | null; domain: string } | null;
  aliases: { id: number; address: string; created_at: string }[];
  alias_requests: { id: number; address: string; status: string; created_at: string }[];
  retention: { days: number; applies_to: string; scope: string } | null;
  shared: { id: number; address: string; display_name: string | null; role: string; role_label: string }[];
  identities: { id: number; from_address: string; display_name: string }[];
  provisioning: { stalwart_principal: string; status: string; requested_at: string; provisioned_at: string | null; synced_at: string | null; drift: string | null; provision_error: string | null } | null;
  app_passwords: { id: number; name: string; created_at: string; last_used_at: string | null }[];
  collections: { id: string; type: string; kind: string; name: string; colour: string | null; role: string; role_label: string; entry_count: number | null; is_default: boolean; shares: { user_id: number; display_name: string | null; email: string; role: string }[] }[];
  activity: { message_id: string | null; from_address: string; recipient_domain: string | null; status: string; sent_at: string }[];
  audit: { ts: string; action: string; before: unknown; after: unknown; detail: unknown; actor_name: string | null; actor_email: string | null }[];
};
type Domain = { id: number; name: string; status: string };
type SharedMb = { id: number; address: string; display_name: string | null };

export default function MailUser() {
  const { id } = useParams();
  const data = useLoad(() => api.get<Detail>(`/api/admin/mail/users/${id}`), [id]);
  const act = useRun(data.reload);
  const d = data.data;
  const back = <Link to="/mail?tab=mailboxes" style={{ display: "inline-flex", gap: 6, alignItems: "center", fontSize: 13 }}><ArrowLeft size={14} />Alle postkasser</Link>;
  if (data.error) return <>{back}<ErrorBox>{data.error}</ErrorBox></>;
  if (!d) return back;
  const mb = d.mailbox;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {back}
      <PageTitle title={d.user.display_name || d.user.email || `Bruger ${d.user.id}`} icon={<Mail size={24} />}>
        {d.user.email} · {mb ? mb.address : "ingen postkasse"}. Du ser og ændrer postkassen og samlingerne, aldrig mail, aftaler eller kontakter.
      </PageTitle>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      {!mb ? <CreateMailbox userId={d.user.id} onDone={data.reload} /> : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 16, alignItems: "start" }}>
            <Card title={<span style={{ display: "flex", gap: 10, alignItems: "center" }}><span style={mono}>{mb.address}</span><Pill tone={(STATUS_LABEL[mb.status] ?? ["", "gray"])[1]}>{(STATUS_LABEL[mb.status] ?? [mb.status])[0]}</Pill></span>} deeplink="mail-user-mailbox">
              <p style={muted}>{fmtBytes(mb.used_bytes ?? 0)} af {fmtBytes(mb.quota_mb * 1048576)} · sidst logget ind {mb.last_login_at ? fmtDate(mb.last_login_at) : "—"} · mail slettes efter {d.retention?.days ?? 30} dage ({d.retention?.scope === "global" ? "global regel" : d.retention?.scope === "domain" ? "domænets regel" : "postkassens regel"})</p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Btn height={32} onClick={() => { const v = window.prompt("Kvote i MB", String(mb.quota_mb)); if (v) act.run(() => api.patch(`/api/admin/mail/users/${d.user.id}/mailbox`, { quota_mb: Number(v) }), "Kvoten er ændret"); }}>Ændr kvote</Btn>
                {mb.status === "suspended"
                  ? <Btn height={32} onClick={() => act.run(() => api.patch(`/api/admin/mail/users/${d.user.id}/mailbox`, { status: "active" }), "Genaktiveret")}>Genaktivér</Btn>
                  : <Btn height={32} disabled={mb.status !== "active"} onClick={() => { const reason = askReason("Suspendér postkassen? Login og ny mail stopper; mailen bliver liggende, til den udløber."); if (reason) act.run(() => api.patch(`/api/admin/mail/users/${d.user.id}/mailbox`, { status: "suspended", reason }), "Suspenderet"); }}>Suspendér</Btn>}
              </div>
              <Label>Aliasser</Label>
              {d.aliases.map((a) => (
                <div key={a.id} style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}>
                  <span style={{ ...mono, flex: 1 }}>{a.address}</span>
                  <Btn kind="ghost" height={28} onClick={() => act.run(() => api.del(`/api/admin/mail/users/${d.user.id}/aliases/${a.id}`), "Alias fjernet")}>Fjern</Btn>
                </div>
              ))}
              {d.aliases.length === 0 && <p style={muted}>Ingen aliasser.</p>}
              <div><Btn height={32} onClick={() => { const a = window.prompt("Nyt alias (fuld adresse)", `@${mb.domain}`); if (a) act.run(() => api.post(`/api/admin/mail/users/${d.user.id}/aliases`, { address: a }), "Alias tilføjet"); }}>Tilføj alias</Btn></div>
            </Card>

            <Card title="Opsætning i Stalwart" deeplink="mail-user-provisioning">
              {d.provisioning && (
                <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: "6px 12px", fontSize: 13 }}>
                  <span style={muted}>Principal</span><span style={mono}>{d.provisioning.stalwart_principal}</span>
                  <span style={muted}>Bestilt</span><span>{fmtDate(d.provisioning.requested_at)}</span>
                  <span style={muted}>Oprettet</span><span>{d.provisioning.provisioned_at ? fmtDate(d.provisioning.provisioned_at) : "—"}</span>
                  <span style={muted}>Sidst synkroniseret</span><span>{d.provisioning.synced_at ? fmtDate(d.provisioning.synced_at) : "—"}</span>
                </div>
              )}
              {d.provisioning?.drift && <ErrorBox>Afvigelse: {d.provisioning.drift}</ErrorBox>}
              {d.provisioning?.provision_error && <ErrorBox>Kan ikke sættes op: {d.provisioning.provision_error}</ErrorBox>}
              <Label>App-adgangskoder ({d.app_passwords.length})</Label>
              {d.app_passwords.map((p) => <p key={p.id} style={muted}>{p.name} · oprettet {fmtDate(p.created_at)}</p>)}
              <div><Btn height={32} disabled={!d.app_passwords.length} onClick={() => { const reason = askReason("Tilbagekald alle app-adgangskoder? Alle mail-, kalender- og kontaktklienter bliver logget ud."); if (reason) act.run(() => api.post(`/api/admin/mail/users/${d.user.id}/app-passwords/revoke-all`, { reason }), "Alle klienter er logget ud"); }}>Tilbagekald alle (log klienter ud)</Btn></div>
            </Card>
          </div>

          <SharedMemberships d={d} onChange={data.reload} />

          <Card title="Kalendere og adressebøger">
            <Table head={["Navn", "Type", "Rolle", "Delt med"]}>
              {d.collections.map((c) => (
                <tr key={c.id}>
                  <td style={td}><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 9999, background: c.colour ?? "#cbd5e1", marginRight: 8 }} />{c.name}{c.is_default ? " (standard)" : ""}</td>
                  <td style={td}>{c.type === "calendar" ? "Kalender" : `Adressebog · ${c.entry_count ?? 0} kontakter`}</td>
                  <td style={td}>{c.role_label}{c.kind === "shared" ? " · fælles" : ""}</td>
                  <td style={td}>{c.shares.map((s) => `${s.display_name || s.email} (${s.role})`).join(", ") || "—"}</td>
                </tr>
              ))}
            </Table>
          </Card>

          <Offboarding d={d} onDone={data.reload} />

          <Card title="Seneste afsendelser">
            {d.activity.length === 0 ? <Empty>Ingen hændelser fra Lettermint.</Empty> : (
              <Table head={["Sendt", "Fra", "Til domæne", "Status"]}>
                {d.activity.map((a, i) => <tr key={a.message_id ?? i}><td style={td}>{fmtDate(a.sent_at)}</td><td style={{ ...td, ...mono }}>{a.from_address}</td><td style={td}>{a.recipient_domain ?? "—"}</td><td style={td}>{a.status}</td></tr>)}
              </Table>
            )}
          </Card>
        </>
      )}
      <Card title="Audit-spor" deeplink="mail-user-audit">
        {d.audit.length === 0 ? <Empty>Ingen ændringer endnu.</Empty> : (
          <Table head={["Tidspunkt", "Handling", "Hvem", "Hvad"]}>
            {d.audit.map((a, i) => (
              <tr key={i}>
                <td style={{ ...td, whiteSpace: "nowrap" }}>{fmtDate(a.ts)}</td>
                <td style={{ ...td, ...mono }}>{a.action}</td>
                <td style={td}>{a.actor_name || a.actor_email || "System"}</td>
                <td style={{ ...td, ...mono, fontSize: 11.5, wordBreak: "break-word" }}>{[a.before ? `før ${JSON.stringify(a.before)}` : "", a.after ? `efter ${JSON.stringify(a.after)}` : ""].filter(Boolean).join(" · ")}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function CreateMailbox({ userId, onDone }: { userId: number; onDone: () => void }) {
  const domains = useLoad(() => api.get<Domain[]>("/api/admin/mail/domains"), []);
  const act = useRun(onDone);
  const [local, setLocal] = useState("");
  const [domain, setDomain] = useState(0);
  const usable = (domains.data ?? []).filter((x) => x.status !== "suspended");
  const domainId = domain || usable[0]?.id;
  return (
    <Card title="Opret postkasse">
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      {usable.length === 0 ? <p style={muted}>Tilføj først et domæne under <Link to="/mail?tab=domains">Domæner</Link>.</p> : (
        <form style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => {
          e.preventDefault();
          act.run(() => api.post(`/api/admin/mail/users/${userId}/mailbox`, { domain_id: domainId, ...(local ? { local_part: local } : {}) }), "Postkassen er oprettet");
        }}>
          <label style={{ ...label, flex: "1 1 200px" }}>Adresse (tom: fornavn.efternavn)<input value={local} placeholder="fornavn.efternavn" onChange={(e) => setLocal(e.target.value)} style={input} /></label>
          <label style={label}>Domæne<select value={domainId ?? ""} onChange={(e) => setDomain(Number(e.target.value))} style={select}>{usable.map((x) => <option key={x.id} value={x.id}>@{x.name}</option>)}</select></label>
          <Btn kind="primary" type="submit" disabled={act.busy}>Opret</Btn>
        </form>
      )}
    </Card>
  );
}

function SharedMemberships({ d, onChange }: { d: Detail; onChange: () => void }) {
  const shared = useLoad(() => api.get<SharedMb[]>("/api/admin/mail/shared"), []);
  const act = useRun(onChange);
  const [pick, setPick] = useState({ mailbox_id: 0, role: "reader" });
  const options = (shared.data ?? []).filter((s) => !d.shared.some((m) => m.id === s.id));
  const target = pick.mailbox_id || options[0]?.id;
  return (
    <Card title="Fælles postkasser">
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      {d.shared.map((s) => (
        <div key={s.id} style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}>
          <span style={{ flex: 1 }}>{s.display_name || s.address} <span style={{ ...mono, color: "#64748b" }}>{s.address}</span></span>
          <select aria-label={`Rolle i ${s.address}`} value={s.role} style={{ ...select, height: 30 }} onChange={(e) => act.run(() => api.put(`/api/admin/mail/shared/${s.id}/members/${d.user.id}`, { role: e.target.value }), "Rolle ændret")}>
            {SHARED_ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <Btn kind="ghost" height={30} onClick={() => act.run(() => api.del(`/api/admin/mail/shared/${s.id}/members/${d.user.id}`), "Fjernet")}>Fjern</Btn>
        </div>
      ))}
      {d.shared.length === 0 && <p style={muted}>Ikke medlem af nogen fælles postkasser.</p>}
      {options.length > 0 && (
        <form style={{ display: "flex", gap: 8, flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); act.run(() => api.post(`/api/admin/mail/users/${d.user.id}/shared-mailboxes`, { mailbox_id: target, role: pick.role }), "Tilføjet"); }}>
          <select aria-label="Fælles postkasse" value={target} onChange={(e) => setPick({ ...pick, mailbox_id: Number(e.target.value) })} style={select}>
            {options.map((s) => <option key={s.id} value={s.id}>{s.address}</option>)}
          </select>
          <select aria-label="Rolle" value={pick.role} onChange={(e) => setPick({ ...pick, role: e.target.value })} style={select}>
            {SHARED_ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <Btn type="submit" disabled={act.busy}>Tilføj</Btn>
        </form>
      )}
    </Card>
  );
}

function Offboarding({ d, onDone }: { d: Detail; onDone: () => void }) {
  const shared = useLoad(() => api.get<SharedMb[]>("/api/admin/mail/shared"), []);
  const act = useRun(onDone);
  const [action, setAction] = useState<"suspend" | "convert" | "delete">("suspend");
  const [collections, setCollections] = useState<"transfer" | "delete">("transfer");
  const [target, setTarget] = useState(0);
  const [managers, setManagers] = useState<Person[]>([]);
  const text = {
    suspend: "Stopper login og ny mail med det samme. Mailen bliver liggende, til opbevaringsreglen sletter den.",
    convert: "Adressen (og aliasserne) bliver aliasser på en fælles postkasse, så mail til personen lander hos teamet. Den personlige postkasse slettes.",
    delete: "Postkassen og al mail i den slettes i Stalwart.",
  }[action];
  return (
    <Card title="Fratrædelse" deeplink="mail-user-offboarding">
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(["suspend", "convert", "delete"] as const).map((a) => (
          <Btn key={a} kind={action === a ? "primary" : "outline"} height={32} onClick={() => setAction(a)}>
            {{ suspend: "Suspendér nu", convert: "Lav om til alias", delete: "Slet postkasse" }[a]}
          </Btn>
        ))}
      </div>
      <p style={muted}>{text}</p>
      {action === "convert" && (
        <label style={label}>Fælles postkasse<select value={target || shared.data?.[0]?.id || ""} onChange={(e) => setTarget(Number(e.target.value))} style={select}>
          {(shared.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.address}</option>)}</select></label>
      )}
      {action !== "suspend" && (
        <>
          <Label>Kalendere og adressebøger</Label>
          <label style={{ display: "flex", gap: 8, fontSize: 14 }}><input type="radio" checked={collections === "transfer"} onChange={() => setCollections("transfer")} />Overfør til fælles kalendere og adressebøger</label>
          <label style={{ display: "flex", gap: 8, fontSize: 14 }}><input type="radio" checked={collections === "delete"} onChange={() => setCollections("delete")} />Slet dem sammen med postkassen</label>
          {collections === "transfer" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span style={muted}>Ansvarlige for de overførte samlinger: {managers.map((m) => m.display_name || m.email).join(", ") || "ingen valgt"}</span>
              <PersonPicker needMailbox placeholder="Tilføj ansvarlig" onPick={(p) => setManagers((m) => m.some((x) => x.id === p.id) ? m : [...m, p])} />
            </div>
          )}
        </>
      )}
      <div>
        <Btn kind="primary" disabled={act.busy || (action === "convert" && !(target || shared.data?.[0]?.id))} onClick={() => {
          const reason = askReason(action === "suspend" ? "Suspendér postkassen?" : action === "convert" ? "Lav postkassen om til et alias på den fælles postkasse?" : "Slet postkassen og al mail i den?");
          if (!reason) return;
          act.run(() => api.post(`/api/admin/mail/users/${d.user.id}/offboard`, {
            action, reason, collections, managers: managers.map((m) => m.id),
            ...(action === "convert" ? { shared_mailbox_id: target || shared.data?.[0]?.id } : {}),
          }), "Udført");
        }}>Udfør</Btn>
      </div>
    </Card>
  );
}
