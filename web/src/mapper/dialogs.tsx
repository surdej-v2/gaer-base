// Mapper's dialogs and small overlays: a name or an address (AskDialog), editing a shortcut (title, folder, Emner), a
// folder's properties (Egenskaber), a folder picker ("Flyt til…"), the bottom sheet of the narrow panel, the "…" menu of a
// row, and toasts.
import { ArrowDown, ArrowUp, Ellipsis, Folder as FolderIcon, X } from "lucide-react";
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { errorText } from "../api";
import { metaApi } from "../tree/api";
import { TagEditor } from "../tree/TagEditor";
import { Alert } from "../ui";
import { mapperApi, type ChildSort, type Folder, type Layout, type MapperState, type Shortcut, type ShortcutSort } from "./api";
import { CHILD_SORTS, SHORTCUT_SORTS, childOrder, folderList, folderKey, type Group } from "./model";

export type Ask = { title: string; label: string; value: string; confirm: string; share?: boolean; onSubmit: (value: string, share: boolean) => Promise<void> };

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <div className="card dialog" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <strong>{title}</strong>
        {children}
      </div>
    </div>
  );
}

export function AskDialog({ ask, onClose }: { ask: Ask; onClose: () => void }) {
  const [value, setValue] = useState(ask.value);
  const [share, setShare] = useState(!!ask.share);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await ask.onSubmit(value.trim(), share);
      onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={ask.title} onClose={onClose}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <label className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
          <span className="small" style={{ fontWeight: 600 }}>{ask.label}</span>
          <input autoFocus value={value} maxLength={2000} onChange={(e) => setValue(e.target.value)} />
        </label>
        {ask.share !== undefined && (
          <label className="mp-opt"><input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} />
            <span>Del med teamet<br /><span className="tiny muted">Alle brugere kan vælge visningen; kun du kan rette den.</span></span></label>
        )}
        <Alert>{error}</Alert>
        <div className="row">
          <button className="btn btn-primary btn-sm" disabled={busy || !value.trim()}>{ask.confirm}</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>Annullér</button>
        </div>
      </form>
    </Modal>
  );
}

