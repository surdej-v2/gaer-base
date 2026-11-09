import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, errorText } from "./api";
import { Alert, fmtDate, Loading, useLoad } from "./ui";
import { Icon, useShell } from "./shell";

export type Clip = { id: number; source: string; title: string; body: string; url: string | null; created_at: string };
export type NewClip = { title: string; body: string; url?: string | null; source: string };

const SOURCE: Record<string, string> = { manual: "Skrevet", extension: "Web", outlook: "Outlook", word: "Word", mobile: "Mobil", mcp: "AI (MCP)" };

export function saveClip(clip: NewClip): Promise<Clip> {
  return api.post<Clip>("/api/clips", { ...clip, title: clip.title.slice(0, 300) || "Uden titel" });
}

export function useClips(initialQ = "") {
  const [q, setQ] = useState(initialQ);
  const list = useLoad(() => api.get<Clip[]>(`/api/clips${q ? `?q=${encodeURIComponent(q)}` : ""}`), [q]);
  return { ...list, q, setQ };
}

/** A searchable list of the user's clips. `actions` renders buttons per clip (insert in Word, …). */
export function ClipsList({ clips, actions, compact = false, source = "manual", openId }: {
  clips: ReturnType<typeof useClips>;
  actions?: (clip: Clip) => ReactNode;
  compact?: boolean;
  source?: string;
  /** A clip opened by a deep link (/klip/:id): expanded, with the clip-body and clip-delete anchors. */
  openId?: number;
}) {
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [open, setOpen] = useState<number | null>(openId ?? null);
  useEffect(() => setOpen(openId ?? null), [openId]);
  const [error, setError] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await saveClip({ title, body, source });
      setTitle("");
      setBody("");
      setAdding(false);
      await clips.reload();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const remove = async (c: Clip) => {
    if (!confirm(`Slet klippet "${c.title}"?`)) return;
    try {
      await api.del(`/api/clips/${c.id}`);
      await clips.reload();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="stack">
      <div className="row">
        <input placeholder="Søg i klip" data-deeplink="clips-search" value={clips.q} onChange={(e) => clips.setQ(e.target.value)} style={{ flex: 1, minWidth: 0 }} />
        <button className="btn btn-outline btn-sm" onClick={() => setAdding(!adding)}>{adding ? "Luk" : "Nyt klip"}</button>
      </div>
      {adding && (
        <form className="card" onSubmit={add}>
          <label className="field">Titel<input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} /></label>
          <label className="field">Tekst<textarea value={body} onChange={(e) => setBody(e.target.value)} /></label>
          <div><button className="btn btn-primary btn-sm">Gem klip</button></div>
        </form>
      )}
      <Alert>{error || clips.error}</Alert>
      {clips.loading && !clips.data ? <Loading /> : clips.data?.length === 0 ? (
        <p className="muted small">Ingen klip endnu.</p>
      ) : (
        clips.data?.map((c) => (
          <div key={c.id} className="card" style={{ padding: compact ? "12px 14px" : undefined, gap: 6 }}>
            <div className="spread" style={{ alignItems: "flex-start" }}>
              <button onClick={() => setOpen(open === c.id ? null : c.id)} aria-expanded={open === c.id}
                      style={{ background: "none", border: 0, padding: 0, textAlign: "left", cursor: "pointer", font: "inherit", color: "inherit", flex: 1, minWidth: 0 }}>
                <strong style={{ wordBreak: "break-word" }}>{c.title}</strong>
                <div className="muted tiny">{SOURCE[c.source] ?? c.source} · {fmtDate(c.created_at)}</div>
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => remove(c)} aria-label="Slet"
                      data-deeplink={c.id === openId ? "clip-delete" : undefined}>Slet</button>
            </div>
            {(open === c.id || !compact) && c.body && (
              <p className="small" data-deeplink={c.id === openId ? "clip-body" : undefined} style={{ whiteSpace: "pre-wrap", maxHeight: open === c.id ? "none" : 72, overflow: "hidden", color: "#334155" }}>{c.body}</p>
            )}
            {c.url && open === c.id && <a className="tiny" href={c.url} target="_blank" rel="noreferrer" style={{ wordBreak: "break-all" }}>{c.url}</a>}
            {actions && <div className="row" style={{ "--gap": "6px" } as React.CSSProperties}>{actions(c)}</div>}
          </div>
        ))
      )}
    </div>
  );
}

