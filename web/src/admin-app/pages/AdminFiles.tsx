import { useState } from "react";
import { api, errorText } from "../../api";
import { Alert, download, fmtBytes, fmtDate, fmtNumber, Loading, PageHeader, useLoad } from "../../ui";

type Data = {
  totals: { files: number; bytes: number };
  per_user: { user_id: number | null; phone: string | null; display_name: string | null; files: number; bytes: number }[];
  items: { id: string; user_id: number | null; phone: string | null; filename: string; content_type: string; size_bytes: number; created_at: string }[];
};

export default function AdminFiles() {
  const [userId, setUserId] = useState<number | null>(null);
  const data = useLoad(() => api.get<Data>(`/api/admin/files${userId ? `?user_id=${userId}` : ""}`), [userId]);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <PageHeader eyebrow="/admin/files" title="Filer">Overblik over filer i Object Storage pr. bruger og forbrug i alt.</PageHeader>
      <Alert>{error || data.error}</Alert>
      {!data.data ? <Loading /> : (
        <>
          <div className="grid" style={{ "--min": "200px" } as React.CSSProperties}>
            <div className="card"><span className="eyebrow">Filer</span><span className="stat">{fmtNumber(data.data.totals.files)}</span></div>
            <div className="card"><span className="eyebrow">Forbrug i alt</span><span className="stat">{fmtBytes(data.data.totals.bytes)}</span></div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Bruger</th><th>Filer</th><th>Forbrug</th></tr></thead>
              <tbody>
                {data.data.per_user.map((u) => (
                  <tr key={u.user_id ?? "none"} className="clickable" onClick={() => setUserId(u.user_id)}>
                    <td>{u.user_id ? (u.display_name || u.phone) : <span className="muted">System (fx logo)</span>}</td>
                    <td className="num">{fmtNumber(u.files)}</td>
                    <td className="num">{fmtBytes(u.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="spread">
            <span className="eyebrow">{userId ? `Filer for bruger #${userId}` : "Seneste filer"}</span>
            {userId && <button className="btn btn-ghost btn-sm" onClick={() => setUserId(null)}>Vis alle</button>}
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Fil</th><th>Bruger</th><th>Størrelse</th><th>Uploadet</th><th /></tr></thead>
              <tbody>
                {data.data.items.map((f) => (
                  <tr key={f.id}>
                    <td className="wrap"><strong>{f.filename}</strong><div className="muted tiny">{f.content_type}</div></td>
                    <td>{f.phone ?? "—"}</td>
                    <td className="num">{fmtBytes(f.size_bytes)}</td>
                    <td>{fmtDate(f.created_at)}</td>
                    <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                      <button className="btn btn-outline btn-sm" onClick={() => act(async () => download((await api.get<{ url: string }>(`/api/files/${f.id}/link`)).url))}>Hent</button>{" "}
                      <button className="btn btn-danger btn-sm" onClick={() => confirm(`Slet ${f.filename}?`) && act(async () => { await api.del(`/api/admin/files/${f.id}`); await data.reload(); })}>Slet</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
