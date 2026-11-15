// /developer/godkendelser — what waits for me, the history, and one version with its changes and approvers (F-3, F-4).
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage, devState, useCrumb } from "./DeveloperShell";
import { ChangePill, Changes, Empty, StatusDot, type Change } from "./devui";

type Item = { id: number; version: string; changeType: string; status: string; statusLabel: string; changes: Change[]; artifactId: number;
  typeLabel: string; name: string; role: string | null; author: string | null; submittedAt: string | null; createdAt: string };

export function ApprovalList({ scope }: { scope: "mine" | "history" }) {
  const { data, error } = useLoad(() => api.get<{ items: Item[] }>(`/api/admin/dev/approvals?scope=${scope}`), [scope]);
  const navigate = useNavigate();
  return (
    <DevPage title={scope === "mine" ? "Venter på mig" : "Historik"}
             intro={scope === "mine" ? "Versioner, hvor du er godkender." : "Alle versioner, der er sendt, udgivet, afvist eller trukket tilbage."}>
      {error && <Alert>{error}</Alert>}
      {data && data.items.length === 0 && <Empty>{scope === "mine" ? "Intet venter på dig." : "Ingen versioner endnu."}</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap">
          <table className="dev-table">
            <thead><tr><th>Artefakt</th><th>Version</th><th>Type</th><th>Status</th>{scope === "mine" && <th>Din rolle</th>}<th>Af</th><th>Sendt</th></tr></thead>
            <tbody>
              {data.items.map((i) => (
                <tr key={i.id} className="clickable" onClick={() => navigate(`/developer/godkendelser/${i.id}`)}>
                  <td><strong>{i.name}</strong> <span className="tiny muted">{i.typeLabel}</span></td>
                  <td className="mono">{i.version}</td>
                  <td><ChangePill type={i.changeType} /></td>
                  <td><StatusDot status={i.status} label={i.statusLabel} /></td>
                  {scope === "mine" && <td>{i.role}</td>}
                  <td>{i.author ?? "—"}</td>
                  <td className="tiny muted">{fmtDate(i.submittedAt ?? i.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </DevPage>
  );
}

export const ApprovalsMine = () => <ApprovalList scope="mine" />;
export const ApprovalsHistory = () => <ApprovalList scope="history" />;

type Detail = {
  artifact: { id: number; name: string; typeLabel: string; slug: string };
  version: { id: number; version: string; changeType: string; status: string; statusLabel: string; changes: Change[]; note: string; reason: string;
    definition: unknown; contract: unknown };
  from: string | null; previous: { definition: unknown } | null; author: string | null;
  approvals: { id: number; user_id: number; name: string | null; role: string; roleLabel: string; decision: string; note: string }[];
  needs: string[]; informed: boolean; mine: boolean;
};

export function ApprovalDetail() {
  const { id } = useParams();
  const { data, error, reload } = useLoad(() => api.get<Detail>(`/api/admin/dev/approvals/${id}`), [id]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  useCrumb(data ? `${data.artifact.name} ${data.version.version}` : null);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;
  const v = data.version;
  const decide = async (approve: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      await api.post(`/api/admin/dev/approvals/${v.id}`, { approve, note });
      await reload();
      devState.refresh();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const decisionText = (d: string) => d === "approved" ? "Godkendt" : d === "rejected" ? "Afvist" : "Afventer";
  return (
    <DevPage title={<>{data.artifact.name} · {data.from ? `v${data.from} → ` : ""}v{v.version}</>}
             intro={`Systemets vurdering: ${v.changeType === "major" ? "bryder interfacet" : v.changeType === "minor" ? "tilføjelse, bryder ikke interfacet" : v.changeType === "initial" ? "første version" : "rettelse uden ændring af kontrakten"}`}
             actions={<><ChangePill type={v.changeType} /><StatusDot status={v.status} label={v.statusLabel} /></>}>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
        <div className="dev-card">
          <strong>Ændringer</strong>
          <Changes items={v.changes} />
          {!v.changes.some((c) => c.kind === "-") && <span className="tiny muted">Ingen felter fjernet eller omdøbt</span>}
          {v.reason && <div className="small"><strong>Begrundelse:</strong> {v.reason}</div>}
          {v.note && <div className="small"><strong>Note:</strong> {v.note}</div>}
          <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setFull(!full)}>{full ? "Skjul fuld diff" : "Se fuld diff →"}</button>
        </div>
        <div className="dev-card">
          <strong>Godkendere</strong>
          {data.approvals.length === 0 && <span className="small muted">{data.needs.length ? "Ingen godkendere" : "Ingen godkendelse kræves (patch eller flowet er slået fra)."}</span>}
          {data.approvals.map((a) => (
            <div key={a.id} className="spread small">
              <span>{a.roleLabel} · {a.name}</span>
              <StatusDot status={a.decision === "approved" ? "ok" : a.decision === "rejected" ? "failed" : "pending"} label={decisionText(a.decision)} />
            </div>
          ))}
          {!data.needs.includes("A") && <div className="spread small muted"><span>Accountable</span><span>Kun ved major</span></div>}
          {data.informed && <div className="spread small muted"><span>Informed</span><span>Får besked</span></div>}
          <span className="tiny muted">Af {data.author ?? "—"} · <Link to={`/developer/artefakter/${data.artifact.id}`}>Åbn {data.artifact.typeLabel.toLowerCase()}</Link></span>
        </div>
      </div>
      {full && (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
          <div className="stack"><span className="small muted">Nu (v{data.from ?? "—"})</span><pre className="dev-code">{JSON.stringify(data.previous?.definition ?? null, null, 2)}</pre></div>
          <div className="stack"><span className="small muted">Ny (v{v.version})</span><pre className="dev-code">{JSON.stringify(v.definition, null, 2)}</pre></div>
        </div>
      )}
      {data.mine && v.status === "pending" && (
        <div className="dev-card">
          <textarea className="dev-input" style={{ height: 64, padding: 8 }} placeholder="Note (skal udfyldes ved afvisning)" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
            <button className="btn btn-outline btn-sm" disabled={busy} onClick={() => decide(false)}>Afvis</button>
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => decide(true)}>Godkend</button>
          </div>
          {err && <Alert>{err}</Alert>}
        </div>
      )}
    </DevPage>
  );
}
