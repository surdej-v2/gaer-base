// /admin/knowledge/spaces/:id → Vidensrum: upload (filer eller ZIP), dokumenter med status, udtrukket tekst og log.
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Bot, Download, Eye, FileText, Pencil, RotateCcw, Trash2, Upload } from "lucide-react";
import { api, errorText } from "../../../api";
import { Btn, CloseBtn, Drawer, Empty, ErrorBox, IconBtn, Modal, PageTitle, Pill, fmtBytes, fmtDate, useLoad, useToast } from "../../ui";
import { card, fmt, input, label, MONO, sectionLabel } from "../../theme";
import { srOnly, STATUS, version, type Doc, type LogEntry, type Space as SpaceData } from "./types";

const ACCEPT = ".zip,.pdf,.docx,.pptx,.xlsx,.csv,.html,.htm,.md,.markdown,.txt";

export default function Space() {
  const { id } = useParams();
  const { data, error, reload } = useLoad(() => api.get<SpaceData>(`/api/admin/knowledge/spaces/${id}`), [id]);
  const log = useLoad(() => api.get<LogEntry[]>(`/api/admin/knowledge/spaces/${id}/log?limit=60`), [id]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [viewing, setViewing] = useState<Doc | null>(null);
  const [editing, setEditing] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const navigate = useNavigate();

  const busy = data?.documents.some((d) => d.status === "pending" || d.status === "processing") ?? false;
  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(() => { reload(); log.reload(); }, 3000);
    return () => window.clearInterval(t);
  }, [busy, reload, log.reload]);

  const upload = async (files: FileList | File[]) => {
    if (!files.length) return;
    const form = new FormData();
    for (const f of Array.from(files)) form.append("files", f);
    setUploading(true);
    try {
      const r = await api.post<{ added: unknown[]; skipped: string[] }>(`/api/admin/knowledge/spaces/${id}/upload`, form);
      toast(`${r.added.length} filer lagt i kø${r.skipped.length ? ` · ${r.skipped.length} sprunget over (ukendt format)` : ""}`);
      reload(); log.reload();
    } catch (e) {
      toast(errorText(e));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const act = async (fn: () => Promise<unknown>, message: string) => {
    try { await fn(); toast(message); reload(); log.reload(); } catch (e) { toast(errorText(e)); }
  };

  if (error) return <ErrorBox>{error}</ErrorBox>;
  if (!data) return null;
  const errors = data.documents.filter((d) => d.status === "error").length;
  const deleted = !!data.deleted_at;

  return (
    <>
      <Link to="/knowledge" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "#1d4ed8", textDecoration: "none", fontWeight: 500 }}>
        <ArrowLeft size={14} /> Alle vidensrum
      </Link>
      <PageTitle title={data.name} aside={!deleted && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Btn icon={Pencil} onClick={() => setEditing(true)}>Redigér</Btn>
          <Btn icon={Trash2} style={{ color: "#b91c1c", borderColor: "#fecaca" }} onClick={() => {
            if (!confirm(`Flyt „${data.name}“ til papirkurven? Agenterne kan ikke længere bruge det. Du kan gendanne det i 30 dage.`)) return;
            act(() => api.del(`/api/admin/knowledge/spaces/${id}`), "Flyttet til papirkurven").then(() => navigate("/knowledge"));
          }}>Flyt til papirkurv</Btn>
        </div>
      )}>
        {data.description || "Ingen beskrivelse."}
      </PageTitle>
      {deleted && <ErrorBox>Vidensrummet ligger i papirkurven. Gendan det fra oversigten for at bruge det igen.</ErrorBox>}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "flex-start" }}>
        <section style={{ flex: "999 1 560px", minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          {!deleted && (
            <div onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
              onDrop={(e) => { e.preventDefault(); setDragOver(false); upload(e.dataTransfer.files); }}
              style={{ ...card, borderStyle: "dashed", borderWidth: 2, borderColor: dragOver ? "#2563eb" : "#cbd5e1", background: dragOver ? "#eff6ff" : "#f8fafc",
                padding: 20, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14 }}>
              <Upload size={22} color="#2563eb" />
              <span style={{ flex: "1 1 260px", display: "flex", flexDirection: "column", gap: 2 }}>
                <b style={{ fontWeight: 600 }}>Træk filer eller en ZIP hertil</b>
                <span style={{ fontSize: 12, color: "#475569" }}>PDF, Word, PowerPoint, Excel, CSV, HTML, Markdown og tekst. En ZIP pakkes ud på serveren, og hver fil indekseres for sig.</span>
              </span>
              <input ref={fileInput} type="file" multiple accept={ACCEPT} style={{ display: "none" }} onChange={(e) => e.target.files && upload(e.target.files)} />
              <Btn kind="primary" icon={Upload} disabled={uploading} onClick={() => fileInput.current?.click()}>{uploading ? "Uploader…" : "Vælg filer"}</Btn>
            </div>
          )}

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ ...sectionLabel, flex: 1 }}>Dokumenter · {fmt(data.documents.length)}</span>
            {errors > 0 && !deleted && <Btn icon={RotateCcw} onClick={() => act(() => api.post(`/api/admin/knowledge/spaces/${id}/retry`), "Lagt i kø igen")}>
              Prøv {errors} fejlede igen</Btn>}
          </div>
          <div style={{ ...card, overflowX: "auto" }}>
            {data.documents.length === 0 ? <Empty>Ingen dokumenter endnu.</Empty> : (
              <table style={{ width: "100%", minWidth: 640, borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "#475569", fontSize: 11, letterSpacing: "0.05em", textTransform: "uppercase" }}>
                    <th style={{ padding: "10px 16px", fontWeight: 600 }}>Fil</th><th style={{ padding: "10px 16px", fontWeight: 600 }}>Status</th>
                    <th style={{ padding: "10px 16px", fontWeight: 600 }}>Indhold</th><th style={{ padding: "10px 16px" }}><span style={srOnly}>Handlinger</span></th>
                  </tr>
                </thead>
                <tbody>
                  {data.documents.map((d) => {
                    const [statusLabel, tone] = STATUS[d.status];
                    return (
                      <tr key={d.id} style={{ borderTop: "1px solid #f1f5f9", background: d.status === "failed" || d.status === "error" ? "#fef2f2" : undefined }}>
                        <td style={{ padding: "10px 16px" }}>
                          <span style={{ display: "flex", alignItems: "center", gap: 8 }}><FileText size={14} color="#475569" />
                            <span style={{ fontFamily: MONO, fontSize: 12 }}>{d.path}</span></span>
                          {d.error && <span style={{ display: "block", fontSize: 12, color: "#991b1b", marginTop: 2 }}>{d.error}</span>}
                        </td>
                        <td style={{ padding: "10px 16px" }}><Pill tone={tone}>{statusLabel}</Pill></td>
                        <td style={{ padding: "10px 16px", color: "#475569", whiteSpace: "nowrap" }}>
                          {fmtBytes(d.size_bytes)}{d.page_count ? ` · ${d.page_count} sider` : ""}{d.chunk_count ? ` · ${d.chunk_count} afsnit` : ""}{d.embedded ? " · vektorer" : ""}
                        </td>
                        <td style={{ padding: "6px 12px" }}><span style={{ display: "flex", justifyContent: "flex-end", gap: 2 }}>
                          <IconBtn icon={Eye} label="Vis udtrukket tekst" onClick={() => setViewing(d)} />
                          <IconBtn icon={Download} label="Hent original" onClick={async () => {
                            try { window.open((await api.get<{ url: string }>(`/api/admin/knowledge/documents/${d.id}/link`)).url, "_blank"); }
                            catch (e) { toast(errorText(e)); }
                          }} />
                          {(d.status === "error" || d.status === "failed") && !deleted &&
                            <IconBtn icon={RotateCcw} label="Prøv igen" onClick={() => act(() => api.post(`/api/admin/knowledge/documents/${d.id}/retry`), "Lagt i kø igen")} />}
                          {!deleted && <IconBtn icon={Trash2} label="Slet dokument" color="#b91c1c" onClick={() => {
                            if (confirm(`Slet ${d.filename}? Filen og dens afsnit fjernes fra indekset.`)) act(() => api.del(`/api/admin/knowledge/documents/${d.id}`), "Dokumentet er slettet");
                          }} />}
                        </span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </section>

        <aside style={{ flex: "1 1 280px", minWidth: 0, display: "flex", flexDirection: "column", gap: 14 }}>
          <section style={{ ...card, padding: 16, display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={sectionLabel}>Agenter på rummet</span>
            {data.agents.length === 0 ? <span style={{ fontSize: 13, color: "#475569" }}>Ingen endnu.</span> : data.agents.map((a) => (
              <Link key={a.id} to={`/knowledge/agents/${a.id}`} style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 36, color: "#020817", fontSize: 14 }}>
                <Bot size={15} color="#2563eb" /><span style={{ flex: 1 }}>{a.name}</span>
                {a.status === "published" ? <Pill tone="green">{version(a)}</Pill> : <Pill tone="gray">Kladde</Pill>}
              </Link>
            ))}
          </section>
          <section style={{ ...card, padding: 16, display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={sectionLabel}>Log</span>
            {(log.data ?? []).length === 0 ? <span style={{ fontSize: 13, color: "#475569" }}>Ingen hændelser endnu.</span> : (
              <div style={{ fontFamily: MONO, fontSize: 11, lineHeight: "18px", color: "#334155", display: "flex", flexDirection: "column", maxHeight: 420, overflow: "auto" }}>
                {log.data!.map((e) => (
                  <span key={e.id}><span style={{ color: "#64748b" }}>{new Date(e.created_at).toLocaleTimeString("da-DK")}</span>{" "}
                    <b style={{ color: e.level === "error" ? "#b91c1c" : e.level === "warn" ? "#9a3412" : e.stage === "done" ? "#166534" : "#1d4ed8" }}>{e.stage}</b> {e.message}</span>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>
      {viewing && <DocumentDrawer doc={viewing} onClose={() => setViewing(null)} />}
      {editing && <EditSpace space={data} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />}
    </>
  );
}

type DocDetail = Doc & { markdown: string; truncated: boolean; chunks: { chunk_index: number; section: string; page: number | null; tokens: number; embedded: boolean; preview: string }[] };

function DocumentDrawer({ doc, onClose }: { doc: Doc; onClose: () => void }) {
  const { data, error } = useLoad(() => api.get<DocDetail>(`/api/admin/knowledge/documents/${doc.id}`), [doc.id]);
  const [tab, setTab] = useState<"text" | "chunks">("text");
  return (
    <Drawer width={760} onClose={onClose} label={doc.filename}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "16px 20px", borderBottom: "1px solid #e2e8f0" }}>
        <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <b style={{ fontSize: 16 }}>{doc.filename}</b>
          <span style={{ fontSize: 12, color: "#475569" }}>{doc.path} · indekseret {fmtDate(doc.indexed_at)}</span>
        </span>
        <CloseBtn onClick={onClose} />
      </div>
      <div role="tablist" style={{ display: "flex", gap: 4, padding: "0 20px", borderBottom: "1px solid #e2e8f0" }}>
        {(["text", "chunks"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} style={{ all: "unset", cursor: "pointer", padding: "12px 10px",
            fontSize: 13, fontWeight: tab === t ? 600 : 400, color: tab === t ? "#1d4ed8" : "#334155", borderBottom: `2px solid ${tab === t ? "#2563eb" : "transparent"}` }}>
            {t === "text" ? "Udtrukket tekst" : `Afsnit · ${doc.chunk_count}`}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: 20 }}>
        {error && <ErrorBox>{error}</ErrorBox>}
        {data && tab === "text" && (data.markdown
          ? <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: MONO, fontSize: 12, lineHeight: "19px", color: "#1e293b" }}>{data.markdown}{data.truncated ? "\n\n…" : ""}</pre>
          : <Empty>Ingen udtrukket tekst for dette dokument.</Empty>)}
        {data && tab === "chunks" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {data.chunks.map((c) => (
              <div key={c.chunk_index} style={{ ...card, padding: 12, display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 12, color: "#475569" }}>#{c.chunk_index + 1}{c.section ? ` · ${c.section}` : ""}{c.page ? ` · side ${c.page}` : ""} · {c.tokens} tokens{c.embedded ? " · vektor" : ""}</span>
                <span style={{ fontSize: 13, color: "#1e293b" }}>{c.preview}{c.preview.length >= 400 ? " …" : ""}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Drawer>
  );
}

function EditSpace({ space, onClose, onSaved }: { space: SpaceData; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(space.name);
  const [description, setDescription] = useState(space.description);
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal onClose={onClose} width={480}>
      <h2 style={{ margin: 0, fontSize: 18 }}>Redigér vidensrum</h2>
      <label style={label}>Navn<input value={name} onChange={(e) => setName(e.target.value)} style={input} /></label>
      <label style={label}>Beskrivelse<textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} style={{ ...input, height: "auto", padding: 10 }} /></label>
      {error && <ErrorBox>{error}</ErrorBox>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn onClick={onClose}>Annullér</Btn>
        <Btn kind="primary" disabled={!name.trim()} onClick={async () => {
          try { await api.patch(`/api/admin/knowledge/spaces/${space.id}`, { name, description }); onSaved(); } catch (e) { setError(errorText(e)); }
        }}>Gem</Btn>
      </div>
    </Modal>
  );
}
