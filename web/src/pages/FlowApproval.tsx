// /flow-godkendelse/:token — a step in a flow (Action-studie) waits for a person: the approver got a mail with this
// link, logs in as usual and answers here. Only the people on the step's list see it; one answer, then the flow goes on
// (server/app/routes/developer_flows.py, the public router).
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, Loading, useLoad } from "../ui";

type Approval = { id: number; title: string; message: string; data: unknown; status: string; flow: string; expiresAt: string;
  decidedAt: string | null; comment: string | null; mine: boolean; decidedByMe: boolean };

const STATUS: Record<string, string> = { pending: "Afventer dit svar", approved: "Godkendt", rejected: "Afvist", expired: "Udløbet uden svar" };

function Data({ value }: { value: unknown }) {
  if (value === null || value === undefined) return null;
  if (typeof value === "object" && !Array.isArray(value)) {
    return (
      <table style={{ borderCollapse: "collapse", fontSize: 14 }}>
        <tbody>{Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <tr key={k}><th style={{ textAlign: "left", padding: "4px 12px 4px 0", fontWeight: 600, verticalAlign: "top" }}>{k}</th>
            <td style={{ padding: "4px 0" }}>{typeof v === "object" ? <code className="small">{JSON.stringify(v)}</code> : String(v)}</td></tr>
        ))}</tbody>
      </table>
    );
  }
  return <code className="small">{JSON.stringify(value)}</code>;
}

export default function FlowApproval() {
  const { token } = useParams();
  const { data, error, setData } = useLoad(() => api.get<Approval>(`/api/flows/approvals/${token}`), [token]);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const decide = async (approve: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      setData(await api.post<Approval>(`/api/flows/approvals/${token}`, { approve, comment }));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page" style={{ maxWidth: 560 }}>
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <span className="eyebrow">Godkendelse</span>
        {error && <Alert>{error}</Alert>}
        {!data && !error && <Loading />}
        {data && (
          <>
            <h1 style={{ margin: 0, fontSize: 24 }}>{data.title}</h1>
            <p className="muted small" style={{ margin: 0 }}>Fra flowet {data.flow} · {STATUS[data.status] ?? data.status}
              {data.status === "pending" && <> · svar senest {fmtDate(data.expiresAt)}</>}{data.decidedAt && <> · {fmtDate(data.decidedAt)}</>}</p>
            {data.message && <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{data.message}</p>}
            <Data value={data.data} />
            {data.status === "pending" && data.mine && (
              <>
                <label className="field">Kommentar (valgfri)
                  <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3} maxLength={2000} />
                </label>
                {err && <Alert>{err}</Alert>}
                <div className="row" style={{ gap: 8 }}>
                  <button type="button" className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>Godkend</button>
                  <button type="button" className="btn btn-outline" disabled={busy} onClick={() => decide(false)}>Afvis</button>
                </div>
              </>
            )}
            {data.status !== "pending" && (
              <Alert kind={data.status === "approved" ? "success" : "info"}>
                {data.decidedByMe ? "Du har svaret." : "Der er svaret på denne godkendelse."}{data.comment && <> Kommentar: „{data.comment}“</>} Flowet fortsætter af sig selv.
              </Alert>
            )}
          </>
        )}
      </div>
    </div>
  );
}
