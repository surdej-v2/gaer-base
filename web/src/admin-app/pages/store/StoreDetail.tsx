// /admin/data/store/:id — one collection or namespace: its content (documents or keys, with revisions), its versions
// (propose, approve, history) and its access (owner, visibility, shares, RACI, versioning rules, schema). Items and
// versions go through /api/store with your own access; governance through /api/admin/store. Rules in
// server/app/datastore.py.
import { useEffect, useState, type CSSProperties } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, errorText, query } from "../../../api";
import { Alert, fmtDate, Loading, PageHeader, useLoad, useToast } from "../../ui";
import { ROLE_LABEL, ROLES, RoleBadge, toggleRole, type Raci } from "../secrets/shared";
import {
  KindBadge, KindTag, LEVEL_LABEL, RELEASE_STATUS, RULE_LABEL, Tag, VERSIONING_LABEL, VisibilityTag,
  type Container, type Person, type Release, type Role, type StoreMeta,
} from "./shared";

type Detail = Container & {
  raci: (Person & { roles: Role[] })[]; my_roles: Role[]; shares: { group_id: number; group_name: string; level: "read" | "write" }[];
  created_by: Person | null; pending: Release | null; status: string; minor_rule_label: string; major_rule_label: string;
};
type Item = { key: string; value: unknown; private: boolean; rev?: number; expires_at?: string | null; updated_at?: string };
type Items = { version: string | null; total: number; items: Item[] };
type Revision = { rev: number; value: unknown; private: boolean; deleted: boolean; changed_by: Person | null; changed_at: string };
type Analysis = {
  first: boolean; next_minor: string | null; next_major: string; breaking: string[]; item_count: number;
  changes: { added: string[]; changed: string[]; removed: string[]; counts: Record<string, number>; schema_changed: boolean };
};
type Versions = { current: string | null; items: Release[]; next: Analysis | null };

const TABS = [["content", "Indhold"], ["versions", "Versioner"], ["access", "Adgang og regler"]] as const;
const LIMIT = 50;

const pretty = (v: unknown) => JSON.stringify(v, null, 2);
const short = (v: unknown) => {
  const s = JSON.stringify(v);
  return s.length > 90 ? `${s.slice(0, 90)}…` : s;
};
const canWrite = (c: Container) => c.my_level === "write" || c.my_level === "manage";

// ── Content ────────────────────────────────────────────────────────────────

