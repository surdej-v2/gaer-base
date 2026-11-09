import { useRef, useState } from "react";
import { api, errorText } from "../api";
import { Alert, download, fmtBytes, fmtDate, Loading, PageHeader, useLoad } from "../ui";
import { Icon } from "../shell";

type FileRow = { id: string; filename: string; content_type: string; size_bytes: number; created_at: string };

export default function Files() {
  const files = useLoad(() => api.get<FileRow[]>("/api/files"));
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true);
    setError(null);
    try {
      for (const file of Array.from(list)) {
        const form = new FormData();
        form.append("file", file);
        await api.post("/api/files", form);
      }
      await files.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  const open = async (id: string) => {
    try {
      const { url } = await api.get<{ url: string }>(`/api/files/${id}/link`);
      download(url);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const rows = files.data ?? [];
  const chosen = rows.filter((f) => selected.has(f.id));
  const all = rows.length > 0 && chosen.length === rows.length;
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const removeMany = async (list: FileRow[]) => {
    if (!confirm(`Slet ${list.length} ${list.length === 1 ? "fil" : "filer"}? Det kan ikke fortrydes.`)) return;
    try {
      for (const f of list) await api.del(`/api/files/${f.id}`);
      setSelected(new Set());
      await files.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const remove = async (f: FileRow) => {
    if (!confirm(`Slet ${f.filename}?`)) return;
    try {
      await api.del(`/api/files/${f.id}`);
      await files.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div className="page">
      <div className="spread">
        <PageHeader eyebrow="Object Storage" title="Mine filer">
          Filer gemmes i Object Storage. Download sker via et link, der kun virker i få minutter.
        </PageHeader>
        <div>
          <input ref={input} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
          <button className="btn btn-primary" disabled={busy} onClick={() => input.current?.click()}>
            <Icon name="upload" size={16} />{busy ? "Uploader …" : "Upload filer"}
          </button>
        </div>
      </div>
      <Alert>{error}</Alert>
      {files.loading && !files.data ? <Loading /> : (
        <section className="data-card" aria-label="Filer">
          <div className={`data-bar ${chosen.length ? "selected" : ""}`}>
            {chosen.length ? (
              <>
                <strong className="small" style={{ color: "#1e3a8a" }}>{chosen.length} valgt</strong>
                <button className="btn btn-outline btn-sm" onClick={() => chosen.forEach((f) => open(f.id))}><Icon name="download" size={15} />Hent</button>
                <button className="btn btn-danger btn-sm" onClick={() => removeMany(chosen)}><Icon name="trash" size={15} />Slet</button>
                <button className="btn btn-ghost btn-sm end" onClick={() => setSelected(new Set())}>Fjern markering</button>
              </>
            ) : (
              <span className="small muted">{rows.length ? `${rows.length} ${rows.length === 1 ? "fil" : "filer"} · markér filer for at hente eller slette flere på én gang` : " "}</span>
            )}
          </div>
          {rows.length === 0 ? <p className="muted small" style={{ padding: 16 }}>Ingen filer endnu.</p> : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr>
                  <th className="tools"><input type="checkbox" aria-label="Vælg alle" checked={all} onChange={() => setSelected(all ? new Set() : new Set(rows.map((f) => f.id)))} /></th>
                  <th>Fil</th><th>Størrelse</th><th className="hide-sm">Uploadet</th>
                </tr></thead>
                <tbody>
                  {rows.map((f) => (
                    <tr key={f.id} data-deeplink={`file-row-${f.id}`} className={selected.has(f.id) ? "selected" : ""}>
                      <td className="tools"><span>
                        <input type="checkbox" aria-label={`Vælg ${f.filename}`} checked={selected.has(f.id)} onChange={() => toggle(f.id)} />
                        <button className="icon-btn" aria-label={`Hent ${f.filename}`} title="Hent" onClick={() => open(f.id)}><Icon name="download" size={16} /></button>
                        <button className="icon-btn danger hide-sm" aria-label={`Slet ${f.filename}`} title="Slet" onClick={() => remove(f)}><Icon name="trash" size={16} /></button>
                      </span></td>
                      <td className="wrap"><span className="title">{f.filename}</span><div className="muted tiny">{f.content_type}</div></td>
                      <td className="num" style={{ whiteSpace: "nowrap" }}>{fmtBytes(f.size_bytes)}</td>
                      <td className="muted hide-sm" style={{ whiteSpace: "nowrap" }}>{fmtDate(f.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