export function EditShortcut({ item, from, state, onClose, onSaved }: {
  item: Shortcut; from: number | null; state: MapperState; onClose: () => void; onSaved: (text: string) => Promise<void>;
}) {
  const [title, setTitle] = useState(item.title);
  const [to, setTo] = useState<number | null>(from);
  const [tags, setTags] = useState<number[]>(item.tags.map((t) => t.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const options = useMemo(() => {
    const out: { id: number; label: string }[] = [];
    const walk = (parent: number | null, prefix: string) => {
      for (const f of state.folders.filter((x) => x.parent_id === parent).sort((a, b) => a.sort_order - b.sort_order || a.label.localeCompare(b.label, "da"))) {
        out.push({ id: f.id, label: prefix + f.label });
        walk(f.id, prefix + " ");
      }
    };
    walk(null, "");
    return out;
  }, [state.folders]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      let cur = item;
      if (title.trim() && title.trim() !== item.title) cur = await mapperApi.renameShortcut(item.id, title.trim());
      if (to !== from && item.folders.includes(from)) cur = await mapperApi.moveShortcut(item.id, from, to);
      const before = item.tags.map((t) => t.id).sort().join(",");
      if ([...tags].sort().join(",") !== before && state.root) {
        // Your tags on the object become exactly these: Mapper's folders are tags too, so they go along.
        const folders = cur.folders.map((f) => f ?? state.root!.id);
        await metaApi.setTags(item.id, [...new Set([...tags, ...folders])]);
      }
      await onSaved(`„${title.trim() || item.title}“ er gemt.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Ret genvej" onClose={onClose}>
      <span className="tiny muted" style={{ wordBreak: "break-all" }}>{item.url}</span>
      <label className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
        <span className="small" style={{ fontWeight: 600 }}>Titel</span>
        <input value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} />
        {!item.mine && <span className="tiny muted">Titlen er din egen; andre, der har samme adresse, ser deres egen.</span>}
      </label>
      {item.folders.includes(from) && (
        <label className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
          <span className="small" style={{ fontWeight: 600 }}>Mappe</span>
          <select value={to ?? ""} onChange={(e) => setTo(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Øverst i Mapper</option>
            {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        </label>
      )}
      <TagEditor value={tags} onChange={setTags} label="Emner" />
      <Alert>{error}</Alert>
      <div className="row">
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={save}>Gem</button>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Annullér</button>
      </div>
    </Modal>
  );
}

export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="mpn-backdrop" role="presentation" onClick={onClose}>
      <div className="mpn-sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="mpn-head">
          <div className="mpn-titles"><strong>{title}</strong></div>
          <button className="mpn-ib" onClick={onClose} aria-label="Luk"><X size={20} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}


/** A short confirmation at the bottom ("Flyttet til …"), gone after a few seconds. */
export function Toast({ text, onDone }: { text: string | null; onDone: () => void }) {
  useEffect(() => {
    if (!text) return;
    const t = window.setTimeout(onDone, 3500);
    return () => window.clearTimeout(t);
  }, [text, onDone]);
  if (!text) return null;
  return <div className="mp-toast" role="status" aria-live="polite">{text}</div>;
}

export type MenuItem = { label: string; hint?: string; onSelect: () => void; danger?: boolean; disabled?: boolean };

/** The "…" menu of a row (wide layout). A transparent layer behind it closes it, so there is no global listener. */
export function RowMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const list = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not(:disabled)"));
    const i = list.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") setOpen(false);
    else if (e.key === "ArrowDown") list[(i + 1) % list.length]?.focus();
    else if (e.key === "ArrowUp") list[(i - 1 + list.length) % list.length]?.focus();
    else return;
    e.preventDefault();
    e.stopPropagation();
  };
  return (
    <span className="mp-menu-wrap" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <button type="button" className="mp-icon" aria-haspopup="menu" aria-expanded={open} aria-label={label} title="Flere handlinger"
        onClick={() => setOpen((o) => !o)}><Ellipsis size={16} /></button>
      {open && (
        <>
          <div className="mp-menu-layer" onClick={() => setOpen(false)} />
          <div className="mp-menu" role="menu" aria-label={label} onKeyDown={onKey}>
            {items.map((it, i) => (
              <button key={it.label} type="button" role="menuitem" autoFocus={i === 0} disabled={it.disabled}
                className={it.danger ? "mp-danger" : undefined} onClick={() => { setOpen(false); it.onSelect(); }}>
                <span>{it.label}</span>{it.hint && <kbd>{it.hint}</kbd>}
              </button>
            ))}
          </div>
        </>
      )}
    </span>
  );
}

/** "Flyt til …" / "Kopiér til …": the user's folders as a list. Folders in exclude (the folder itself and its
 * subfolders, when a folder is moved) cannot be chosen. */
export function FolderPicker({ state, title, exclude, current, onPick, onClose, narrow }: {
  state: MapperState; title: string; exclude?: Set<number>; current?: number | null;
  onPick: (id: number | null) => void; onClose: () => void; narrow?: boolean;
}) {
  const list = useMemo(() => folderList(state), [state]);
  const body = (
    <div className="mp-picker" role="listbox" aria-label={title}>
      <button type="button" role="option" aria-selected={current === null} className="mp-pick" onClick={() => onPick(null)}
        disabled={current === null}>
        <FolderIcon size={16} aria-hidden="true" /> <span>Øverst i Mapper</span>
      </button>
      {list.map(({ folder, level }) => (
        <button key={folder.id} type="button" role="option" aria-selected={current === folder.id} className="mp-pick"
          style={{ paddingLeft: 12 + level * 18 }} disabled={exclude?.has(folder.id) || current === folder.id} onClick={() => onPick(folder.id)}>
          <FolderIcon size={16} aria-hidden="true" /> <span>{folder.label}</span>
        </button>
      ))}
    </div>
  );
  return narrow ? <Sheet title={title} onClose={onClose}>{body}</Sheet> : <Modal title={title} onClose={onClose}>{body}</Modal>;
}

const LAYOUT_CHOICES: { id: Layout | ""; label: string }[] = [
  { id: "", label: "Følg visningen" },
  { id: "list", label: "Liste" },
  { id: "grid", label: "Kort" },
  { id: "table", label: "Tabel" },
];

/** Egenskaber for a folder (folderId null = the top level of Mapper, which takes only the orderings and the layout):
 * name, English label, sort key, how the subfolders are ordered (with up/down for Manuel), how the shortcuts are sorted
 * when the view says "Mappens sortering", and the layout the folder opens in. */
export function FolderProperties({ state, folderId, narrow, onSaved, onClose }: {
  state: MapperState; folderId: number | null; narrow?: boolean; onSaved: (text: string) => Promise<void>; onClose: () => void;
}) {
  const f: Folder | null = folderId === null ? null : state.folders.find((x) => x.id === folderId) ?? null;
  const root = state.root!;
  const [label, setLabel] = useState(f?.label ?? "");
  const [en, setEn] = useState(f?.en_label ?? "");
  const [key, setKey] = useState(f?.sort_key ?? "");
  const [childSort, setChildSort] = useState<ChildSort>((f ? f.child_sort : root.child_sort) ?? "label");
  const [shortcutSort, setShortcutSort] = useState<ShortcutSort>((f ? f.shortcut_sort : root.shortcut_sort) ?? "manual");
  const [layout, setLayout] = useState<Layout | "">((f ? f.layout : root.layout) ?? "");
  const kids = useMemo(() => {
    const byKey = new Map(state.folders.map((x) => [folderKey(x.id), x]));
    const groups: Group[] = state.folders.filter((x) => x.parent_id === folderId)
      .map((x) => ({ key: folderKey(x.id), label: x.label, parent: null, kind: "folder", folderId: x.id }));
    return groups.sort(childOrder((f ? f.child_sort : root.child_sort) ?? null, (g) => byKey.get(g.key)!)).map((g) => byKey.get(g.key)!);
  }, [state, folderId, f, root.child_sort]);
  const [order, setOrder] = useState<Folder[]>(kids);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const move = (i: number, by: number) => setOrder((o) => {
    const n = [...o];
    const [x] = n.splice(i, 1);
    n.splice(i + by, 0, x);
    return n;
  });

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const id = f?.id ?? root.id;
      const body: Parameters<typeof mapperApi.saveFolder>[1] = {
        child_sort: childSort === "label" ? null : childSort, shortcut_sort: shortcutSort, layout: layout || null,
      };
      if (f) Object.assign(body, { label: label.trim() || f.label, en_label: en.trim() || null, sort_key: key.trim() || null });
      await mapperApi.saveFolder(id, body);
      const ids = order.map((x) => x.id);
      if (childSort === "manual" && ids.join() !== kids.map((x) => x.id).join()) await mapperApi.orderChildren(id, ids);
      await onSaved(`Egenskaber for „${f ? label.trim() || f.label : "Mapper"}“ er gemt.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const title = `Egenskaber · ${f?.label ?? "Mapper (øverste niveau)"}`;
  const field = (lbl: string, el: ReactNode, hint?: string) => (
    <label className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
      <span className="small" style={{ fontWeight: 600 }}>{lbl}</span>{el}{hint && <span className="tiny muted">{hint}</span>}
    </label>
  );
  const form = (
    <form className="stack mp-props" style={{ "--gap": "12px" } as React.CSSProperties} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {f && field("Navn", <input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} required />)}
      {f && field("Engelsk navn", <input value={en} maxLength={120} onChange={(e) => setEn(e.target.value)} placeholder="Fx Operations" />)}
      {f && field("Sorteringsnøgle", <input value={key} maxLength={64} onChange={(e) => setKey(e.target.value)} placeholder="Fx 010" />,
        "Bruges, når mappen over denne sorterer sine undermapper efter sorteringsnøgle.")}
      <fieldset className="mp-fieldset">
        <legend className="small" style={{ fontWeight: 600 }}>Undermapper sorteres efter</legend>
        {CHILD_SORTS.map((c) => (
          <label key={c.id} className="mp-opt mp-touch-row"><input type="radio" name="child-sort" checked={childSort === c.id} onChange={() => setChildSort(c.id)} />
            <span>{c.label}{c.id === "label" ? " (standard)" : ""}</span></label>
        ))}
        {childSort === "manual" && (order.length ? (
          <ol className="mp-order" aria-label="Rækkefølge af undermapper">
            {order.map((x, i) => (
              <li key={x.id}>
                <span>{x.label}</span>
                <button type="button" className="mp-icon mp-touch-btn" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Flyt ${x.label} op`}><ArrowUp size={15} /></button>
                <button type="button" className="mp-icon mp-touch-btn" disabled={i === order.length - 1} onClick={() => move(i, 1)} aria-label={`Flyt ${x.label} ned`}><ArrowDown size={15} /></button>
              </li>
            ))}
          </ol>
        ) : <span className="tiny muted">Ingen undermapper endnu.</span>)}
      </fieldset>
      {field("Genveje sorteres efter", (
        <select value={shortcutSort} onChange={(e) => setShortcutSort(e.target.value as ShortcutSort)}>
          {SHORTCUT_SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      ), "Gælder, når visningen sorterer efter „Mappens sortering“.")}
      {field("Layout", (
        <select value={layout} onChange={(e) => setLayout(e.target.value as Layout | "")}>
          {LAYOUT_CHOICES.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
        </select>
      ), "Mappen åbner i dette layout; „Følg visningen“ bruger visningens.")}
      <Alert>{error}</Alert>
      <div className={narrow ? "stack" : "row"} style={{ "--gap": "6px" } as React.CSSProperties}>
        <button className={`btn btn-primary ${narrow ? "btn-block mpn-touch" : "btn-sm"}`} disabled={busy}>Gem</button>
        <button type="button" className={`btn btn-ghost ${narrow ? "btn-block mpn-touch" : "btn-sm"}`} onClick={onClose}>Annullér</button>
      </div>
    </form>
  );
  return narrow ? <Sheet title={title} onClose={onClose}>{form}</Sheet> : <Modal title={title} onClose={onClose}>{form}</Modal>;
}

/** "Tilføj genvej" where there is no current tab (/m, a plain browser): address, title, folder and Emner. */
export function AddShortcut({ state, folderId, narrow, onSaved, onClose }: {
  state: MapperState; folderId: number | null; narrow?: boolean; onSaved: (text: string) => Promise<void>; onClose: () => void;
}) {
  const [url, setUrl] = useState("https://");
  const [title, setTitle] = useState("");
  const [to, setTo] = useState<number | null>(folderId);
  const [tags, setTags] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const list = useMemo(() => folderList(state), [state]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await mapperApi.addShortcut(url.trim(), title.trim() || null, to);
      if (tags.length && state.root) {
        // Your tags on the object become these plus the folders it lies in (folders are tags too).
        await metaApi.setTags(r.id, [...new Set([...tags, ...r.folders.map((f) => f ?? state.root!.id)])]);
      }
      const where = to === null ? "Mapper" : list.find((x) => x.folder.id === to)?.folder.label ?? "mappen";
      await onSaved(r.added ? `„${r.title}“ er gemt i „${where}“.` : `„${r.title}“ ligger allerede i „${where}“.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const form = (
    <form className="stack mp-props" style={{ "--gap": "12px" } as React.CSSProperties} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
        <span className="small" style={{ fontWeight: 600 }}>Adresse</span>
        <input type="url" inputMode="url" autoFocus required value={url} maxLength={2000} onChange={(e) => setUrl(e.target.value)} />
      </label>
      <label className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
        <span className="small" style={{ fontWeight: 600 }}>Titel</span>
        <input value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} placeholder="Tom: sidens værtsnavn" />
      </label>
      <label className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
        <span className="small" style={{ fontWeight: 600 }}>Mappe</span>
        <select value={to ?? ""} onChange={(e) => setTo(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Øverst i Mapper</option>
          {list.map(({ folder, level }) => <option key={folder.id} value={folder.id}>{" ".repeat(level)}{folder.label}</option>)}
        </select>
      </label>
      <TagEditor value={tags} onChange={setTags} label="Emner" />
      <Alert>{error}</Alert>
      <div className={narrow ? "stack" : "row"} style={{ "--gap": "6px" } as React.CSSProperties}>
        <button className={`btn btn-primary ${narrow ? "btn-block mpn-touch" : "btn-sm"}`} disabled={busy || !/^https?:\/\/.+/.test(url.trim())}>Gem genvej</button>
        <button type="button" className={`btn btn-ghost ${narrow ? "btn-block mpn-touch" : "btn-sm"}`} onClick={onClose}>Annullér</button>
      </div>
    </form>
  );
  return narrow ? <Sheet title="Tilføj genvej" onClose={onClose}>{form}</Sheet> : <Modal title="Tilføj genvej" onClose={onClose}>{form}</Modal>;
}
