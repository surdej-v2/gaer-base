// Mapper's dialogs: a name or an address (AskDialog), and editing a shortcut (title, folder, Emner).
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { errorText } from "../api";
import { metaApi } from "../tree/api";
import { TagEditor } from "../tree/TagEditor";
import { Alert } from "../ui";
import { mapperApi, type MapperState, type Shortcut } from "./api";

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
