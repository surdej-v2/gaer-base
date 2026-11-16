// /admin/data/store — Datalager: NoSQL collections and KV namespaces, owned by a group with RACI (or by you), private
// or shared, with major/minor versions approved by the RACI. Rules in server/app/datastore.py.
import { useState, type CSSProperties } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, errorText } from "../../../api";
import { Alert, fmtDate, Loading, Modal, PageHeader, useLoad } from "../../ui";
import { RoleBadge } from "../secrets/shared";
import { KindBadge, KindTag, Tag, VERSIONING_LABEL, VisibilityTag, type Container, type Person, type StoreMeta } from "./shared";

type Item = Container & {
  my_roles: string[]; accountable: Person | null; share_count: number; status: string;
  pending_version: string | null; pending_kind: "major" | "minor" | null;
};
type Waiting = { id: number; container_id: number; version: string; kind: "major" | "minor"; container_name: string; slug: string;
  fallback: boolean; breaking: number; created_at: string };
type List = { items: Item[]; waiting: Waiting[] };

const FILTERS = [["all", "Alle"], ["nosql", "NoSQL"], ["kv", "KV"], ["private", "Private"], ["shared", "Delte"], ["mine", "Mine"]] as const;

function slugify(name: string): string {
  return name.toLowerCase().replace(/æ/g, "ae").replace(/ø/g, "oe").replace(/å/g, "aa").replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 63);
}

