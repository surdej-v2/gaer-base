// Shared pieces of the Gær Developer pages: classification badges, change lists, the artifact page and its tabs.
import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage, devState, useCrumb } from "./DeveloperShell";

export const CLS_DA: Record<string, string> = { white: "Hvid", green: "Grøn", yellow: "Gul", red: "Rød" };
export const MASK_DA: Record<string, string> = { show: "Vis", round: "Afrund", interval: "Interval", aggregate: "Kun aggregeret",
  remove: "Fjern", mask: "Maskér", hash: "Hash", partial: "Delvis visning" };

export function Cls({ c, suffix }: { c: string | null | undefined; suffix?: ReactNode }) {
  if (!c) return <span className="muted">—</span>;
  return <span className={`cls ${c}`}>{CLS_DA[c] ?? c}{suffix}</span>;
}

export type Change = { kind: string; text: string; type: string };

export function Changes({ items }: { items: Change[] | null | undefined }) {
  if (!items?.length) return <p className="muted small">Ingen ændringer i kontrakten.</p>;
  return (
    <div>
      {items.map((c, i) => (
        <div key={i} className="dev-change">
          <span className="mono" style={{ color: c.type === "major" ? "#b91c1c" : c.type === "minor" ? "#b45309" : "#475569" }}>{c.kind}</span> {c.text}
        </div>
      ))}
    </div>
  );
}

export function ChangePill({ type }: { type: string }) {
  const tone = type === "major" ? "red" : type === "minor" ? "amber" : type === "initial" ? "blue" : "";
  const label = { major: "Major", minor: "Minor", patch: "Patch", initial: "Første version" }[type] ?? type;
  return <span className={`pill ${tone}`}>{label}</span>;
}

export function StatusDot({ status, label }: { status: string; label?: string }) {
  const color = status === "active" || status === "published" || status === "ok" || status === "succeeded" ? "#16a34a"
    : status === "review_required" || status === "pending" || status === "waiting" || status === "deferred" || status === "running" ? "#f59e0b"
      : status === "disabled" || status === "draft" || status === "superseded" || status === "withdrawn" ? "#94a3b8" : "#dc2626";
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
    <span style={{ width: 8, height: 8, borderRadius: 9999, background: color }} />{label ?? status}
  </span>;
}

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="dev-tabs" role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} type="button" role="tab" aria-selected={value === k} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}

export type ArtifactDetail = {
  id: number; type: string; typeLabel: string; slug: string; name: string; description: string; status: string; statusLabel: string;
  approvalFlow: boolean; version: string | null;
  current: (Version & { definition: Record<string, unknown>; contract: Record<string, unknown> }) | null;
  draft: (Version & { definition: Record<string, unknown>; contract: Record<string, unknown> }) | null;
  approvals: { id: number; user_id: number; role: string; roleLabel: string; decision: string; name: string | null; note: string }[];
  raci: { userId: number; name: string | null; roles: string[] }[];
  versions: (Version & { author: string | null })[];
  reviews: { id: number; status: string; cause_artifact_id: number; cause_name: string; cause_type: string; ai_analysis: string | null;
    impact: { changes?: Change[]; uses?: string }; created_at: string; decided_at: string | null; note: string }[];
  usedBy: { id: number; type: string; slug: string; name: string; status: string; detail: string }[];
  uses: { id: number; type: string; slug: string; name: string; detail: string }[];
};
export type Version = { id: number; version: string; changeType: string; status: string; statusLabel: string; changes: Change[];
  note: string; reason: string; createdAt: string; submittedAt: string | null; publishedAt: string | null };

const RACI = ["R", "A", "C", "I"] as const;

