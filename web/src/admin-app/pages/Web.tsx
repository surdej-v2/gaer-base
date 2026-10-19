import { useEffect, useState } from "react";
import { api, errorText } from "../../api";
import { Alert, fmtDate, Loading, PageHeader, useLoad } from "../../ui";

type Page = { id: number; slug: string; lang: "da" | "en"; title: string; description: string; body: string; nav_order: number | null; published: boolean; updated_at: string };
type Draft = Omit<Page, "id" | "updated_at"> & { id?: number };

const EMPTY: Draft = { slug: "", lang: "da", title: "", description: "", body: "", nav_order: null, published: false };

export default function Web() {
  const pages = useLoad(() => api.get<Page[]>("/api/admin/web"));
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!draft) return;
    const t = setTimeout(() => api.post<{ html: string }>("/api/admin/web/preview", { body: draft.body }).then((r) => setPreview(r.html), () => undefined), 300);
    return () => clearTimeout(t);
  }, [draft?.body]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!draft) return;
    setError(null);
    try {
      const { id, ...body } = draft;
      if (id) await api.put(`/api/admin/web/${id}`, body);
      else setDraft({ ...draft, id: (await api.post<Page>("/api/admin/web", body)).id });
      setNotice("Siden er gemt");
      await pages.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const url = (p: { slug: string; lang: string }) => `/w/${p.slug}?lang=${p.lang}`;

  return (
    <>
      <div className="spread">
        <PageHeader eyebrow="/admin/web" title="Website">
          Det offentlige website under /w. Sider skrives i Markdown og vises af Python-serveren, på dansk og engelsk.
          Besøgende, der ikke er logget ind, sendes fra / til /w/.
        </PageHeader>
        <button className="btn btn-primary" onClick={() => { setDraft({ ...EMPTY }); setNotice(null); }}>Ny side</button>
      </div>
      <Alert kind="success">{notice}</Alert>
      <Alert>{error || pages.error}</Alert>
      {!pages.data ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Adresse</th><th>Sprog</th><th>Titel</th><th>Menu</th><th>Status</th><th>Opdateret</th></tr></thead>
            <tbody>
              {pages.data.map((p) => (
                <tr key={p.id} className="clickable" onClick={() => { setDraft({ ...p }); setNotice(null); }}>
                  <td className="mono small">/w/{p.slug}</td>
                  <td>{p.lang}</td>
                  <td><strong>{p.title}</strong></td>
                  <td className="num">{p.nav_order ?? "—"}</td>
                  <td><span className={`pill ${p.published ? "green" : ""}`}>{p.published ? "Udgivet" : "Kladde"}</span></td>
                  <td>{fmtDate(p.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {draft && (
        <div className="grid" style={{ "--min": "340px", alignItems: "start" } as React.CSSProperties}>
          <div className="card">
            <span className="eyebrow">{draft.id ? "Redigér side" : "Ny side"}</span>
            <div className="filters">
              <label className="field">Adresse (/w/…)<input value={draft.slug} placeholder="tom = forside" onChange={(e) => setDraft({ ...draft, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} /></label>
              <label className="field">Sprog
                <select value={draft.lang} onChange={(e) => setDraft({ ...draft, lang: e.target.value as "da" | "en" })}><option value="da">Dansk</option><option value="en">Engelsk</option></select>
              </label>
              <label className="field">Placering i menu<input type="number" value={draft.nav_order ?? ""} placeholder="ikke i menu" onChange={(e) => setDraft({ ...draft, nav_order: e.target.value === "" ? null : Number(e.target.value) })} /></label>
            </div>
            <label className="field">Titel<input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} required /></label>
            <label className="field">Beskrivelse (til søgemaskiner)<input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
            <label className="field">Indhold (Markdown)<textarea value={draft.body} style={{ minHeight: 260, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} onChange={(e) => setDraft({ ...draft, body: e.target.value })} /></label>
            <label className="row small"><input type="checkbox" checked={draft.published} onChange={(e) => setDraft({ ...draft, published: e.target.checked })} /> Udgivet</label>
            <div className="row">
              <button className="btn btn-primary" onClick={save}>Gem</button>
              {draft.id && draft.published && <a className="btn btn-outline" href={url(draft)} target="_blank" rel="noreferrer" style={{ color: "inherit", textDecoration: "none" }}>Åbn</a>}
              {draft.id && <button className="btn btn-danger" onClick={async () => {
                if (!confirm(`Slet siden /w/${draft.slug} (${draft.lang})?`)) return;
                await api.del(`/api/admin/web/${draft.id}`);
                setDraft(null);
                await pages.reload();
              }}>Slet</button>}
            </div>
          </div>
          <div className="card">
            <span className="eyebrow">Forhåndsvisning</span>
            <div className="web-preview" dangerouslySetInnerHTML={{ __html: preview }} />
          </div>
        </div>
      )}
    </>
  );
}
