// /admin/mail → Mail. The operator console for mail, calendars and contacts (specs/mail): Overblik · Domæner ·
// Postkasser · Fælles postkasser · Kalendere og kontakter · Afsendelse · Opbevaring · Drift · Audit. ?tab=… is the deep
// link. API: /api/admin/mail (server/app/routes/admin_mail.py). Admins manage mailboxes and collections, never content.
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Download, Mail, RefreshCw } from "lucide-react";
import { api, query } from "../../../api";
import { fmt, input, label, select } from "../../theme";
import { Btn, Empty, ErrorBox, fmtBytes, fmtDate, PageTitle, Pill, useLoad } from "../../ui";
import { askReason, Card, COLL_ROLES, Label, mono, muted, PersonPicker, SHARED_ROLES, STATUS_LABEL, Table, td, useRun } from "./shared";

type Tab = "overview" | "domains" | "mailboxes" | "shared" | "collections" | "sending" | "retention" | "health" | "audit";
const TABS: [Tab, string][] = [
  ["overview", "Overblik"], ["domains", "Domæner"], ["mailboxes", "Postkasser"], ["shared", "Fælles postkasser"],
  ["collections", "Kalendere og kontakter"], ["sending", "Afsendelse"], ["retention", "Opbevaring"], ["health", "Drift"], ["audit", "Audit"],
];

type Warning = { key: string; tab: Tab; text: string };
type Overview = {
  enabled: boolean; backend: string;
  counts?: { personal: number; shared: number; suspended: number; provisioning: number; used_bytes: number; quota_bytes: number };
  per_day?: { day: string; sent: number; bounced: number; expired: number }[];
  lettermint_month?: { messages: number; quota: number | null };
  collections?: { calendars: number; address_books: number };
  last_retention?: { finished_at: string | null; started_at: string; expired: number; status: string } | null;
  warnings?: Warning[];
};
type Domain = {
  id: number; name: string; status: string; dkim_selector: string; lettermint_domain_id: string | null; checked_at: string | null;
  mailboxes: number | null; checks: Record<string, { ok: boolean; found: string[]; error?: string }>;
  records: { key: string; label: string; type: string; name: string; value: string; why: string }[];
};

export default function MailAdmin() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some(([k]) => k === params.get("tab")) ? params.get("tab") : "overview") as Tab;
  const ov = useLoad(() => api.get<Overview>("/api/admin/mail/overview"), []);
  const go = (t: Tab) => setParams(t === "overview" ? {} : { tab: t }, { replace: true });
  const title = (
    <PageTitle title="Mail" icon={<Mail size={24} />}>
      Postkasser, kalendere og kontakter i Stalwart; al udgående mail går gennem Lettermint. Mail slettes efter højst 30 dage,
      kalendere og kontakter aldrig. Du styrer postkasser og samlinger, aldrig indholdet.
    </PageTitle>
  );
  if (ov.error) return <>{title}<ErrorBox>{ov.error}</ErrorBox></>;
  if (!ov.data) return title;
  if (!ov.data.enabled) return <>{title}<NotConfigured /></>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {title}
      <div role="tablist" aria-label="Mail" style={{ display: "flex", gap: 22, borderBottom: "1px solid #e2e8f0", overflowX: "auto" }}>
        {TABS.map(([key, text]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} data-deeplink={`mail-tab-${key}`} onClick={() => go(key)}
            style={{ all: "unset", cursor: "pointer", padding: "10px 0", fontSize: 14, whiteSpace: "nowrap", fontWeight: tab === key ? 600 : 500,
              color: tab === key ? "#020817" : "#64748b", borderBottom: tab === key ? "2px solid #2563eb" : "2px solid transparent", marginBottom: -1 }}>
            {text}{key === "overview" && (ov.data?.warnings?.length ?? 0) > 0 && <span style={{ marginLeft: 6 }}><Pill tone="orange" size={10}>{ov.data!.warnings!.length}</Pill></span>}
          </button>
        ))}
      </div>
      {tab === "overview" && <OverviewTab o={ov.data} go={go} />}
      {tab === "domains" && <Domains />}
      {tab === "mailboxes" && <Mailboxes />}
      {tab === "shared" && <SharedMailboxes />}
      {tab === "collections" && <CollectionsTab />}
      {tab === "sending" && <Sending />}
      {tab === "retention" && <Retention />}
      {tab === "health" && <Health />}
      {tab === "audit" && <Audit />}
    </div>
  );
}

function NotConfigured() {
  return (
    <Card title="Mail er ikke sat op" style={{ maxWidth: 760 }}>
      <p style={muted}>Modulet er slået fra, indtil appen kender mailserveren. Sæt i miljøet (se <code>specs/mail/README.md</code>):</p>
      <pre style={{ margin: 0, padding: 12, borderRadius: 8, background: "#f8fafc", ...mono }}>
        MAILBOX_BACKEND=stalwart{"\n"}STALWART_URL=http://stalwart.internal:8080{"\n"}STALWART_API_KEY=…{"\n"}STALWART_MASTER_USER=gaer-master{"\n"}STALWART_MASTER_SECRET=…{"\n"}MAIL_HOSTNAME=mail.firma.dk
      </pre>
      <p style={muted}>Lokalt kan du prøve siderne med <code>MAILBOX_BACKEND=memory</code>.</p>
    </Card>
  );
}

function Stat({ label: l, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card style={{ gap: 4 }}>
      <Label>{l}</Label>
      <span style={{ fontSize: 28, fontWeight: 700, letterSpacing: "-0.02em" }}>{value}</span>
      {sub && <span style={muted}>{sub}</span>}
    </Card>
  );
}

// ── Overblik ────────────────────────────────────────────────────────────────

