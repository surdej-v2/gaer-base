import { useState } from "react";
import { api, errorText } from "../../api";
import { Alert, download, fmtDate, fmtDay, Loading, PageHeader, useLoad } from "../../ui";

type Request = {
  id: number; user_id: number | null; user_phone: string | null; user_name: string | null;
  kind: "access" | "rectification" | "erasure"; contact: string; message: string;
  status: "received" | "in_progress" | "completed" | "rejected"; received_at: string; due_at: string;
  completed_at: string | null; notes: string; overdue: boolean;
};

const KIND = { access: "Indsigt", rectification: "Rettelse", erasure: "Sletning" };
const STATUS = { received: "Modtaget", in_progress: "Under behandling", completed: "Afsluttet", rejected: "Afvist" };
const STATUS_PILL = { received: "blue", in_progress: "amber", completed: "green", rejected: "" };

function RequestCard({ r, onChange }: { r: Request; onChange: () => Promise<void> }) {
  const [notes, setNotes] = useState(r.notes);
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);
  const open = r.status === "received" || r.status === "in_progress";

  const act = async (fn: () => Promise<unknown>, confirmText?: string) => {
    if (confirmText && !confirm(confirmText)) return;
    setError(null);
    try {
      await fn();
      await onChange();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const patch = (body: object) => act(() => api.patch(`/api/admin/gdpr/${r.id}`, body));

  return (
    <div className={open && r.overdue ? "glow" : ""}>
      <div className="card">
        <div className="spread">
          <div className="row">
            <strong>#{r.id} · {KIND[r.kind]}</strong>
            <span className={`pill ${STATUS_PILL[r.status]}`}>{STATUS[r.status]}</span>
            {open && r.overdue && <span className="pill red">Over fristen</span>}
          </div>
          <span className="muted small">Modtaget {fmtDate(r.received_at)} · Frist {fmtDay(r.due_at)}</span>
        </div>
        <div className="small">
          {r.user_id ? <>Bruger <strong>{r.user_name || r.user_phone}</strong> (#{r.user_id})</> : <>Kontakt: <strong>{r.contact}</strong> · ikke knyttet til en bruger</>}
        </div>
        {r.message && <p className="small muted" style={{ whiteSpace: "pre-wrap" }}>{r.message}</p>}
        <label className="field">
          Interne noter
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} onBlur={() => notes !== r.notes && patch({ notes })} />
        </label>
        {!r.user_id && open && (
          <div className="row">
            <input placeholder="Telefonnummer på bruger" value={phone} onChange={(e) => setPhone(e.target.value)} />
            <button className="btn btn-outline btn-sm" disabled={!phone} onClick={() => patch({ user_phone: phone })}>Knyt til bruger</button>
          </div>
        )}
        <Alert>{error}</Alert>
        <div className="row">
          {r.kind === "access" && r.user_id && (
            <button className="btn btn-primary btn-sm" onClick={() => { download(`/api/admin/gdpr/${r.id}/export`); setTimeout(onChange, 1500); }}>
              Hent samlet eksport (.zip)
            </button>
          )}
          {r.kind === "erasure" && r.user_id && open && (
            <button className="btn btn-danger btn-sm" onClick={() => act(() => api.post(`/api/admin/gdpr/${r.id}/erase`),
              "Slet brugeren, alle filer og al logdata om brugeren? Det kan ikke fortrydes. Sletningen bliver logget.")}>
              Slet brugerens data
            </button>
          )}
          {r.status === "received" && <button className="btn btn-outline btn-sm" onClick={() => patch({ status: "in_progress" })}>Påbegynd</button>}
          {open && <button className="btn btn-outline btn-sm" onClick={() => patch({ status: "completed" })}>Markér som afsluttet</button>}
          {open && <button className="btn btn-ghost btn-sm" onClick={() => patch({ status: "rejected" })}>Afvis</button>}
          {!open && <button className="btn btn-ghost btn-sm" onClick={() => patch({ status: "in_progress" })}>Genåbn</button>}
        </div>
      </div>
    </div>
  );
}

export default function Gdpr() {
  const [status, setStatus] = useState("");
  const list = useLoad(() => api.get<Request[]>(`/api/admin/gdpr${status ? `?status=${status}` : ""}`), [status]);

  return (
    <>
      <div className="spread">
        <PageHeader eyebrow="/admin/gdpr" title="GDPR-forespørgsler">
          Anmodninger om indsigt, rettelse og sletning. Fristen er 30 dage. Indsigt udleveres som én samlet JSON-eksport,
          og sletning fjerner brugeren, filerne og logdata om brugeren. Alt bliver logget.
        </PageHeader>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">Alle</option>
          {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      <Alert>{list.error}</Alert>
      {list.loading && !list.data ? <Loading /> : list.data?.length === 0 ? <p className="muted">Ingen forespørgsler.</p> : (
        <div className="stack" style={{ "--gap": "16px" } as React.CSSProperties}>
          {list.data?.map((r) => <RequestCard key={`${r.id}-${r.status}-${r.user_id}`} r={r} onChange={list.reload} />)}
        </div>
      )}
    </>
  );
}