function Editor({ c, item, onSaved, onClose }: { c: Detail; item: Item | null; onSaved: () => void; onClose: () => void }) {
  const toast = useToast();
  const isNew = item === null;
  const [key, setKey] = useState(item?.key ?? "");
  const [text, setText] = useState(item ? pretty(item.value) : c.kind === "nosql" ? "{\n  \n}" : "\"\"");
  const [priv, setPriv] = useState(item?.private ?? false);
  const [ttl, setTtl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [revs, setRevs] = useState<Revision[] | null>(null);
  const base = `/api/store/${c.id}/items`;
  useEffect(() => {
    if (!item || c.versioning === "none") return;
    api.get<{ items: Revision[] }>(`${base}/${encodeURIComponent(item.key)}/revisions`).then((r) => setRevs(r.items)).catch(() => setRevs([]));
  }, [item?.key, item?.rev]);
  const save = async () => {
    setError(null);
    let value: unknown;
    try {
      value = JSON.parse(text);
    } catch {
      setError("Værdien er ikke gyldig JSON");
      return;
    }
    try {
      const body = { value, private: priv, ttl_seconds: ttl ? Number(ttl) : null };
      if (isNew && c.kind === "nosql" && !key) await api.post(base, body);
      else await api.put(`${base}/${encodeURIComponent(key)}`, body);
      toast(c.versioning === "semver" ? "Gemt i kladden" : "Gemt");
      onSaved();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const remove = async () => {
    if (!item || !window.confirm(`Slet ${item.key}?`)) return;
    try {
      await api.del(`${base}/${encodeURIComponent(item.key)}`);
      toast("Slettet");
      onSaved();
      onClose();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const restore = async (rev: number) => {
    try {
      await api.post(`${base}/${encodeURIComponent(item!.key)}/revisions/${rev}/restore`);
      toast(`Revision ${rev} gendannet`);
      onSaved();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <div className="card" style={{ gap: 12 }}>
      <div className="spread">
        <h3 className="mono">{isNew ? (c.kind === "kv" ? "Ny nøgle" : "Nyt dokument") : item.key}</h3>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Luk</button>
      </div>
      {isNew && (
        <label className="field">{c.kind === "kv" ? "Nøgle" : "Id (tomt = et nyt id)"}
          <input className="mono" value={key} onChange={(e) => setKey(e.target.value)} maxLength={200} placeholder={c.kind === "kv" ? "checkout.max_items" : ""} />
        </label>
      )}
      <label className="field">Værdi (JSON)
        <textarea className="mono" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false}
          style={{ minHeight: 220, fontSize: 13, background: "#0f172a", color: "#e2e8f0" }} />
      </label>
      <div className="row">
        <label className="row small" style={{ "--gap": "6px" } as CSSProperties}>
          <input type="checkbox" checked={priv} onChange={(e) => setPriv(e.target.checked)} /> Privat (kun R og A ser den)
        </label>
        {c.kind === "kv" && (
          <label className="row small" style={{ "--gap": "6px" } as CSSProperties}>Udløber om
            <input inputMode="numeric" value={ttl} onChange={(e) => setTtl(e.target.value)} placeholder={c.default_ttl_seconds ? String(c.default_ttl_seconds) : "aldrig"} style={{ width: 110 }} /> sek.
          </label>
        )}
      </div>
      {c.json_schema && <p className="small muted">Dokumentet skal passe til samlingens skema.</p>}
      <Alert>{error}</Alert>
      <div className="row">
        <button className="btn btn-primary btn-sm" onClick={save}>{c.versioning === "semver" ? "Gem i kladden" : "Gem"}</button>
        {!isNew && <button className="btn btn-outline btn-sm" style={{ color: "#b91c1c" }} onClick={remove}>Slet</button>}
      </div>
      {revs && revs.length > 0 && (
        <div className="stack" style={{ "--gap": "6px" } as CSSProperties}>
          <h3 style={{ fontSize: 14 }}>Revisioner</h3>
          {revs.map((r) => (
            <div key={r.rev} className="row small" style={{ borderTop: "1px solid #f1f5f9", paddingTop: 6 }}>
              <span className="mono" style={{ fontWeight: 700, width: 56 }}>rev {r.rev}</span>
              <span className="muted" style={{ flex: 1, minWidth: 160 }}>
                {r.deleted ? "slettet" : short(r.value)} · {r.changed_by?.name ?? "ukendt"} · {fmtDate(r.changed_at)}
              </span>
              {r.rev !== item?.rev && <button className="btn btn-ghost btn-sm" onClick={() => restore(r.rev)}>Gendan</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Content({ c, versions }: { c: Detail; versions: Versions | null }) {
  const writer = canWrite(c);
  const semver = c.versioning === "semver";
  const [version, setVersion] = useState<string>(semver && writer ? "draft" : "");
  const [filter, setFilter] = useState("");
  const [applied, setApplied] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<Item | null | "new">(null);
  const list = useLoad(() => api.get<Items>(`/api/store/${c.id}/items${query({
    version: version || null, limit: LIMIT, offset,
    ...(c.kind === "nosql" ? { filter: applied || null } : { prefix: applied || null }),
  })}`), [version, applied, offset]);
  const released = (versions?.items ?? []).filter((r) => r.status === "approved");
  const draftView = !semver || version === "draft" || (!version && !c.current_version);
  return (
    <div className="row" style={{ alignItems: "flex-start", "--gap": "16px" } as CSSProperties}>
      <div className="card" style={{ flex: "1 1 380px", minWidth: 0, gap: 10 }}>
        <div className="row" style={{ alignItems: "flex-end" }}>
          {semver && (
            <label className="field">Version
              <select value={version} onChange={(e) => { setVersion(e.target.value); setOffset(0); }}>
                {writer && <option value="draft">Kladde (arbejdskopi)</option>}
                <option value="">Gældende{c.current_version ? ` (${c.current_version})` : ""}</option>
                {released.map((r) => <option key={r.id} value={r.version} disabled={!!r.items_purged_at}>{r.version}{r.items_purged_at ? " · udfaset" : ""}</option>)}
              </select>
            </label>
          )}
          <label className="field" style={{ flex: 1, minWidth: 200 }}>{c.kind === "nosql" ? "Filter (JSON)" : "Præfiks"}
            <input className="mono" value={filter} onChange={(e) => setFilter(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { setApplied(filter); setOffset(0); } }}
              placeholder={c.kind === "nosql" ? '{ "status": "aktiv" }' : "checkout."} />
          </label>
          <button className="btn btn-outline btn-sm" onClick={() => { setApplied(filter); setOffset(0); }}>Søg</button>
          {writer && draftView && <button className="btn btn-primary btn-sm" onClick={() => setOpen("new")}>{c.kind === "kv" ? "Ny nøgle" : "Nyt dokument"}</button>}
        </div>
        <Alert>{list.error}</Alert>
        {list.loading && !list.data ? <Loading /> : (
          <>
            <span className="small muted">{list.data?.total ?? 0} i alt{list.data?.version ? ` · version ${list.data.version === "draft" ? "kladde" : list.data.version}` : ""}</span>
            <div className="table-wrap">
              <table>
                <thead><tr><th>{c.kind === "kv" ? "Nøgle" : "Id"}</th><th>Værdi</th><th>{draftView ? "Rev" : ""}</th></tr></thead>
                <tbody>
                  {list.data?.items.map((i) => (
                    <tr key={i.key} style={open !== "new" && open?.key === i.key ? { background: "#eef3fe" } : undefined}>
                      <td>
                        {draftView && writer
                          ? <button className="btn btn-ghost btn-sm mono" style={{ padding: 0, height: "auto", fontWeight: 600 }} onClick={() => setOpen(i)}>{i.key}</button>
                          : <span className="mono" style={{ fontWeight: 600 }}>{i.key}</span>}
                        {i.private && <> <Tag tone="gray">Privat</Tag></>}
                      </td>
                      <td className="mono small" style={{ whiteSpace: "normal", wordBreak: "break-all" }}>{short(i.value)}</td>
                      <td className="small muted">{i.rev ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {(list.data?.total ?? 0) > LIMIT && (
              <div className="row">
                <button className="btn btn-outline btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>Forrige</button>
                <span className="small muted">{offset + 1}–{Math.min(offset + LIMIT, list.data!.total)}</span>
                <button className="btn btn-outline btn-sm" disabled={offset + LIMIT >= list.data!.total} onClick={() => setOffset(offset + LIMIT)}>Næste</button>
              </div>
            )}
          </>
        )}
      </div>
      {open !== null && (
        <div style={{ flex: "999 1 460px", minWidth: 0 }}>
          <Editor key={open === "new" ? "new" : `${open.key}-${open.rev}`} c={c} item={open === "new" ? null : open}
            onSaved={async () => { await list.reload(); if (open === "new") setOpen(null); }} onClose={() => setOpen(null)} />
        </div>
      )}
    </div>
  );
}

// ── Versions ───────────────────────────────────────────────────────────────

function ReleaseCard({ r, onChange }: { r: Release; onChange: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const decide = async (approve: boolean) => {
    setError(null);
    try {
      await api.post(`/api/admin/store/versions/${r.id}/decide`, { approve, note: note || null });
      onChange();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const cancel = async () => {
    try {
      await api.post(`/api/store/${r.container_id}/versions/${r.id}/cancel`);
      onChange();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const counts = r.changes.counts ?? {};
  const [label, color] = RELEASE_STATUS[r.status];
  return (
    <div className="card" style={{ gap: 12, ...(r.status === "pending" ? { borderColor: "#fed7aa", background: "#fffbf5" } : {}) }}>
      <div className="row">
        <h3 className="mono">{r.version}</h3>
        <KindBadge kind={r.kind} />
        <span className="small" style={{ fontWeight: 700, color }}>{label}</span>
        <span className="small muted">
          Foreslået af {r.proposed_by?.name ?? "ukendt"} · {fmtDate(r.created_at)} · regel: {RULE_LABEL[r.approval_rule] ?? r.approval_rule}
          {r.status === "pending" && ` · udløber ${fmtDate(r.expires_at)}`}
        </span>
      </div>
      {r.note && <p className="small">{r.note}</p>}
      <p className="small muted">
        {r.item_count} {r.item_count === 1 ? "element" : "elementer"} · {counts.added ?? 0} nye · {counts.changed ?? 0} ændrede · {counts.removed ?? 0} fjernede
        {r.changes.schema_changed ? " · skemaet er ændret" : ""}
      </p>
      {r.breaking.length > 0 && (
        <div className="stack" style={{ "--gap": "4px" } as CSSProperties}>
          <strong className="small" style={{ color: "#b91c1c" }}>Brud for læsere</strong>
          <ul className="small mono" style={{ margin: 0, paddingLeft: 18 }}>{r.breaking.map((b) => <li key={b}>{b}</li>)}</ul>
        </div>
      )}
      {(r.approvals.length > 0 || r.fallback) && (
        <div className="stack" style={{ "--gap": "6px" } as CSSProperties}>
          {r.approvals.map((a, i) => (
            <div key={i} className="row small">
              {a.roles.map((role) => <RoleBadge key={role} role={role} />)}
              <span style={{ fontWeight: 600 }}>{a.approver?.name ?? "Slettet bruger"}</span>
              <Tag tone={a.decision === "approved" ? "green" : a.decision === "rejected" ? "red" : "gray"}>
                {a.decision === "approved" ? "Godkendt" : a.decision === "rejected" ? "Afvist" : "Venter"}
              </Tag>
              {a.note && <span className="muted">“{a.note}”</span>}
            </div>
          ))}
          {r.fallback && r.status === "pending" && (
            <p className="small muted">Ingen andre i RACI kan godkende, så én anden administrator med adgang til Datalager kan.</p>
          )}
        </div>
      )}
      <Alert>{error}</Alert>
      {r.can_decide && (
        <div className="stack" style={{ "--gap": "8px" } as CSSProperties}>
          <label className="field">Kommentar<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Vises i audit-loggen" /></label>
          <div className="row">
            <button className="btn btn-primary btn-sm" onClick={() => decide(true)}>Godkend {r.version}</button>
            <button className="btn btn-outline btn-sm" style={{ color: "#b91c1c" }} onClick={() => decide(false)}>Afvis</button>
            <span className="small muted">Kræver din anden faktor.</span>
          </div>
        </div>
      )}
      {r.status === "pending" && r.own && <div className="row"><button className="btn btn-ghost btn-sm" onClick={cancel}>Træk forslaget tilbage</button></div>}
    </div>
  );
}

function Propose({ c, next, onDone }: { c: Detail; next: Analysis; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const counts = next.changes.counts;
  const nothing = !next.first && !(counts.added + counts.changed + counts.removed) && !next.changes.schema_changed;
  const propose = async (kind: "auto" | "minor" | "major") => {
    setError(null);
    try {
      await api.post(`/api/store/${c.id}/versions`, { kind, note });
      setNote("");
      onDone();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <div className="card" style={{ gap: 10 }}>
      <h3>Udgiv kladden</h3>
      {next.first ? <p className="small muted">Den første version bliver 1.0 og godkendes efter major-reglen ({RULE_LABEL[c.major_rule]}).</p> : (
        <p className="small muted">
          Siden {c.current_version}: {counts.added} nye · {counts.changed} ændrede · {counts.removed} fjernede{next.changes.schema_changed ? " · skemaet er ændret" : ""}.
          Minor ({next.next_minor}) godkendes af: {RULE_LABEL[c.minor_rule]}. Major ({next.next_major}) af: {RULE_LABEL[c.major_rule]}.
        </p>
      )}
      {next.breaking.length > 0 && (
        <Alert kind="warn">
          <strong>Ændringerne bryder læsere og skal være en major:</strong>
          <ul className="mono small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>{next.breaking.map((b) => <li key={b}>{b}</li>)}</ul>
        </Alert>
      )}
      <label className="field">Note<textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} style={{ minHeight: 60 }} placeholder="Hvad er nyt, og hvorfor" /></label>
      <Alert>{error}</Alert>
      <div className="row">
        {next.first ? <button className="btn btn-primary btn-sm" onClick={() => propose("major")}>Foreslå 1.0</button> : (
          <>
            <button className="btn btn-primary btn-sm" disabled={nothing || next.breaking.length > 0} onClick={() => propose("minor")}>Foreslå {next.next_minor}</button>
            <button className="btn btn-outline btn-sm" disabled={nothing && !next.breaking.length} onClick={() => propose("major")}>Foreslå {next.next_major} (major)</button>
          </>
        )}
        {nothing && <span className="small muted">Ingen ændringer siden {c.current_version}.</span>}
      </div>
    </div>
  );
}

function VersionsTab({ c, versions, reload }: { c: Detail; versions: Versions | null; reload: () => void }) {
  if (c.versioning !== "semver") {
    return <p className="muted">Samlingen bruger {VERSIONING_LABEL[c.versioning].toLowerCase()} versionering. Slå major/minor til under Adgang og regler for at udgive versioner.</p>;
  }
  if (!versions) return <Loading />;
  const pending = versions.items.find((r) => r.status === "pending");
  const history = versions.items.filter((r) => r.status !== "pending");
  return (
    <div className="stack" style={{ "--gap": "16px" } as CSSProperties}>
      <div className="row">
        <span>Gældende version: <strong className="mono">{versions.current ?? "ingen endnu"}</strong></span>
        <span className="small muted">Klienter læser <code>/api/store/{c.ref}@{c.current_version?.split(".")[0] ?? "1"}</code> og får nyeste godkendte minor i den major.</span>
      </div>
      {pending && <ReleaseCard r={pending} onChange={reload} />}
      {!pending && versions.next && canWrite(c) && !c.archived_at && <Propose c={c} next={versions.next} onDone={reload} />}
      {history.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Version</th><th>Type</th><th>Status</th><th>Foreslået af</th><th>Afgjort</th><th>Udfases</th></tr></thead>
            <tbody>
              {history.map((r) => (
                <tr key={r.id}>
                  <td className="mono" style={{ fontWeight: 700 }}>{r.version}{r.version === versions.current && <> <Tag tone="green">Gældende</Tag></>}</td>
                  <td><KindBadge kind={r.kind} /></td>
                  <td style={{ color: RELEASE_STATUS[r.status][1], fontWeight: 600 }}>{RELEASE_STATUS[r.status][0]}</td>
                  <td>{r.proposed_by?.name ?? "—"}</td>
                  <td className="small muted">{fmtDate(r.decided_at)}</td>
                  <td className="small muted">{r.items_purged_at ? "Udfaset" : r.sunset_at ? fmtDate(r.sunset_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Access and rules ───────────────────────────────────────────────────────

function RaciMatrix({ raci, onChange, rows, disabled }: { raci: Raci; onChange: (r: Raci) => void; rows: Person[]; disabled: boolean }) {
  const [warn, setWarn] = useState<string | null>(null);
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Person</th>{ROLES.map((r) => <th key={r} style={{ textAlign: "center" }} title={ROLE_LABEL[r]}>{r}</th>)}</tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}>
                <td><div style={{ fontWeight: 600 }}>{p.name}</div><div className="small muted">{p.eligible ? p.email : "Kan kun være I (ikke admin med Datalager)"}</div></td>
                {ROLES.map((r) => {
                  const on = (raci[p.id] ?? []).includes(r);
                  const off = disabled || (r !== "I" && !p.eligible);
                  return (
                    <td key={r} style={{ textAlign: "center", padding: 6 }}>
                      <button type="button" aria-pressed={on} aria-label={`${ROLE_LABEL[r]} for ${p.name}`} disabled={off}
                        onClick={() => { const [next, err] = toggleRole(raci, p.id, r); setWarn(err); if (!err) onChange(next); }}
                        style={{ width: 36, height: 36, borderRadius: 8, cursor: off ? "not-allowed" : "pointer", fontWeight: 700,
                          border: on ? "1px solid transparent" : "1px dashed #cbd5e1", background: on ? (r === "A" ? "#0f172a" : r === "R" ? "#2563eb" : r === "C" ? "#dbeafe" : "#f1f5f9") : "#fff",
                          color: on ? (r === "A" || r === "R" ? "#fff" : r === "C" ? "#1e40af" : "#475569") : "#94a3b8", opacity: off && !on ? 0.4 : 1 }}>
                        {on ? r : "·"}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Alert kind="warn">{warn}</Alert>
    </>
  );
}

function AccessTab({ c, meta, reload }: { c: Detail; meta: StoreMeta; reload: () => void }) {
  const toast = useToast();
  const manage = c.my_level === "manage" && !c.archived_at;
  const admin = (path: string) => (c.personal ? `/api/store/${c.id}${path}` : `/api/admin/store/${c.id}${path}`);
  const [s, setS] = useState({
    name: c.name, description: c.description, visibility: c.visibility, versioning: c.versioning, minor_rule: c.minor_rule,
    major_rule: c.major_rule, sunset_days: String(c.sunset_days), ttl: c.default_ttl_seconds ? String(c.default_ttl_seconds) : "",
    schema: c.json_schema ? pretty(c.json_schema) : "",
  });
  const [shares, setShares] = useState(c.shares.map((x) => ({ group_id: x.group_id, level: x.level })));
  const [raci, setRaci] = useState<Raci>(() => Object.fromEntries(c.raci.map((p) => [p.id, p.roles])));
  const [error, setError] = useState<string | null>(null);
  const group = meta.groups.find((g) => g.id === c.group_id);
  const rows: Person[] = [...c.raci];
  for (const m of group?.members ?? []) if (!rows.some((r) => r.id === m.id)) rows.push(m);
  const [add, setAdd] = useState("");
  const candidates = meta.people.filter((p) => !rows.some((r) => r.id === p.id));
  const extra = add ? meta.people.find((p) => p.id === Number(add)) : undefined;
  if (extra) rows.push(extra);

  const run = async (fn: () => Promise<unknown>, done: string) => {
    setError(null);
    try {
      await fn();
      toast(done);
      reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const saveSettings = () => {
    let schema: unknown = null;
    if (c.kind === "nosql" && s.schema.trim()) {
      try { schema = JSON.parse(s.schema); } catch { setError("Skemaet er ikke gyldig JSON"); return; }
    }
    const body: Record<string, unknown> = {
      name: s.name, description: s.description, visibility: s.visibility, versioning: s.versioning, sunset_days: Number(s.sunset_days) || 0,
      ...(c.personal ? {} : { minor_rule: s.minor_rule, major_rule: s.major_rule }),
      ...(c.kind === "nosql" ? { json_schema: schema } : { default_ttl_seconds: s.ttl ? Number(s.ttl) : null }),
    };
    run(() => api.patch(admin(""), body), "Indstillinger gemt");
  };
  const archive = () => {
    if (!window.confirm(`Arkivér ${c.name}? Den kan ikke længere ændres.`)) return;
    run(() => (c.personal ? api.del(`/api/store/${c.id}`) : api.post(admin("/archive"), { reason: null })), "Arkiveret");
  };
  const set = (k: keyof typeof s) => (e: { target: { value: string } }) => setS({ ...s, [k]: e.target.value });

  return (
    <div className="stack" style={{ "--gap": "16px" } as CSSProperties}>
      {!manage && <Alert kind="info">Kun {c.personal ? "ejeren" : "den ansvarlige (A)"} kan ændre adgang og regler. Ændringer kræver din anden faktor.</Alert>}
      <Alert>{error}</Alert>
      <div className="grid" style={{ "--min": "420px", "--gap": "16px", alignItems: "start" } as CSSProperties}>
        <div className="card" style={{ gap: 12 }}>
          <h3>Synlighed og deling</h3>
          <div className="segmented" role="radiogroup" aria-label="Synlighed">
            {(["private", "shared"] as const).map((v) => (
              <button key={v} role="radio" aria-checked={s.visibility === v} className={s.visibility === v ? "active" : ""} disabled={!manage}
                onClick={() => setS({ ...s, visibility: v })}>{v === "private" ? "Privat" : "Delt"}</button>
            ))}
          </div>
          <p className="small muted">
            {s.visibility === "private"
              ? (c.personal ? "Kun du kan se den." : "Kun ejergruppen og personerne i RACI kan se den. R og A skriver.")
              : "Grupperne herunder får den adgang, der står. Enkelte nøgler og dokumenter kan stadig være private."}
          </p>
          <div className="stack" style={{ "--gap": "8px" } as CSSProperties}>
            {shares.map((x, i) => (
              <div key={x.group_id} className="row">
                <span style={{ flex: 1, fontWeight: 600 }}>{meta.groups.find((g) => g.id === x.group_id)?.name ?? `#${x.group_id}`}</span>
                <select aria-label="Niveau" value={x.level} disabled={!manage}
                  onChange={(e) => setShares(shares.map((y, j) => (j === i ? { ...y, level: e.target.value as "read" | "write" } : y)))}>
                  {meta.share_levels.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
                {manage && <button className="btn btn-ghost btn-sm" aria-label="Fjern" onClick={() => setShares(shares.filter((_, j) => j !== i))}>×</button>}
              </div>
            ))}
            {manage && (
              <select aria-label="Del med en gruppe" value="" onChange={(e) => e.target.value && setShares([...shares, { group_id: Number(e.target.value), level: "read" }])}>
                <option value="">+ Del med en gruppe…</option>
                {meta.groups.filter((g) => g.id !== c.group_id && !shares.some((x) => x.group_id === g.id)).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select>
            )}
          </div>
          {manage && (
            <div className="row">
              <button className="btn btn-primary btn-sm" onClick={() => run(async () => {
                if (s.visibility !== c.visibility) await api.patch(admin(""), { visibility: s.visibility });
                await api.put(admin("/shares"), { shares });
              }, "Deling gemt")}>Gem synlighed og deling</button>
            </div>
          )}
        </div>

        {!c.personal && (
          <div className="card" style={{ gap: 12 }}>
            <div className="spread"><h3>RACI</h3><span className="small muted">Præcis én A · mindst én R</span></div>
            <RaciMatrix raci={raci} onChange={setRaci} rows={rows} disabled={!manage} />
            {manage && (
              <div className="row">
                {candidates.length > 0 && (
                  <select aria-label="Tilføj person" value={add} onChange={(e) => setAdd(e.target.value)}>
                    <option value="">+ Tilføj en administrator…</option>
                    {candidates.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                )}
                <button className="btn btn-primary btn-sm" onClick={() => run(() => api.put(admin("/raci"), {
                  raci: Object.entries(raci).filter(([, v]) => v.length).map(([k, v]) => ({ user_id: Number(k), roles: v })),
                }), "RACI gemt")}>Gem RACI</button>
              </div>
            )}
            <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
              <li><strong>R</strong> skriver og foreslår versioner. <strong>A</strong> ejer, ændrer regler og godkender majors.</li>
              <li><strong>C</strong> godkender majors (efter reglen). <strong>I</strong> kan læse og får besked om nye versioner.</li>
            </ul>
          </div>
        )}
      </div>

      <div className="card" style={{ gap: 12 }}>
        <h3>Indstillinger og versionering</h3>
        <div className="grid" style={{ "--min": "220px", "--gap": "12px" } as CSSProperties}>
          <label className="field">Navn<input value={s.name} onChange={set("name")} disabled={!manage} /></label>
          <label className="field">Versionering
            <select value={s.versioning} onChange={set("versioning")} disabled={!manage}>
              {meta.versioning.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
          </label>
          {s.versioning === "semver" && !c.personal && (
            <>
              <label className="field">Minor godkendes af
                <select value={s.minor_rule} onChange={set("minor_rule")} disabled={!manage}>
                  {meta.minor_rules.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              </label>
              <label className="field">Major godkendes af
                <select value={s.major_rule} onChange={set("major_rule")} disabled={!manage}>
                  {meta.major_rules.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              </label>
            </>
          )}
          {s.versioning === "semver" && (
            <label className="field">Gamle majors kan læses i (dage)<input inputMode="numeric" value={s.sunset_days} onChange={set("sunset_days")} disabled={!manage} /></label>
          )}
          {c.kind === "kv" && (
            <label className="field">Nye nøgler udløber efter (sek.)<input inputMode="numeric" value={s.ttl} onChange={set("ttl")} placeholder="Aldrig" disabled={!manage} /></label>
          )}
        </div>
        <label className="field">Beskrivelse<textarea value={s.description} onChange={set("description")} disabled={!manage} style={{ minHeight: 60 }} /></label>
        {c.kind === "nosql" && (
          <label className="field">Skema (JSON Schema: type, properties, required, items, enum — tomt = intet skema)
            <textarea className="mono" value={s.schema} onChange={set("schema")} disabled={!manage} spellCheck={false}
              style={{ minHeight: 160, fontSize: 13 }} placeholder={'{\n  "type": "object",\n  "required": ["navn"],\n  "properties": { "navn": { "type": "string" } }\n}'} />
          </label>
        )}
        {c.kind === "nosql" && c.versioning === "semver" && (
          <p className="small muted">Et nyt skema gælder kladden med det samme og følger med i næste version. Fjerner det et felt eller ændrer en type, skal versionen være en major.</p>
        )}
        {manage && (
          <div className="row" style={{ justifyContent: "space-between" }}>
            <button className="btn btn-primary btn-sm" onClick={saveSettings}>Gem indstillinger</button>
            <button className="btn btn-outline btn-sm" style={{ color: "#b91c1c" }} onClick={archive}>Arkivér</button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function StoreDetail() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "content";
  const detail = useLoad(() => api.get<Detail>(`/api/admin/store/${id}`), [id]);
  const meta = useLoad(() => api.get<StoreMeta>("/api/admin/store/meta"), []);
  const versions = useLoad(() => (detail.data?.my_level ? api.get<Versions>(`/api/store/${id}/versions`) : Promise.resolve(null)),
    [id, detail.data?.my_level, detail.data?.updated_at]);
  const c = detail.data;
  if (detail.error) return <><Link to="/data/store">← Datalager</Link><Alert>{detail.error}</Alert></>;
  if (!c) return <Loading />;
  const reloadAll = () => { detail.reload(); versions.reload(); };
  const a = c.raci.find((p) => p.roles.includes("A"));
  return (
    <>
      <Link to="/data/store" className="small">← Datalager</Link>
      <PageHeader eyebrow={c.personal ? "Personlig" : `Gruppe: ${c.group_name}`} title={<span className="mono">{c.ref}</span>}>
        {c.name}{c.description ? ` — ${c.description}` : ""}
      </PageHeader>
      <div className="row">
        <KindTag kind={c.kind} />
        <VisibilityTag c={{ ...c, share_count: c.shares.length }} />
        <Tag tone="gray">{VERSIONING_LABEL[c.versioning]}{c.current_version ? ` · gældende ${c.current_version}` : ""}</Tag>
        {c.archived_at && <Tag tone="red">Arkiveret</Tag>}
        {c.status === "orphaned" && <Tag tone="red">Uden ansvarlig (A)</Tag>}
        <span className="small muted">
          {c.items} {c.kind === "kv" ? "nøgler" : "dokumenter"} · {c.personal ? "ejet af dig" : `A: ${a?.name ?? "ingen"}`} · din adgang: {c.my_level ? LEVEL_LABEL[c.my_level] : "ingen (kun oversigten)"}
          {c.my_roles.length > 0 && ` (${c.my_roles.join(", ")})`}
        </span>
      </div>
      {c.pending && tab !== "versions" && (
        <Alert kind="warn">
          <strong>Version {c.pending.version} ({c.pending.kind}) venter på godkendelse</strong> — {c.pending.approvals.filter((x) => x.decision === "approved").length} af {c.pending.approvals.length || 1} har sagt ja.
          {c.current_version && <> Læsere får fortsat {c.current_version}.</>}{" "}
          <button className="btn btn-outline btn-sm" onClick={() => setParams({ tab: "versions" })}>{c.pending.can_decide ? "Tag stilling" : "Se versionen"}</button>
        </Alert>
      )}
      <div className="row" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`btn btn-sm ${tab === k ? "btn-primary" : "btn-outline"}`}
            onClick={() => setParams(k === "content" ? {} : { tab: k })}>{label}</button>
        ))}
      </div>
      {tab === "content" && (c.my_level ? <Content c={c} versions={versions.data} /> : <p className="muted">Du har ikke adgang til indholdet. Bed den ansvarlige (A) om at dele samlingen med en af dine grupper.</p>)}
      {tab === "versions" && (c.my_level ? <VersionsTab c={c} versions={versions.data} reload={reloadAll} /> : <p className="muted">Du har ikke adgang til versionerne.</p>)}
      {tab === "access" && (meta.data ? <AccessTab key={c.updated_at} c={c} meta={meta.data} reload={reloadAll} /> : <Loading />)}
    </>
  );
}