function OverviewTab({ o, go }: { o: Overview; go: (t: Tab) => void }) {
  const c = o.counts!;
  const max = Math.max(1, ...(o.per_day ?? []).map((d) => d.sent));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {(o.warnings ?? []).length > 0 && (
        <Card title="Advarsler" deeplink="mail-warnings">
          {o.warnings!.map((w) => (
            <button key={w.key} type="button" onClick={() => go(w.tab)} className="tg-hover"
              style={{ all: "unset", cursor: "pointer", display: "flex", gap: 10, alignItems: "center", padding: "6px 8px", borderRadius: 8 }}>
              <Pill tone="orange">!</Pill><span style={{ fontSize: 14 }}>{w.text}</span>
            </button>
          ))}
        </Card>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <Stat label="Postkasser" value={fmt(c.personal)} sub={`${c.shared} fælles · ${c.suspended} suspenderet${c.provisioning ? ` · ${c.provisioning} sættes op` : ""}`} />
        <Stat label="Lager brugt" value={fmtBytes(c.used_bytes)} sub={`af ${fmtBytes(c.quota_bytes)} tildelt`} />
        <Stat label="Sendt denne måned" value={fmt(o.lettermint_month!.messages)} sub={o.lettermint_month!.quota ? `af ${fmt(o.lettermint_month!.quota)} i Lettermint-planen` : "via Lettermint"} />
        <Stat label="Kalendere og adressebøger" value={fmt((o.collections?.calendars ?? 0) + (o.collections?.address_books ?? 0))} sub={`${o.collections?.calendars ?? 0} kalendere · ${o.collections?.address_books ?? 0} adressebøger`} />
        <Stat label="Sidste oprydning" value={o.last_retention ? fmtDate(o.last_retention.finished_at ?? o.last_retention.started_at) : "Aldrig"} sub={o.last_retention ? `${fmt(o.last_retention.expired)} mails slettet` : undefined} />
      </div>
      <Card title="Udgående mail pr. dag (14 dage)">
        {(o.per_day ?? []).length === 0 ? <Empty>Ingen hændelser fra Lettermint endnu.</Empty> : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {o.per_day!.map((d) => (
              <div key={d.day} style={{ display: "grid", gridTemplateColumns: "90px 1fr 140px", gap: 10, alignItems: "center", fontSize: 13 }}>
                <span style={{ color: "#64748b" }}>{new Date(d.day).toLocaleDateString("da-DK", { day: "numeric", month: "short" })}</span>
                <div style={{ height: 10, background: "#f1f5f9", borderRadius: 9999, overflow: "hidden" }}>
                  <div style={{ width: `${(d.sent / max) * 100}%`, height: "100%", background: "#2563eb" }} />
                </div>
                <span>{fmt(d.sent)} sendt{d.bounced ? ` · ${d.bounced} afvist` : ""}</span>
              </div>
            ))}
          </div>
        )}
        <p style={muted}>Indgående mail tælles i Stalwart og vises ikke her.</p>
      </Card>
    </div>
  );
}

// ── Domæner ─────────────────────────────────────────────────────────────────

function Domains() {
  const list = useLoad(() => api.get<Domain[]>("/api/admin/mail/domains"), []);
  const act = useRun(list.reload);
  const [name, setName] = useState("");
  const [selector, setSelector] = useState("lm");
  const add = (e: FormEvent) => {
    e.preventDefault();
    act.run(() => api.post("/api/admin/mail/domains", { name, dkim_selector: selector }), "Domænet er tilføjet. Opret DNS-posterne nedenfor.").then((ok) => ok && setName(""));
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      {list.error && <ErrorBox>{list.error}</ErrorBox>}
      {(list.data ?? []).map((d) => {
        const [text, tone] = STATUS_LABEL[d.status] ?? [d.status, "gray"];
        return (
          <Card key={d.id} deeplink={`mail-domain-${d.name}`} title={<span style={{ display: "flex", gap: 10, alignItems: "center" }}>{d.name}<Pill tone={tone}>{text}</Pill></span>}
            aside={<div style={{ display: "flex", gap: 8 }}>
              <Btn icon={RefreshCw} height={32} disabled={act.busy} onClick={() => act.run(() => api.post(`/api/admin/mail/domains/${d.id}/verify`), "Tjekket")}>Tjek igen</Btn>
              {d.status === "suspended"
                ? <Btn height={32} onClick={() => act.run(() => api.patch(`/api/admin/mail/domains/${d.id}`, { status: "pending" }), "Genaktiveret")}>Genaktivér</Btn>
                : <Btn height={32} onClick={() => { const reason = askReason(`Suspendér ${d.name}? Der kan ikke oprettes nye adresser på domænet.`); if (reason) act.run(() => api.patch(`/api/admin/mail/domains/${d.id}`, { status: "suspended", reason }), "Suspenderet"); }}>Suspendér</Btn>}
            </div>}>
            <p style={muted}>{d.checked_at ? `Tjekket ${fmtDate(d.checked_at)}` : "Ikke tjekket endnu"} · DKIM-selector <code>{d.dkim_selector}</code>{d.mailboxes != null ? ` · ${d.mailboxes} postkasser` : ""}</p>
            <Table head={["Post", "Type", "Navn", "Værdi", "Status"]}>
              {d.records.map((r) => {
                const check = d.checks?.[r.key];
                return (
                  <tr key={r.key}>
                    <td style={td} title={r.why}><strong>{r.label}</strong></td>
                    <td style={td}>{r.type}</td>
                    <td style={{ ...td, ...mono, wordBreak: "break-all" }}>{r.name}</td>
                    <td style={{ ...td, ...mono, wordBreak: "break-all" }}>{r.value}{check && !check.ok && check.found?.length ? <div style={{ color: "#b91c1c" }}>Fundet: {check.found.join(", ")}</div> : null}</td>
                    <td style={td}>{!check ? <Pill tone="gray">—</Pill> : check.ok ? <Pill tone="green">OK</Pill> : <Pill tone="red">{check.error ? "Fejl" : "Mangler"}</Pill>}</td>
                  </tr>
                );
              })}
            </Table>
          </Card>
        );
      })}
      {list.data?.length === 0 && <Empty>Ingen domæner endnu. Tilføj det første nedenfor.</Empty>}
      <Card title="Tilføj domæne">
        <form onSubmit={add} style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ ...label, flex: "1 1 240px" }}>Domæne<input required placeholder="firma.dk" value={name} onChange={(e) => setName(e.target.value)} style={input} /></label>
          <label style={label}>DKIM-selector<input required value={selector} onChange={(e) => setSelector(e.target.value)} style={{ ...input, width: 120 }} /></label>
          <Btn kind="primary" type="submit" disabled={act.busy}>Tilføj</Btn>
        </form>
        <p style={muted}>Domænet bliver verificeret, når MX, SPF, DKIM og DMARC er på plads. Tilføj også domænet i Lettermint, og brug den DKIM-selector, Lettermint viser.</p>
      </Card>
    </div>
  );
}