export function RaciEditor({ artifact, onSaved }: { artifact: ArtifactDetail; onSaved: (a: ArtifactDetail) => void }) {
  const [rows, setRows] = useState(artifact.raci.map((r) => ({ ...r })));
  const [q, setQ] = useState("");
  const [found, setFound] = useState<{ id: number; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setRows(artifact.raci.map((r) => ({ ...r }))), [artifact]);
  useEffect(() => {
    if (!q.trim()) return setFound([]);
    const t = setTimeout(() => api.get<{ items: { id: number; name: string }[] }>(`/api/admin/dev/people?q=${encodeURIComponent(q)}`)
      .then((r) => setFound(r.items)).catch(() => {}), 200);
    return () => clearTimeout(t);
  }, [q]);
  const toggle = (uid: number, role: string) => setRows((rs) => rs.map((r) => r.userId !== uid ? r
    : { ...r, roles: r.roles.includes(role) ? r.roles.filter((x) => x !== role) : [...r.roles, role] }));
  const save = async () => {
    setError(null);
    try {
      onSaved(await api.put<ArtifactDetail>(`/api/admin/dev/artifacts/${artifact.id}/raci`,
        { assignments: rows.filter((r) => r.roles.length).map((r) => ({ userId: r.userId, roles: r.roles })) }));
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <div className="stack">
      <p className="muted small">Præcis én Accountable og mindst én Responsible. Consulted godkender minor og major, Informed får besked om alle ændringer.</p>
      <div className="dev-wrap">
        <table className="dev-table">
          <thead><tr><th>Person</th>{RACI.map((r) => <th key={r} title={r}>{r}</th>)}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.userId}>
                <td>{r.name ?? `#${r.userId}`}</td>
                {RACI.map((role) => (
                  <td key={role}><input type="checkbox" aria-label={`${r.name} ${role}`} checked={r.roles.includes(role)} onChange={() => toggle(r.userId, role)} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
        <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
          <input className="dev-input" placeholder="Tilføj person" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Søg person" />
          {found.filter((f) => !rows.some((r) => r.userId === f.id)).map((f) => (
            <button key={f.id} type="button" className="btn btn-ghost btn-sm" style={{ justifyContent: "flex-start" }}
                    onClick={() => { setRows((rs) => [...rs, { userId: f.id, name: f.name, roles: ["I"] }]); setQ(""); }}>+ {f.name}</button>
          ))}
        </div>
        <button className="btn btn-primary btn-sm" onClick={save}>Gem ejere</button>
      </div>
      {error && <Alert>{error}</Alert>}
    </div>
  );
}

export function VersionTable({ versions }: { versions: ArtifactDetail["versions"] }) {
  return (
    <div className="dev-wrap">
      <table className="dev-table">
        <thead><tr><th>Version</th><th>Type</th><th>Status</th><th>Ændringer</th><th>Af</th><th>Tid</th></tr></thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.id}>
              <td className="mono"><Link to={`/developer/godkendelser/${v.id}`}>{v.version}</Link></td>
              <td><ChangePill type={v.changeType} /></td>
              <td><StatusDot status={v.status} label={v.statusLabel} /></td>
              <td><Changes items={v.changes.slice(0, 3)} />{v.changes.length > 3 && <span className="tiny muted">+{v.changes.length - 3} mere</span>}</td>
              <td>{v.author ?? "—"}</td>
              <td className="tiny muted">{fmtDate(v.publishedAt ?? v.submittedAt ?? v.createdAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Versions, owners, reviews and usage of one artifact: the tabs every artifact page shares. */
export function ArtifactTabs({ artifact, onChange, extra }: { artifact: ArtifactDetail; onChange: (a: ArtifactDetail) => void;
  extra?: [string, string, ReactNode][] }) {
  const [tab, setTab] = useState(extra?.[0]?.[0] ?? "versions");
  const [error, setError] = useState<string | null>(null);
  const tabs: [string, string][] = [...(extra ?? []).map(([k, l]) => [k, l] as [string, string]), ["versions", "Versioner"], ["owners", "Ejere"],
    ["usage", "Bruges af"], ["reviews", `Review${artifact.reviews.some((r) => r.status === "open") ? " •" : ""}`]];
  const flow = async (on: boolean) => {
    try {
      onChange(await api.patch<ArtifactDetail>(`/api/admin/dev/artifacts/${artifact.id}`, { approval_flow: on }));
    } catch (e) {
      setError(errorText(e));
    }
  };
  const approveReview = async (id: number) => {
    try {
      await api.post(`/api/admin/dev/reviews/${id}/approve`, { note: "" });
      onChange(await api.get<ArtifactDetail>(`/api/admin/dev/artifacts/${artifact.id}`));
      devState.refresh();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <div className="stack">
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {error && <Alert>{error}</Alert>}
      {extra?.find(([k]) => k === tab)?.[2]}
      {tab === "versions" && (
        <div className="stack">
          <label className="row small" style={{ gap: 8 }}>
            <input type="checkbox" checked={artifact.approvalFlow} onChange={(e) => flow(e.target.checked)} />
            Godkendelsesflow: patch udgives straks, minor kræver R og C, major kræver A, R og C
          </label>
          {artifact.draft && (
            <Alert kind="info">
              Version {artifact.draft.version} er {artifact.draft.statusLabel.toLowerCase()} ·{" "}
              <Link to={`/developer/godkendelser/${artifact.draft.id}`}>se ændringer</Link>
            </Alert>
          )}
          <VersionTable versions={artifact.versions} />
        </div>
      )}
      {tab === "owners" && <RaciEditor artifact={artifact} onSaved={onChange} />}
      {tab === "usage" && (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
          <div className="dev-card">
            <strong>Bruges af</strong>
            {artifact.usedBy.length === 0 && <span className="muted small">Intet bruger det endnu.</span>}
            {artifact.usedBy.map((u) => <Link key={u.id} to={`/developer/artefakter/${u.id}`}>{u.name} <span className="tiny muted">{u.type}</span></Link>)}
            <span className="tiny muted">En major her gør ejerne af alt ovenfor til Consulted i godkendelsen.</span>
          </div>
          <div className="dev-card">
            <strong>Bruger</strong>
            {artifact.uses.length === 0 && <span className="muted small">Ingen afhængigheder.</span>}
            {artifact.uses.map((u) => <Link key={u.id} to={`/developer/artefakter/${u.id}`}>{u.name} <span className="tiny muted">{u.type}</span></Link>)}
          </div>
        </div>
      )}
      {tab === "reviews" && (
        <div className="stack">
          {artifact.reviews.length === 0 && <p className="muted small">Ingen reviews.</p>}
          {artifact.reviews.map((r) => (
            <div key={r.id} className="dev-card">
              <div className="spread"><strong>{r.cause_name} fik en major</strong><StatusDot status={r.status === "open" ? "pending" : "ok"} label={r.status === "open" ? "Kræver review" : "Godkendt"} /></div>
              <Changes items={r.impact?.changes} />
              {r.ai_analysis && <div className="small" style={{ whiteSpace: "pre-wrap" }}><strong>AI-analyse:</strong> {r.ai_analysis}</div>}
              {r.status === "open" && <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => approveReview(r.id)}>Godkend review</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ArtifactPage() {
  const { id } = useParams();
  const { data, error, setData } = useLoad(() => api.get<ArtifactDetail>(`/api/admin/dev/artifacts/${id}`), [id]);
  useCrumb(data?.name);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;
  return (
    <DevPage title={<>{data.name} <span className="pill mono">v{data.version ?? "—"}</span></>}
             intro={`${data.typeLabel} · ${data.slug}`} actions={<StatusDot status={data.status} label={data.statusLabel} />}>
      {data.description && <p className="small">{data.description}</p>}
      <ArtifactTabs artifact={data} onChange={setData} />
    </DevPage>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="dev-card" style={{ textAlign: "center", color: "var(--hm-muted-foreground)" }}>{children}</div>;
}
