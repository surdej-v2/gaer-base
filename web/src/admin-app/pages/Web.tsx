import { useEffect, useState } from "react";
import { api, errorText } from "../../api";
import { Alert, fmtDate, Loading, PageHeader, useLoad } from "../ui";

type Page = { id: number; slug: string; lang: "da" | "en"; title: string; description: string; body: string; nav_order: number | null; published: boolean; updated_at: string; og_image_key: string | null };
type Draft = Omit<Page, "id" | "updated_at" | "og_image_key"> & { id?: number; og_image_key?: string | null };
type OgSurface = { name: string; label: string; custom: boolean; url: string };

type Front = "markdown" | "facit246";

const EMPTY: Draft = { slug: "", lang: "da", title: "", description: "", body: "", nav_order: null, published: false };

/** Upload a PNG, JPEG or WebP (1200 × 630) to `path`; returns when stored. */
async function uploadImage(path: string, file: File): Promise<void> {
  const body = new FormData();
  body.append("file", file);
  await api.post(path, body);
}

/** og:image for the pages anonymous visitors see: the generated images, and an admin's own per surface. */
function SharingImages({ onError, onNotice }: { onError: (e: string | null) => void; onNotice: (n: string) => void }) {
  const og = useLoad(() => api.get<{ surfaces: OgSurface[] }>("/api/admin/web/og"));
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (name: string, work: () => Promise<unknown>, notice: string) => {
    onError(null);
    setBusy(name);
    try {
      await work();
      await og.reload();
      onNotice(notice);
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="card" data-deeplink="web-og">
      <span className="eyebrow">Delingsbilleder (og:image)</span>
      <p className="muted small">
        Billedet, der vises, når en offentlig side deles på LinkedIn, Facebook, Slack, Teams m.fl. Der er genereret et billede
        til hver side ud fra logo og navn. Upload dit eget for at erstatte det — PNG, JPEG eller WebP i 1200 × 630 pixels.
        Et eget standardbillede bruges på alle offentlige sider, der ikke har deres eget. Markdown-sider kan også få deres eget nedenfor.
      </p>
      {!og.data ? <Loading /> : (
        <div className="grid" style={{ "--min": "220px" } as React.CSSProperties}>
          {og.data.surfaces.map((s) => (
            <div key={s.name} className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
              <img src={s.url} alt={`Delingsbillede: ${s.label}`} style={{ width: "100%", aspectRatio: "1200 / 630", objectFit: "cover", borderRadius: 8, border: "1px solid var(--hm-border)", background: "var(--hm-muted)" }} />
              <strong className="small">{s.label}</strong>
              <span className="tiny muted">{s.custom ? "Eget billede" : "Genereret"}</span>
              <div className="row" style={{ "--gap": "6px" } as React.CSSProperties}>
                <label className="btn btn-outline btn-sm" style={{ cursor: "pointer" }}>
                  {busy === s.name ? "Uploader …" : "Upload eget"}
                  <input type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={busy !== null} onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) run(s.name, () => uploadImage(`/api/admin/web/og/${s.name}`, file), "Delingsbilledet er gemt");
                  }} />
                </label>
                {s.custom && <button className="btn btn-ghost btn-sm" disabled={busy !== null} onClick={() => {
                  if (confirm("Gå tilbage til det genererede billede? Dit eget billede slettes.")) run(s.name, () => api.del(`/api/admin/web/og/${s.name}`), "Det genererede billede bruges igen");
                }}>Brug genereret</button>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Web() {
  const pages = useLoad(() => api.get<Page[]>("/api/admin/web"));
  const front = useLoad(() => api.get<{ front: Front }>("/api/admin/web/front"));
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
      const { id } = draft;
      const body: Partial<Draft> = { ...draft };
      delete body.id;
      delete body.og_image_key;
      if (id) await api.put(`/api/admin/web/${id}`, body);
      else setDraft({ ...draft, id: (await api.post<Page>("/api/admin/web", body)).id, og_image_key: null });
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
      <div className="card">
        <span className="eyebrow">Forside /w/</span>
        <label className="field">
          Vis som forside
          <select value={front.data?.front ?? "markdown"} disabled={!front.data} onChange={async (e) => {
            setError(null);
            try {
              await api.put("/api/admin/web/front", { front: e.target.value as Front });
              await front.reload();
              setNotice("Forsiden er skiftet");
            } catch (err) {
              setError(errorText(err));
            }
          }}>
            <option value="markdown">Markdown-siden med tom adresse (redigeres her)</option>
            <option value="facit246">„Facit · 246“ — fast fortælling på dansk, engelsk, tysk, svensk og norsk</option>
          </select>
        </label>
        <p className="muted small">Begge slutter med login-kortet. Undersiderne (/w/…) er Markdown-siderne nedenfor. <a href="/w/" target="_blank" rel="noreferrer">Se forsiden</a></p>
      </div>
      <SharingImages onError={setError} onNotice={setNotice} />
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
              <label className="field">Adresse (/w/…)<input value={draft.slug} placeholder={front.data?.front === "facit246" ? "fx priser — forsiden /w/ er 246-fortællingen" : "tom = forside"} onChange={(e) => setDraft({ ...draft, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} /></label>
              <label className="field">Sprog
                <select value={draft.lang} onChange={(e) => setDraft({ ...draft, lang: e.target.value as "da" | "en" })}><option value="da">Dansk</option><option value="en">Engelsk</option></select>
              </label>
              <label className="field">Placering i menu<input type="number" value={draft.nav_order ?? ""} placeholder="ikke i menu" onChange={(e) => setDraft({ ...draft, nav_order: e.target.value === "" ? null : Number(e.target.value) })} /></label>
            </div>
            <label className="field">Titel<input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} required /></label>
            <label className="field">Beskrivelse (til søgemaskiner)<input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
            <label className="field">Indhold (Markdown)<textarea value={draft.body} style={{ minHeight: 260, fontFamily: "var(--hm-font-mono)", fontSize: 13 }} onChange={(e) => setDraft({ ...draft, body: e.target.value })} /></label>
            {draft.id && (
              <div className="field">
                <span>Delingsbillede (og:image)</span>
                <div className="row" style={{ "--gap": "8px" } as React.CSSProperties}>
                  <span className="small muted" style={{ flex: 1 }}>{draft.og_image_key ? "Siden har sit eget billede." : `Bruger ${draft.slug ? "standardbilledet" : "forsidens billede"}.`}</span>
                  <label className="btn btn-outline btn-sm" style={{ cursor: "pointer" }}>
                    Upload
                    <input type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async (e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      if (!file || !draft.id) return;
                      setError(null);
                      try {
                        await uploadImage(`/api/admin/web/${draft.id}/og`, file);
                        const list = await api.get<Page[]>("/api/admin/web");
                        pages.setData(list);
                        setDraft({ ...draft, og_image_key: list.find((p) => p.id === draft.id)?.og_image_key ?? null });
                        setNotice("Sidens delingsbillede er gemt");
                      } catch (err) {
                        setError(errorText(err));
                      }
                    }} />
                  </label>
                  {draft.og_image_key && <button className="btn btn-ghost btn-sm" onClick={async () => {
                    if (!draft.id) return;
                    try {
                      await api.del(`/api/admin/web/${draft.id}/og`);
                      setDraft({ ...draft, og_image_key: null });
                      await pages.reload();
                    } catch (err) {
                      setError(errorText(err));
                    }
                  }}>Fjern</button>}
                </div>
              </div>
            )}
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