// ── Postkasser ──────────────────────────────────────────────────────────────

type MailboxRow = {
  id: number; address: string; status: string; owner_id: number | null; owner_name: string | null; owner_email: string | null;
  quota_mb: number; used_bytes: number | null; last_login_at: string | null; provision_error: string | null; drift: string | null;
};
type AliasRequest = { id: number; address: string; note: string; created_at: string; user_id: number; display_name: string | null; email: string };

function Mailboxes() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const list = useLoad(() => api.get<{ items: MailboxRow[]; total: number }>(`/api/admin/mail/mailboxes${query({ q, status, offset })}`), [q, status, offset]);
  const requests = useLoad(() => api.get<AliasRequest[]>("/api/admin/mail/alias-requests"), []);
  const act = useRun(() => { list.reload(); requests.reload(); });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      {(requests.data ?? []).length > 0 && (
        <Card title="Anmodninger om alias" deeplink="mail-alias-requests">
          {requests.data!.map((r) => (
            <div key={r.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", borderTop: "1px solid #e2e8f0", paddingTop: 10 }}>
              <span style={{ flex: "1 1 260px" }}><strong style={mono}>{r.address}</strong> til {r.display_name || r.email}{r.note ? ` — „${r.note}“` : ""}<br /><span style={muted}>{fmtDate(r.created_at)}</span></span>
              <Btn kind="primary" height={32} disabled={act.busy} onClick={() => act.run(() => api.post(`/api/admin/mail/alias-requests/${r.id}`, { approve: true }), "Alias oprettet")}>Godkend</Btn>
              <Btn height={32} disabled={act.busy} onClick={() => act.run(() => api.post(`/api/admin/mail/alias-requests/${r.id}`, { approve: false }), "Afvist")}>Afvis</Btn>
            </div>
          ))}
        </Card>
      )}
      <Card title={`Personlige postkasser${list.data ? ` (${list.data.total})` : ""}`} aside={
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input aria-label="Søg" placeholder="Søg adresse eller navn" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} style={{ ...input, width: 220 }} />
          <select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }} style={select}>
            <option value="">Alle</option><option value="active">Aktive</option><option value="provisioning">Sættes op</option><option value="suspended">Suspenderede</option>
          </select>
        </div>}>
        {list.error && <ErrorBox>{list.error}</ErrorBox>}
        <Table head={["Adresse", "Ejer", "Lager", "Status", "Sidst logget ind"]}>
          {(list.data?.items ?? []).map((m) => {
            const [text, tone] = STATUS_LABEL[m.status] ?? [m.status, "gray"];
            return (
              <tr key={m.id}>
                <td style={td}><Link to={`/mail/users/${m.owner_id}`} style={mono}>{m.address}</Link>{m.drift && <div style={{ color: "#9a3412", fontSize: 12 }}>{m.drift}</div>}{m.provision_error && <div style={{ color: "#b91c1c", fontSize: 12 }}>{m.provision_error}</div>}</td>
                <td style={td}>{m.owner_name || m.owner_email}</td>
                <td style={td}>{fmtBytes(m.used_bytes ?? 0)} / {fmtBytes(m.quota_mb * 1048576)}</td>
                <td style={td}><Pill tone={tone}>{text}</Pill></td>
                <td style={td}>{m.last_login_at ? fmtDate(m.last_login_at) : "—"}</td>
              </tr>
            );
          })}
        </Table>
        {list.data?.items.length === 0 && <Empty>Ingen postkasser.</Empty>}
        {list.data && list.data.total > 100 && (
          <div style={{ display: "flex", gap: 8 }}>
            <Btn height={32} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Forrige</Btn>
            <Btn height={32} disabled={offset + 100 >= list.data.total} onClick={() => setOffset(offset + 100)}>Næste</Btn>
          </div>
        )}
      </Card>
      <BulkProvision onDone={list.reload} />
    </div>
  );
}

function BulkProvision({ onDone }: { onDone: () => void }) {
  const domains = useLoad(() => api.get<Domain[]>("/api/admin/mail/domains"), []);
  const [q, setQ] = useState("");
  const users = useLoad(() => api.get<{ id: number; display_name: string | null; email: string | null }[]>(`/api/admin/mail/users-without-mailbox${query({ q })}`), [q]);
  const [picked, setPicked] = useState<number[]>([]);
  const [domain, setDomain] = useState<number | null>(null);
  const act = useRun(() => { users.reload(); onDone(); setPicked([]); });
  const usable = (domains.data ?? []).filter((d) => d.status !== "suspended");
  const domainId = domain ?? usable[0]?.id;
  return (
    <Card title="Opret postkasser fra brugerlisten" deeplink="mail-bulk">
      <p style={muted}>Hver bruger får fornavn.efternavn@domæne (eller delen før @ i login-e-mailen) og en standardkalender og -adressebog.</p>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <input aria-label="Søg brugere" placeholder="Søg brugere uden postkasse" value={q} onChange={(e) => setQ(e.target.value)} style={{ ...input, flex: "1 1 220px" }} />
        <select aria-label="Domæne" value={domainId ?? ""} onChange={(e) => setDomain(Number(e.target.value))} style={select}>
          {usable.map((d) => <option key={d.id} value={d.id}>@{d.name}</option>)}
        </select>
        <Btn kind="primary" disabled={!picked.length || !domainId || act.busy}
          onClick={() => act.run(() => api.post("/api/admin/mail/mailboxes/bulk", { user_ids: picked, domain_id: domainId }), `${picked.length} postkasse(r) bestilt`)}>
          Opret {picked.length || ""} postkasse{picked.length === 1 ? "" : "r"}
        </Btn>
      </div>
      <div style={{ maxHeight: 280, overflowY: "auto", display: "flex", flexDirection: "column" }}>
        {(users.data ?? []).map((u) => (
          <label key={u.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "6px 4px", fontSize: 13, borderTop: "1px solid #f1f5f9" }}>
            <input type="checkbox" checked={picked.includes(u.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, u.id] : picked.filter((x) => x !== u.id))} />
            <span style={{ flex: 1 }}>{u.display_name || "Uden navn"}</span><span style={{ color: "#64748b" }}>{u.email}</span>
          </label>
        ))}
        {users.data?.length === 0 && <Empty>Alle brugere har en postkasse.</Empty>}
      </div>
      {(users.data ?? []).length > 0 && <div><Btn height={30} onClick={() => setPicked((users.data ?? []).map((u) => u.id))}>Vælg alle viste</Btn></div>}
    </Card>
  );
}