/** /klip — the clips as a table: the row's tools on the left, a bulk bar when rows are selected. */
export default function ClipsPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const clips = useClips(params.get("q") ?? "");
  const { aiEnabled, openAi } = useShell();
  const openId = id ? Number(id) : undefined;
  const [open, setOpen] = useState<number | null>(openId ?? null);
  useEffect(() => setOpen(openId ?? null), [openId]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);

  const rows = clips.data ?? [];
  const chosen = rows.filter((c) => selected.has(c.id));
  const all = rows.length > 0 && chosen.length === rows.length;
  const toggle = (cid: number) => setSelected((s) => { const n = new Set(s); if (n.has(cid)) n.delete(cid); else n.add(cid); return n; });

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await saveClip({ title, body, source: "manual" });
      setTitle(""); setBody(""); setAdding(false);
      await clips.reload();
    } catch (err) {
      setError(errorText(err));
    }
  };
  const remove = async (list: Clip[]) => {
    if (!list.length) return;
    const what = list.length === 1 ? `klippet "${list[0].title}"` : `${list.length} klip`;
    if (!confirm(`Slet ${what}? Det kan ikke fortrydes.`)) return;
    try {
      for (const c of list) await api.del(`/api/clips/${c.id}`);
      setSelected(new Set());
      await clips.reload();
    } catch (err) {
      setError(errorText(err));
    }
  };
  const copy = async (list: Clip[]) => {
    await navigator.clipboard.writeText(list.map((c) => (list.length > 1 ? `${c.title}\n${c.body}` : c.body)).join("\n\n"));
    setCopied(list.length === 1 ? list[0].id : -1);
    setTimeout(() => setCopied(null), 1500);
  };
  const ask = (list: Clip[]) => openAi({
    prompt: list.length === 1 ? "Opsummer klippet." : "Opsummer de markerede klip.",
    context: { label: list.length === 1 ? `Klippet «${list[0].title}»` : `${list.length} markerede klip`,
               text: list.map((c) => `# ${c.title}\n${c.body}${c.url ? `\nKilde: ${c.url}` : ""}`).join("\n\n") },
  });

  return (
    <div className="page" style={{ maxWidth: 1240 }}>
      <div className="spread" style={{ alignItems: "flex-end" }}>
        <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
          <div className="eyebrow">Klip</div>
          <h1 style={{ fontSize: "clamp(26px, 3.4vw, 34px)" }}>Dine klip</h1>
          <p className="muted small">Tekst fra websider, mails og dokumenter, eller som du selv har skrevet.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setAdding(!adding)} aria-expanded={adding}>
          <Icon name={adding ? "x" : "plus"} size={16} />{adding ? "Luk" : "Nyt klip"}
        </button>
      </div>
      {adding && (
        <form className="card" onSubmit={add}>
          <label className="field">Titel<input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} autoFocus /></label>
          <label className="field">Tekst<textarea value={body} onChange={(e) => setBody(e.target.value)} /></label>
          <div><button className="btn btn-primary btn-sm">Gem klip</button></div>
        </form>
      )}
      <input type="search" placeholder="Søg i titel og tekst" aria-label="Søg i klip" data-deeplink="clips-search"
             value={clips.q} onChange={(e) => clips.setQ(e.target.value)} style={{ maxWidth: 420 }} />
      <Alert>{error || clips.error}</Alert>

      <section className="data-card" aria-label="Klip">
        <div className={`data-bar ${chosen.length ? "selected" : ""}`}>
          {chosen.length ? (
            <>
              <strong className="small" style={{ color: "#1e3a8a" }}>{chosen.length} valgt</strong>
              {aiEnabled && <button className="btn btn-outline btn-sm" onClick={() => ask(chosen)}><Icon name="sparkle" size={15} />Spørg assistenten</button>}
              <button className="btn btn-outline btn-sm" onClick={() => copy(chosen)}><Icon name="copy" size={15} />{copied === -1 ? "Kopieret" : "Kopiér"}</button>
              <button className="btn btn-danger btn-sm" onClick={() => remove(chosen)}><Icon name="trash" size={15} />Slet</button>
              <button className="btn btn-ghost btn-sm end" onClick={() => setSelected(new Set())}>Fjern markering</button>
            </>
          ) : (
            <span className="small muted">{rows.length ? `${rows.length} klip · markér klip for at kopiere, slette eller spørge assistenten` : " "}</span>
          )}
        </div>
        {clips.loading && !clips.data ? <div style={{ padding: 16 }}><Loading /></div> : rows.length === 0 ? (
          <p className="muted small" style={{ padding: 16 }}>{clips.q ? "Ingen klip matcher søgningen." : "Ingen klip endnu."}</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr>
                <th className="tools"><input type="checkbox" aria-label="Vælg alle" checked={all} onChange={() => setSelected(all ? new Set() : new Set(rows.map((c) => c.id)))} /></th>
                <th>Titel</th><th className="hide-sm">Kilde</th><th className="hide-sm">Gemt</th>
              </tr></thead>
              <tbody>
                {rows.map((c) => {
                  const isOpen = open === c.id;
                  const deep = c.id === openId;
                  return [
                    <tr key={c.id} className={selected.has(c.id) ? "selected" : ""}>
                      <td className="tools"><span>
                        <input type="checkbox" aria-label={`Vælg ${c.title}`} checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                        <button className="icon-btn" aria-expanded={isOpen} aria-label={`${isOpen ? "Skjul" : "Vis"} ${c.title}`} title={isOpen ? "Skjul" : "Vis"}
                                onClick={() => setOpen(isOpen ? null : c.id)}><Icon name="eye" size={16} /></button>
                        <button className="icon-btn hide-sm" aria-label={`Kopiér ${c.title}`} title={copied === c.id ? "Kopieret" : "Kopiér"} onClick={() => copy([c])}>
                          <Icon name={copied === c.id ? "check" : "copy"} size={16} />
                        </button>
                        {aiEnabled && <button className="icon-btn hide-sm" aria-label={`Spørg assistenten om ${c.title}`} title="Spørg assistenten" onClick={() => ask([c])}><Icon name="sparkle" size={16} /></button>}
                        <button className="icon-btn danger hide-sm" aria-label={`Slet ${c.title}`} title="Slet" data-deeplink={deep ? "clip-delete" : undefined} onClick={() => remove([c])}>
                          <Icon name="trash" size={16} />
                        </button>
                      </span></td>
                      <td className="wrap" style={{ minWidth: 220 }}>
                        <button onClick={() => setOpen(isOpen ? null : c.id)} className="title"
                                style={{ background: "none", border: 0, padding: 0, font: "inherit", fontWeight: 600, textAlign: "left", cursor: "pointer" }}>{c.title}</button>
                        {!isOpen && c.body && <div className="excerpt">{c.body}</div>}
                        <div className="tiny muted show-sm">{SOURCE[c.source] ?? c.source} · {fmtDate(c.created_at)}</div>
                      </td>
                      <td className="hide-sm"><span className="pill">{SOURCE[c.source] ?? c.source}</span></td>
                      <td className="muted num hide-sm" style={{ whiteSpace: "nowrap" }}>{fmtDate(c.created_at)}</td>
                    </tr>,
                    isOpen && (
                      <tr key={`${c.id}-body`} className={selected.has(c.id) ? "selected" : ""}>
                        <td />
                        <td colSpan={3} className="wrap">
                          {c.body && <p className="small" data-deeplink={deep ? "clip-body" : undefined} style={{ whiteSpace: "pre-wrap", color: "#334155" }}>{c.body}</p>}
                          {c.url && <a className="tiny" href={c.url} target="_blank" rel="noreferrer" style={{ wordBreak: "break-all" }}>{c.url}</a>}
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