function NewContainer({ meta, onClose }: { meta: StoreMeta; onClose: () => void }) {
  const nav = useNavigate();
  const mine = meta.groups.filter((g) => g.is_member || meta.full);
  const [f, setF] = useState({
    kind: "nosql", owner: mine[0] ? String(mine[0].id) : "me", name: "", slug: "", slugTouched: false, description: "",
    visibility: "private", versioning: "semver", minor_rule: "one_r", major_rule: "a_and_all_c", sunset_days: "90", ttl: "",
  });
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const personal = f.owner === "me";
  const slug = f.slugTouched ? f.slug : slugify(f.name);
  const save = async () => {
    setError(null);
    try {
      const r = await api.post<Container>("/api/admin/store", {
        kind: f.kind, group_id: personal ? null : Number(f.owner), slug, name: f.name, description: f.description,
        visibility: f.visibility, versioning: f.versioning, minor_rule: personal ? "auto" : f.minor_rule,
        major_rule: personal ? "a_only" : f.major_rule, sunset_days: Number(f.sunset_days) || 0,
        default_ttl_seconds: f.kind === "kv" && f.ttl ? Number(f.ttl) : null,
      });
      nav(`/data/store/${r.id}`);
    } catch (e) {
      setError(errorText(e));
    }
  };
  const choice = (on: boolean): CSSProperties => ({
    textAlign: "left", padding: 14, borderRadius: 10, cursor: "pointer", font: "inherit", color: "#020817",
    display: "flex", flexDirection: "column", gap: 4,
    ...(on ? { border: "1px solid #2563eb", background: "#eef3fe", boxShadow: "0 0 0 1px #2563eb" } : { border: "1px solid #e2e8f0", background: "#fff" }),
  });
  return (
    <Modal width={620} onClose={onClose}>
      <h3>Opret i datalageret</h3>
      <div role="radiogroup" aria-label="Type" style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
        <button type="button" role="radio" aria-checked={f.kind === "nosql"} style={choice(f.kind === "nosql")} onClick={() => setF({ ...f, kind: "nosql" })}>
          <strong>NoSQL-samling</strong><span className="small muted">JSON-dokumenter, filtre og valgfrit skema.</span>
        </button>
        <button type="button" role="radio" aria-checked={f.kind === "kv"} style={choice(f.kind === "kv")} onClick={() => setF({ ...f, kind: "kv" })}>
          <strong>KV-navnerum</strong><span className="small muted">Nøgle → værdi. Konfiguration, tekster, cache.</span>
        </button>
      </div>
      <div className="grid" style={{ "--min": "220px", "--gap": "12px" } as CSSProperties}>
        <label className="field">Navn<input value={f.name} onChange={set("name")} maxLength={120} /></label>
        <label className="field">Adresse (slug)
          <input value={slug} onChange={(e) => setF({ ...f, slug: e.target.value, slugTouched: true })} className="mono" maxLength={63} />
        </label>
        <label className="field">Ejer
          <select value={f.owner} onChange={set("owner")}>
            {mine.map((g) => <option key={g.id} value={g.id}>Gruppe: {g.name}</option>)}
            <option value="me">Kun mig (~{slug || "navn"})</option>
          </select>
        </label>
        <label className="field">Synlighed
          <select value={f.visibility} onChange={set("visibility")}>
            <option value="private">Privat</option><option value="shared">Delt</option>
          </select>
        </label>
        <label className="field">Versionering
          <select value={f.versioning} onChange={set("versioning")}>
            {meta.versioning.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
        </label>
        {f.kind === "kv" && (
          <label className="field">Nøgler udløber efter (sekunder)
            <input inputMode="numeric" value={f.ttl} onChange={set("ttl")} placeholder="Aldrig" />
          </label>
        )}
        {f.versioning === "semver" && !personal && (
          <>
            <label className="field">Minor godkendes af
              <select value={f.minor_rule} onChange={set("minor_rule")}>
                {meta.minor_rules.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
              </select>
            </label>
            <label className="field">Major godkendes af
              <select value={f.major_rule} onChange={set("major_rule")}>
                {meta.major_rules.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
              </select>
            </label>
          </>
        )}
      </div>
      <label className="field">Beskrivelse<textarea value={f.description} onChange={set("description")} maxLength={1000} style={{ minHeight: 64 }} /></label>
      <p className="small muted">
        {personal ? "Den er din alene. Du kan dele den med grupper bagefter."
          : "Du bliver A og R. Resten af gruppen bliver I. RACI og deling ændres bagefter under Adgang."}
        {f.visibility === "private" ? " Privat: kun ejerne kan se den." : " Delt: vælg bagefter, hvilke grupper der må læse eller skrive."}
      </p>
      <Alert>{error}</Alert>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button className="btn btn-outline btn-sm" onClick={onClose}>Annuller</button>
        <button className="btn btn-primary btn-sm" disabled={!f.name.trim() || !slug} onClick={save}>
          {f.kind === "kv" ? "Opret navnerum" : "Opret samling"}
        </button>
      </div>
    </Modal>
  );
}

export default function Store() {
  const [params, setParams] = useSearchParams();
  const filter = params.get("vis") ?? "all";
  const archived = params.get("arkiv") === "1";
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const meta = useLoad(() => api.get<StoreMeta>("/api/admin/store/meta"), []);
  const list = useLoad(() => api.get<List>(`/api/admin/store${archived ? "?archived=true" : ""}`), [archived]);
  const items = (list.data?.items ?? []).filter((c) => {
    if (filter === "nosql" || filter === "kv") return c.kind === filter;
    if (filter === "private" || filter === "shared") return c.visibility === filter;
    if (filter === "mine") return c.personal || c.my_roles.length > 0;
    return true;
  }).filter((c) => !q || `${c.name} ${c.slug} ${c.group_name ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  const waiting = list.data?.waiting ?? [];
  const all = list.data?.items ?? [];
  return (
    <>
      <PageHeader eyebrow="/admin/data/store" title="Datalager">
        NoSQL-samlinger med JSON-dokumenter og KV-navnerum med nøgler. Hver ejes af en gruppe med RACI (eller af dig alene), er
        privat eller delt, og kan versioneres med major og minor. Minor godkendes af én R, major af A og C. Alt bliver logget.
      </PageHeader>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row" role="tablist" style={{ flexWrap: "wrap" }}>
          {FILTERS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={filter === k} className={`btn btn-sm ${filter === k ? "btn-primary" : "btn-outline"}`}
              onClick={() => setParams(k === "all" ? (archived ? { arkiv: "1" } : {}) : { vis: k, ...(archived ? { arkiv: "1" } : {}) })}>{label}</button>
          ))}
          <button className={`btn btn-sm ${archived ? "btn-dark" : "btn-ghost"}`} onClick={() => setParams(archived ? {} : { arkiv: "1" })}>
            {archived ? "Viser arkiverede" : "Arkiverede"}
          </button>
        </div>
        <div className="row">
          <input type="search" aria-label="Søg" placeholder="Søg i navn eller gruppe…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} />
          <button className="btn btn-primary btn-sm" disabled={!meta.data} onClick={() => setCreating(true)}>Ny samling eller navnerum</button>
        </div>
      </div>
      <Alert>{list.error || meta.error}</Alert>

      <div className="grid" style={{ "--min": "180px", "--gap": "12px" } as CSSProperties}>
        {[["NoSQL-samlinger", all.filter((c) => c.kind === "nosql").length],
          ["KV-navnerum", all.filter((c) => c.kind === "kv").length],
          ["Venter på dig", waiting.length],
          ["Delte", all.filter((c) => c.visibility === "shared").length]].map(([label, n]) => (
          <div key={label} className="card" style={{ gap: 2, padding: "14px 16px" }}>
            <span className="small muted" style={{ fontWeight: 600 }}>{label}</span>
            <span className="stat" style={{ fontSize: 26 }}>{n}</span>
          </div>
        ))}
      </div>

      {waiting.length > 0 && (
        <div className="card" style={{ gap: 10 }}>
          <h3>Venter på dig</h3>
          {waiting.map((w) => (
            <Link key={w.id} to={`/data/store/${w.container_id}?tab=versions`} className="row"
              style={{ padding: "10px 12px", border: "1px solid #e2e8f0", borderRadius: 10, color: "#020817", textDecoration: "none" }}>
              <KindBadge kind={w.kind} />
              <span className="mono" style={{ fontWeight: 600 }}>{w.container_name} → {w.version}</span>
              <span className="small muted">
                {w.breaking ? `${w.breaking} brud · ` : ""}{w.fallback ? "ingen andre kunne godkende — du kan som admin" : "du er godkender"} · {fmtDate(w.created_at)}
              </span>
            </Link>
          ))}
        </div>
      )}

      {list.loading && !list.data ? <Loading /> : items.length === 0 ? (
        <p className="muted">{all.length ? "Ingen samlinger passer til filteret." : "Der er ingen samlinger endnu. Opret den første."}</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Navn</th><th>Type</th><th>Synlighed</th><th>Ejer</th><th>Version</th><th>Versionering</th><th>Mine roller</th><th>Ændret</th></tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={`/data/store/${c.id}`} className="mono" style={{ fontWeight: 600 }}>{c.ref}</Link>
                    <div className="small muted">{c.name} · {c.items} {c.kind === "kv" ? "nøgler" : "dokumenter"}</div>
                  </td>
                  <td><KindTag kind={c.kind} /></td>
                  <td><VisibilityTag c={c} /></td>
                  <td>{c.personal ? "Mig" : c.group_name}{c.status === "orphaned" && <> <Tag tone="red">Uden A</Tag></>}</td>
                  <td>
                    <span className="mono" style={{ fontWeight: 600 }}>{c.current_version ?? "—"}</span>
                    {c.pending_version && <> <Tag tone="orange">{c.pending_version} venter</Tag></>}
                  </td>
                  <td>{VERSIONING_LABEL[c.versioning]}</td>
                  <td>
                    <span className="row" style={{ "--gap": "4px" } as CSSProperties}>
                      {c.personal ? <span className="small muted">Ejer</span> : c.my_roles.length ? c.my_roles.map((r) => <RoleBadge key={r} role={r} />)
                        : <span className="small muted">{c.my_level ? "Via deling" : "—"}</span>}
                    </span>
                  </td>
                  <td className="small muted">{fmtDate(c.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && meta.data && <NewContainer meta={meta.data} onClose={() => setCreating(false)} />}
    </>
  );
}