// ── Fælles postkasser ───────────────────────────────────────────────────────

type Member = { user_id: number; display_name: string | null; email: string; role: string; has_mailbox: boolean };
type SharedMb = { id: number; address: string; display_name: string | null; status: string; quota_mb: number; used_bytes: number | null; members: Member[]; aliases: { id: number; address: string }[]; provision_error: string | null };

function SharedMailboxes() {
  const list = useLoad(() => api.get<SharedMb[]>("/api/admin/mail/shared"), []);
  const domains = useLoad(() => api.get<Domain[]>("/api/admin/mail/domains"), []);
  const act = useRun(list.reload);
  const [form, setForm] = useState({ local_part: "", display_name: "", domain_id: 0 });
  const usable = (domains.data ?? []).filter((d) => d.status !== "suspended");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      {(list.data ?? []).map((s) => (
        <Card key={s.id} deeplink={`mail-shared-${s.id}`} title={<span style={{ display: "flex", gap: 10, alignItems: "center" }}>{s.display_name || s.address}<span style={{ ...mono, color: "#64748b" }}>{s.address}</span></span>}
          aside={<div style={{ display: "flex", gap: 8 }}>
            <Btn height={32} onClick={() => { const n = window.prompt("Nyt visningsnavn", s.display_name ?? ""); if (n) act.run(() => api.patch(`/api/admin/mail/shared/${s.id}`, { display_name: n }), "Omdøbt"); }}>Omdøb</Btn>
            <Btn height={32} onClick={() => { const reason = askReason(`Slet ${s.address}? Mailen i postkassen slettes, og medlemmerne mister adgangen.`); if (reason) act.run(() => api.del(`/api/admin/mail/shared/${s.id}?reason=${encodeURIComponent(reason)}`), "Slettet"); }}>Slet</Btn>
          </div>}>
          {s.provision_error && <ErrorBox>{s.provision_error}</ErrorBox>}
          {s.aliases.length > 0 && <p style={muted}>Aliasser: {s.aliases.map((a) => a.address).join(", ")}</p>}
          <Table head={["Medlem", "Rolle", ""]}>
            {s.members.map((m) => (
              <tr key={m.user_id}>
                <td style={td}>{m.display_name || m.email}<div style={{ color: "#64748b", fontSize: 12 }}>{m.email}{m.has_mailbox ? "" : " · ingen postkasse"}</div></td>
                <td style={td}>
                  <select aria-label={`Rolle for ${m.email}`} value={m.role} style={select} onChange={(e) => act.run(() => api.put(`/api/admin/mail/shared/${s.id}/members/${m.user_id}`, { role: e.target.value }), "Rolle ændret")}>
                    {SHARED_ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                  </select>
                </td>
                <td style={{ ...td, textAlign: "right" }}><Btn kind="ghost" height={30} onClick={() => act.run(() => api.del(`/api/admin/mail/shared/${s.id}/members/${m.user_id}`), "Fjernet")}>Fjern</Btn></td>
              </tr>
            ))}
          </Table>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <PersonPicker placeholder="Tilføj medlem som læser" onPick={(p) => act.run(() => api.put(`/api/admin/mail/shared/${s.id}/members/${p.id}`, { role: "reader" }), `${p.display_name || p.email} tilføjet`)} />
            <Btn height={36} onClick={() => { const a = window.prompt("Nyt alias (fuld adresse)"); if (a) act.run(() => api.post(`/api/admin/mail/shared/${s.id}/aliases`, { address: a }), "Alias tilføjet"); }}>Tilføj alias</Btn>
          </div>
        </Card>
      ))}
      {list.data?.length === 0 && <Empty>Ingen fælles postkasser endnu.</Empty>}
      <Card title="Ny fælles postkasse">
        <form style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => {
          e.preventDefault();
          act.run(() => api.post("/api/admin/mail/shared", { ...form, domain_id: form.domain_id || usable[0]?.id }), "Oprettet").then((ok) => ok && setForm({ local_part: "", display_name: "", domain_id: form.domain_id }));
        }}>
          <label style={{ ...label, flex: "1 1 160px" }}>Adresse<input required placeholder="support" value={form.local_part} onChange={(e) => setForm({ ...form, local_part: e.target.value })} style={input} /></label>
          <label style={label}>Domæne<select value={form.domain_id || usable[0]?.id || ""} onChange={(e) => setForm({ ...form, domain_id: Number(e.target.value) })} style={select}>
            {usable.map((d) => <option key={d.id} value={d.id}>@{d.name}</option>)}</select></label>
          <label style={{ ...label, flex: "1 1 200px" }}>Visningsnavn<input required placeholder="Support" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} style={input} /></label>
          <Btn kind="primary" type="submit" disabled={act.busy || !usable.length}>Opret</Btn>
        </form>
        <p style={muted}>Læser: læser og arkiverer. Afsender: sender også som adressen. Ansvarlig: tilføjer og fjerner også medlemmer (fra /user/mail).</p>
      </Card>
    </div>
  );
}

// ── Kalendere og kontakter ──────────────────────────────────────────────────

type Coll = { id: string; type: "calendar" | "address_book"; name: string; colour: string | null; entry_count: number | null;
  shares: { user_id: number; display_name: string | null; email: string; role: string }[] };

function CollectionsTab() {
  const data = useLoad(() => api.get<{ shared: Coll[]; per_user: { user_id: number; display_name: string | null; email: string; calendars: number; address_books: number; contacts: number }[] }>("/api/admin/mail/collections"), []);
  const act = useRun(data.reload);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"calendar" | "address_book">("calendar");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      <Card title="Fælles kalendere og adressebøger" deeplink="mail-shared-collections">
        {(data.data?.shared ?? []).map((c) => (
          <div key={c.id} style={{ borderTop: "1px solid #e2e8f0", paddingTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ width: 10, height: 10, borderRadius: 9999, background: c.colour ?? "#cbd5e1" }} />
              <strong style={{ fontSize: 14 }}>{c.name}</strong>
              <Pill tone="blue">{c.type === "calendar" ? "Kalender" : "Adressebog"}</Pill>
              {c.type === "address_book" && <span style={muted}>{c.entry_count ?? 0} kontakter</span>}
              <span style={{ flex: 1 }} />
              <Btn kind="ghost" height={30} onClick={() => { const n = window.prompt("Nyt navn", c.name); if (n) act.run(() => api.patch(`/api/admin/mail/collections/${c.id}`, { name: n }), "Omdøbt"); }}>Omdøb</Btn>
              <Btn kind="ghost" height={30} onClick={() => { const reason = askReason(`Slet „${c.name}“ med alt indhold?`); if (reason) act.run(() => api.del(`/api/admin/mail/collections/${c.id}?reason=${encodeURIComponent(reason)}`), "Slettet"); }}>Slet</Btn>
            </div>
            {c.shares.map((s) => (
              <div key={s.user_id} style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13, paddingLeft: 20 }}>
                <span style={{ flex: 1 }}>{s.display_name || s.email}</span>
                <select aria-label={`Rolle for ${s.email}`} value={s.role} style={{ ...select, height: 30 }} onChange={(e) => act.run(() => api.put(`/api/admin/mail/collections/${c.id}/shares/${s.user_id}`, { role: e.target.value }))}>
                  {COLL_ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                <Btn kind="ghost" height={30} onClick={() => act.run(() => api.del(`/api/admin/mail/collections/${c.id}/shares/${s.user_id}`), "Fjernet")}>Fjern</Btn>
              </div>
            ))}
            <div style={{ paddingLeft: 20, display: "flex" }}>
              <PersonPicker needMailbox placeholder="Giv adgang (redigere)" onPick={(p) => act.run(() => api.put(`/api/admin/mail/collections/${c.id}/shares/${p.id}`, { role: "editor" }), "Adgang givet")} />
            </div>
          </div>
        ))}
        {data.data?.shared.length === 0 && <Empty>Ingen fælles kalendere eller adressebøger endnu.</Empty>}
        <form style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end", borderTop: "1px solid #e2e8f0", paddingTop: 12 }} onSubmit={(e) => {
          e.preventDefault();
          act.run(() => api.post(kind === "calendar" ? "/api/admin/mail/shared-calendars" : "/api/admin/mail/shared-address-books", { name }), "Oprettet").then((ok) => ok && setName(""));
        }}>
          <label style={label}>Type<select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} style={select}><option value="calendar">Kalender</option><option value="address_book">Adressebog</option></select></label>
          <label style={{ ...label, flex: "1 1 220px" }}>Navn<input required value={name} placeholder="Mødelokaler, Kunder …" onChange={(e) => setName(e.target.value)} style={input} /></label>
          <Btn kind="primary" type="submit" disabled={act.busy}>Opret</Btn>
        </form>
      </Card>
      <Card title="Pr. bruger">
        <Table head={["Bruger", "Kalendere", "Adressebøger", "Kontakter"]}>
          {(data.data?.per_user ?? []).map((u) => (
            <tr key={u.user_id}>
              <td style={td}><Link to={`/mail/users/${u.user_id}`}>{u.display_name || u.email}</Link></td>
              <td style={td}>{u.calendars}</td><td style={td}>{u.address_books}</td><td style={td}>{fmt(u.contacts)}</td>
            </tr>
          ))}
        </Table>
        <p style={muted}>En fratrådt medarbejders kalendere og adressebøger overføres fra brugerens side (Fratrædelse).</p>
      </Card>
    </div>
  );
}

// ── Afsendelse ──────────────────────────────────────────────────────────────

type Rates = { messages: number; bounced: number; complained: number; delivered: number; bounce_rate: number; complaint_rate: number };
type SendingData = {
  relay_host: string; api_token_set: boolean; rotated_at: string | null; credentials_age_days: number | null; paused: boolean;
  rates_24h: Rates; rates_30d: Rates; recent_problems: { recipient_domain: string | null; type: string; at: string }[];
  webhook_endpoints: { id: number; name: string; active: boolean; received_count: number; last_received_at: string | null }[];
  thresholds: { bounce: number; complaint: number };
};
const pct = (v: number, digits = 1) => `${(v * 100).toLocaleString("da-DK", { maximumFractionDigits: digits })} %`;

function Sending() {
  const data = useLoad(() => api.get<SendingData>("/api/admin/mail/sending"), []);
  const act = useRun(data.reload);
  const [form, setForm] = useState({ username: "", password: "" });
  const d = data.data;
  if (!d) return data.error ? <ErrorBox>{data.error}</ErrorBox> : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <Stat label="Bounce-rate 24 t" value={pct(d.rates_24h.bounce_rate)} sub={`Advarsel over ${pct(d.thresholds.bounce, 0)} · ${d.rates_24h.bounced} af ${d.rates_24h.messages}`} />
        <Stat label="Klager 24 t" value={pct(d.rates_24h.complaint_rate, 2)} sub={`Advarsel over ${pct(d.thresholds.complaint, 1)} · ${d.rates_24h.complained} klager`} />
        <Stat label="Bounce-rate 30 dage" value={pct(d.rates_30d.bounce_rate)} sub={`${fmt(d.rates_30d.messages)} mails`} />
        <Stat label="Relæ" value={d.paused ? "På pause" : "Kører"} sub={d.relay_host} />
      </div>
      <Card title="Lettermint-forbindelsen">
        <p style={muted}>
          Stalwart sender al udgående mail til <code>{d.relay_host}</code> med SMTP-login. Legitimationen blev sidst skiftet{" "}
          {d.rotated_at ? `${fmtDate(d.rotated_at)} (${d.credentials_age_days} dage siden)` : "aldrig fra denne side"}.
          API-nøgle til Lettermint {d.api_token_set ? "er sat" : "er ikke sat (LETTERMINT_API_TOKEN)"}. Spærrede modtagere administreres i Lettermint.
        </p>
        <Label>Leveringshændelser (indgående webhook med handler „lettermint“)</Label>
        {d.webhook_endpoints.length === 0
          ? <p style={muted}>Ingen endpoint endnu. Opret et i <a href="/admin/webhooks">Webhooks → Indgående</a> med handleren „lettermint“, og indsæt URL'en i Lettermint.</p>
          : d.webhook_endpoints.map((w) => <p key={w.id} style={muted}>{w.name}: {w.active ? "aktiv" : "slået fra"} · {fmt(w.received_count)} modtaget · sidst {w.last_received_at ? fmtDate(w.last_received_at) : "aldrig"}</p>)}
        <form style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end", borderTop: "1px solid #e2e8f0", paddingTop: 12 }} onSubmit={(e) => {
          e.preventDefault();
          const reason = askReason("Skift Lettermint SMTP-legitimationen i Stalwart?");
          if (reason) act.run(() => api.post("/api/admin/mail/sending/rotate", { ...form, reason }), "Legitimationen er skiftet").then((ok) => ok && setForm({ username: "", password: "" }));
        }}>
          <label style={{ ...label, flex: "1 1 200px" }}>SMTP-brugernavn<input required value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} style={input} autoComplete="off" /></label>
          <label style={{ ...label, flex: "1 1 200px" }}>Ny SMTP-adgangskode<input required type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} style={input} autoComplete="new-password" /></label>
          <Btn kind="primary" type="submit" disabled={act.busy}>Skift legitimation</Btn>
        </form>
        <p style={muted}>Opret den nye i Lettermint først. Adgangskoden sendes direkte til Stalwart og gemmes hverken i appen eller loggen.</p>
      </Card>
      <Card title="Seneste afviste og klager">
        {d.recent_problems.length === 0 ? <Empty>Ingen.</Empty> : (
          <Table head={["Tidspunkt", "Hændelse", "Modtagerdomæne"]}>
            {d.recent_problems.map((p, i) => <tr key={i}><td style={td}>{fmtDate(p.at)}</td><td style={td}><Pill tone={p.type === "bounced" ? "red" : "orange"}>{p.type === "bounced" ? "Afvist" : "Klage"}</Pill></td><td style={td}>{p.recipient_domain ?? "—"}</td></tr>)}
          </Table>
        )}
      </Card>
    </div>
  );
}

// ── Opbevaring ──────────────────────────────────────────────────────────────

type Policy = { id: number; scope: string; days: number; applies_to: string; domain_id: number | null; domain: string | null; mailbox: string | null; updated_at: string };
type RunRow = { id: number; dry_run: boolean; status: string; mailboxes: number; expired: number; archived: number; failures: number; started_at: string; finished_at: string | null; triggered_by: string | null };
type RetentionData = { max_days: number; policies: Policy[]; runs: RunRow[]; archive_export: boolean; schedule: { enabled: boolean; next_run_at: string; last_run_at: string | null } | null; last_backup: { at: string; calendars: number; address_books: number; failures: number } | null };
const APPLIES: Record<string, string> = { all: "Alle mapper", trash: "Kun papirkurv", junk: "Kun spam" };

function Retention() {
  const data = useLoad(() => api.get<RetentionData>("/api/admin/mail/retention"), []);
  const domains = useLoad(() => api.get<Domain[]>("/api/admin/mail/domains"), []);
  const act = useRun(data.reload);
  const [dry, setDry] = useState<RunRow | null>(null);
  const [draft, setDraft] = useState<{ days: number; applies_to: string } | null>(null);
  const [override, setOverride] = useState({ domain_id: 0, days: 14, applies_to: "all" });
  const d = data.data;
  if (!d) return data.error ? <ErrorBox>{data.error}</ErrorBox> : null;
  const global = d.policies.find((p) => p.scope === "global") ?? { days: 30, applies_to: "all" } as Policy;
  const g = draft ?? { days: global.days, applies_to: global.applies_to };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      <Card title="Regel for mail" deeplink="mail-retention-policy">
        <p style={muted}>Mail slettes gennem Stalwart, når den er ældre end reglen (højst {d.max_days} dage). Kalenderaftaler og kontakter slettes aldrig automatisk. Postkassens regel går foran domænets, som går foran den globale.</p>
        <form style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); act.run(() => api.put("/api/admin/mail/retention", g), "Gemt").then(() => setDraft(null)); }}>
          <label style={label}>Dage<input type="number" min={1} max={d.max_days} required value={g.days} onChange={(e) => setDraft({ ...g, days: Number(e.target.value) })} style={{ ...input, width: 100 }} /></label>
          <label style={label}>Gælder<select value={g.applies_to} onChange={(e) => setDraft({ ...g, applies_to: e.target.value })} style={select}>{Object.entries(APPLIES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <Btn kind="primary" type="submit" disabled={!draft || act.busy}>Gem</Btn>
        </form>
        <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14 }}>
          <input type="checkbox" checked={d.archive_export} onChange={(e) => act.run(() => api.put("/api/admin/mail/retention", { days: global.days, applies_to: global.applies_to, archive_export: e.target.checked }), e.target.checked ? "Arkiveksport slået til" : "Arkiveksport slået fra")} />
          Arkiveksport: gem hver mail som .eml i arkivet før sletning, og lad brugerne hente deres mail
        </label>
      </Card>
      <Card title="Regler pr. domæne">
        {d.policies.filter((p) => p.scope === "domain").map((p) => (
          <div key={p.id} style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14 }}>
            <strong style={{ flex: 1 }}>@{p.domain}</strong><span>{p.days} dage · {APPLIES[p.applies_to]}</span>
            <Btn kind="ghost" height={30} onClick={() => act.run(() => api.del(`/api/admin/mail/retention/domains/${p.domain_id}`), "Fjernet")}>Fjern</Btn>
          </div>
        ))}
        <form style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); const id = override.domain_id || domains.data?.[0]?.id; if (id) act.run(() => api.put(`/api/admin/mail/retention/domains/${id}`, { days: override.days, applies_to: override.applies_to }), "Gemt"); }}>
          <label style={label}>Domæne<select value={override.domain_id || domains.data?.[0]?.id || ""} onChange={(e) => setOverride({ ...override, domain_id: Number(e.target.value) })} style={select}>{(domains.data ?? []).map((x) => <option key={x.id} value={x.id}>@{x.name}</option>)}</select></label>
          <label style={label}>Dage<input type="number" min={1} max={d.max_days} value={override.days} onChange={(e) => setOverride({ ...override, days: Number(e.target.value) })} style={{ ...input, width: 100 }} /></label>
          <label style={label}>Gælder<select value={override.applies_to} onChange={(e) => setOverride({ ...override, applies_to: e.target.value })} style={select}>{Object.entries(APPLIES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <Btn type="submit" disabled={act.busy || !domains.data?.length}>Tilføj regel</Btn>
        </form>
      </Card>
      <Card title="Kørsler" aside={<div style={{ display: "flex", gap: 8 }}>
        <Btn height={32} disabled={act.busy} onClick={() => act.run(async () => setDry(await api.post<RunRow>("/api/admin/mail/retention/run?dryRun=true")))}>Prøvekørsel</Btn>
        <Btn kind="primary" height={32} disabled={act.busy} onClick={() => { if (confirm("Slet mail ældre end reglen nu? Det kan ikke fortrydes.")) act.run(() => api.post("/api/admin/mail/retention/run"), "Kørslen er sat i kø"); }}>Kør nu</Btn>
      </div>}>
        {dry && <p style={{ ...muted, color: "#1d4ed8" }}>Prøvekørsel: {fmt(dry.expired)} mails i {dry.mailboxes} postkasser ville blive slettet{dry.failures ? ` (${dry.failures} fejl)` : ""}.</p>}
        <p style={muted}>Kører hver nat{d.schedule ? ` — næste ${fmtDate(d.schedule.next_run_at)}` : ""}. Sidste backup af kalendere og kontakter: {d.last_backup ? `${fmtDate(d.last_backup.at)} (${d.last_backup.calendars} kalendere, ${d.last_backup.address_books} adressebøger${d.last_backup.failures ? `, ${d.last_backup.failures} fejl` : ""})` : "ingen endnu"}.</p>
        <Table head={["Startet", "Type", "Postkasser", "Slettet", "Arkiveret", "Fejl", "Status"]}>
          {d.runs.map((r) => (
            <tr key={r.id}>
              <td style={td}>{fmtDate(r.started_at)}{r.triggered_by ? <div style={{ fontSize: 12, color: "#64748b" }}>{r.triggered_by}</div> : null}</td>
              <td style={td}>{r.dry_run ? "Prøve" : "Sletning"}</td><td style={td}>{r.mailboxes}</td><td style={td}>{fmt(r.expired)}</td>
              <td style={td}>{fmt(r.archived)}</td><td style={td}>{r.failures}</td>
              <td style={td}><Pill tone={r.status === "succeeded" ? "green" : r.status === "running" ? "blue" : "red"}>{r.status === "succeeded" ? "OK" : r.status === "running" ? "Kører" : "Fejlet"}</Pill></td>
            </tr>
          ))}
        </Table>
        {d.runs.length === 0 && <Empty>Ingen kørsler endnu.</Empty>}
      </Card>
    </div>
  );
}

// ── Drift ───────────────────────────────────────────────────────────────────

type HealthData = {
  backend: string; hostname: string | null; paused: boolean;
  stalwart: { reachable: boolean; version: string | null; uptime_seconds: number | null; error?: string; queue: { messages: number; oldest_at: string | null; paused: boolean | null } | null };
  certificate: { host: string | null; not_after: string | null; days_left: number | null; error: string | null };
  last_sync: { at: string; mailboxes: number; drift: { address: string; problem: string }[] } | null;
  last_backup: { at: string; calendars: number; address_books: number; failures: number } | null;
  drift: { id: number; address: string; drift: string }[]; provisioning_errors: { id: number; address: string; error: string }[];
  schedules: { name: string; enabled: boolean; next_run_at: string; last_run_at: string | null }[];
};

function Health() {
  const data = useLoad(() => api.get<HealthData>("/api/admin/mail/health"), []);
  const act = useRun(data.reload);
  const h = data.data;
  if (!h) return data.error ? <ErrorBox>{data.error}</ErrorBox> : null;
  const q = h.stalwart.queue;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {act.error && <ErrorBox>{act.error}</ErrorBox>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <Stat label="Stalwart" value={h.stalwart.reachable ? "Svarer" : "Svarer ikke"} sub={h.stalwart.error ?? h.stalwart.version ?? h.backend} />
        <Stat label="Udgående kø" value={q ? fmt(q.messages) : "—"} sub={q?.oldest_at ? `Ældste fra ${fmtDate(q.oldest_at)}` : "Ingen i kø"} />
        <Stat label="TLS-certifikat" value={h.certificate.days_left != null ? `${h.certificate.days_left} dage` : "—"} sub={h.certificate.error ?? (h.certificate.not_after ? `Udløber ${fmtDate(h.certificate.not_after)}` : undefined)} />
        <Stat label="Backup" value={h.last_backup ? fmtDate(h.last_backup.at) : "Ingen"} sub={h.last_backup ? `${h.last_backup.calendars} kalendere · ${h.last_backup.address_books} adressebøger` : "Kalendere og kontakter, hver nat"} />
      </div>
      <Card title="Udgående relæ" aside={<Pill tone={h.paused ? "orange" : "green"}>{h.paused ? "På pause" : "Kører"}</Pill>}>
        <p style={muted}>En hændelse hos Lettermint viser sig først som en voksende kø. Mail venter i Stalwarts kø og prøves igen, så intet går tabt under en pause.</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {h.paused
            ? <Btn kind="primary" onClick={() => act.run(() => api.post("/api/admin/mail/health/queue/resume"), "Relæet kører igen")}>Genoptag</Btn>
            : <Btn onClick={() => { if (confirm("Sæt al udgående mail på pause?")) act.run(() => api.post("/api/admin/mail/health/queue/pause"), "Sat på pause"); }}>Sæt på pause</Btn>}
          <Btn onClick={() => act.run(() => api.post("/api/admin/mail/health/queue/flush"), "Køen prøves igen nu")}>Tøm køen nu</Btn>
          <Btn icon={RefreshCw} onClick={() => act.run(() => api.post("/api/admin/mail/health/sync"), "Synkronisering sat i kø")}>Synkronisér nu</Btn>
        </div>
      </Card>
      <Card title="Afvigelser mellem appen og Stalwart" deeplink="mail-drift">
        <p style={muted}>Sidste synkronisering: {h.last_sync ? `${fmtDate(h.last_sync.at)} (${h.last_sync.mailboxes} postkasser)` : "aldrig"}.</p>
        {[...h.drift.map((d) => ({ address: d.address, problem: d.drift })), ...(h.last_sync?.drift ?? []).filter((d) => !h.drift.some((x) => x.address === d.address))].map((d) => (
          <div key={d.address} style={{ display: "flex", gap: 10, fontSize: 13 }}><span style={mono}>{d.address}</span><span style={{ color: "#9a3412" }}>{d.problem}</span></div>
        ))}
        {h.provisioning_errors.map((d) => (
          <div key={d.id} style={{ display: "flex", gap: 10, fontSize: 13 }}><span style={mono}>{d.address}</span><span style={{ color: "#b91c1c" }}>Kan ikke sættes op: {d.error}</span></div>
        ))}
        {!h.drift.length && !h.provisioning_errors.length && !(h.last_sync?.drift.length) && <Empty>Ingen afvigelser.</Empty>}
      </Card>
      <Card title="Planlagte jobs">
        <Table head={["Job", "Næste kørsel", "Sidste kørsel", ""]}>
          {h.schedules.map((s) => <tr key={s.name}><td style={{ ...td, ...mono }}>{s.name}</td><td style={td}>{fmtDate(s.next_run_at)}</td><td style={td}>{s.last_run_at ? fmtDate(s.last_run_at) : "—"}</td><td style={td}>{s.enabled ? "" : <Pill tone="gray">Pause</Pill>}</td></tr>)}
        </Table>
        <p style={muted}>Pause og interval styres i <a href="/admin/jobs">Jobs og oprydning</a>.</p>
      </Card>
    </div>
  );
}

// ── Audit ───────────────────────────────────────────────────────────────────

type AuditRow = { id: number; ts: string; action: string; table_name: string | null; row_id: string | null; before: unknown; after: unknown; detail: Record<string, unknown> | null; actor_name: string | null; actor_email: string | null };

function Audit() {
  const [f, setF] = useState({ actor: "", mailbox: "", action: "" });
  const [offset, setOffset] = useState(0);
  const q = query({ ...f, offset });
  const data = useLoad(() => api.get<{ items: AuditRow[]; total: number }>(`/api/admin/mail/audit${q}`), [q]);
  return (
    <Card title={`Alle handlinger i mailmodulet${data.data ? ` (${fmt(data.data.total)})` : ""}`} aside={
      <a href={`/api/admin/mail/audit${query({ ...f, format: "csv" })}`} download style={{ display: "inline-flex", gap: 6, alignItems: "center", fontSize: 13 }}><Download size={14} />Eksportér CSV</a>}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input aria-label="Aktør" placeholder="Aktør (navn eller e-mail)" value={f.actor} onChange={(e) => { setF({ ...f, actor: e.target.value }); setOffset(0); }} style={{ ...input, flex: "1 1 180px" }} />
        <input aria-label="Postkasse" placeholder="Postkasse eller adresse" value={f.mailbox} onChange={(e) => { setF({ ...f, mailbox: e.target.value }); setOffset(0); }} style={{ ...input, flex: "1 1 180px" }} />
        <select aria-label="Handling" value={f.action} onChange={(e) => { setF({ ...f, action: e.target.value }); setOffset(0); }} style={select}>
          <option value="">Alle handlinger</option>
          {["mailbox", "alias", "shared", "collection", "app_password", "identity", "domain", "retention", "relay", "queue", "offboard"].map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>
      {data.error && <ErrorBox>{data.error}</ErrorBox>}
      <Table head={["Tidspunkt", "Handling", "Aktør", "Detaljer"]}>
        {(data.data?.items ?? []).map((r) => (
          <tr key={r.id}>
            <td style={{ ...td, whiteSpace: "nowrap" }}>{fmtDate(r.ts)}</td>
            <td style={{ ...td, ...mono }}>{r.action}</td>
            <td style={td}>{r.actor_name || r.actor_email || "System"}</td>
            <td style={{ ...td, ...mono, fontSize: 11.5, wordBreak: "break-word", maxWidth: 520 }}>
              {[r.before ? `før ${JSON.stringify(r.before)}` : "", r.after ? `efter ${JSON.stringify(r.after)}` : "", r.detail && Object.keys(r.detail).length ? JSON.stringify(r.detail) : ""].filter(Boolean).join(" · ")}
            </td>
          </tr>
        ))}
      </Table>
      {data.data && data.data.total > 100 && (
        <div style={{ display: "flex", gap: 8 }}>
          <Btn height={32} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Forrige</Btn>
          <Btn height={32} disabled={offset + 100 >= data.data.total} onClick={() => setOffset(offset + 100)}>Næste</Btn>
        </div>
      )}
    </Card>
  );
}
