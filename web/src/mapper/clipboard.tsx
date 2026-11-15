// Mapper's own clipboard: copy / cut / paste of shortcuts and folders, shared by the "…" menus, ⌘/Ctrl+C/X/V on a
// focused row (element-level handlers, no global listener), drag and drop and "Flyt til…".
//   shortcut  cut + paste = move (retag from the old folder to the new) · copy + paste = one more folder (one more tag)
//   folder    cut + paste = move (meta's path rewrite) · copy + paste = the subtree cloned, "<name> (kopi)"
// The browser's clipboard is only used by "Kopiér adresse".
import { ClipboardPaste, X } from "lucide-react";
import { useCallback, useState, type KeyboardEvent } from "react";
import { errorText } from "../api";
import { mapperApi, type MapperState } from "./api";
import { descendantsOf } from "./model";

export type ClipItem = { kind: "shortcut" | "folder"; id: number; from: number | null; label: string };
export type Clip = ClipItem & { mode: "copy" | "cut" };

/** ⌘/Ctrl + C, X or V on an element (not while typing in a field). */
export function clipKey(e: KeyboardEvent): "copy" | "cut" | "paste" | null {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return null;
  const t = e.target as HTMLElement;
  if (t.closest("input, textarea, select, [contenteditable=true]")) return null;
  const k = e.key.toLowerCase();
  return k === "c" ? "copy" : k === "x" ? "cut" : k === "v" ? "paste" : null;
}

export function useMapperClipboard({ state, reload, onDone, onError }: {
  state: MapperState | null; reload: () => Promise<void>; onDone: (text: string) => void; onError: (text: string) => void;
}) {
  const [clip, setClip] = useState<Clip | null>(null);
  const name = useCallback((id: number | null) => (id === null ? "Mapper" : state?.folders.find((f) => f.id === id)?.label ?? "mappen"),
    [state]);

  /** Moves or copies an item into a folder (null = the top level). Used by paste, drag and drop and "Flyt til…". */
  const transfer = useCallback(async (item: ClipItem, to: number | null, copy: boolean): Promise<boolean> => {
    if (!state) return false;
    try {
      if (item.kind === "folder" && descendantsOf(state, item.id).has(to as number)) {
        throw new Error(`En mappe kan ikke ${copy ? "kopieres" : "flyttes"} ind i sig selv eller en af sine undermapper`);
      }
      if (item.kind === "shortcut") {
        if (copy) await mapperApi.copyShortcut(item.id, to);
        else if (item.from !== to) await mapperApi.moveShortcut(item.id, item.from, to);
        else return false;
        onDone(copy ? `„${item.label}“ er kopieret til „${name(to)}“.` : `Flyttet til „${name(to)}“.`);
      } else {
        const folder = state.folders.find((f) => f.id === item.id);
        if (copy) {
          const r = await mapperApi.copyFolder(item.id, to);
          onDone(`„${r.label}“ er lagt i „${name(to)}“ (${r.folders} mapper, ${r.shortcuts} genveje).`);
        } else {
          if ((folder?.parent_id ?? null) === to) return false;
          await mapperApi.moveFolder(item.id, to);
          onDone(`Flyttet til „${name(to)}“.`);
        }
      }
      await reload();
      return true;
    } catch (e) {
      onError(errorText(e));
      return false;
    }
  }, [state, name, reload, onDone, onError]);

  const paste = useCallback(async (to: number | null) => {
    if (!clip) return;
    const ok = await transfer(clip, to, clip.mode === "copy");
    if (ok && clip.mode === "cut") setClip(null);
  }, [clip, transfer]);

  return {
    clip,
    copy: (item: ClipItem) => { setClip({ ...item, mode: "copy" }); onDone(`Kopieret: „${item.label}“. Indsæt med ⌘V / Ctrl+V.`); },
    cut: (item: ClipItem) => { setClip({ ...item, mode: "cut" }); onDone(`Klippet: „${item.label}“. Indsæt i en anden mappe.`); },
    clear: () => setClip(null),
    paste,
    transfer,
    name,
  };
}

export type MapperClipboard = ReturnType<typeof useMapperClipboard>;

/** The pill at the bottom while something is on the clipboard. */
export function ClipPill({ cb, target, narrow }: { cb: MapperClipboard; target: number | null | undefined; narrow?: boolean }) {
  if (!cb.clip) return null;
  return (
    <div className={`mp-clip${narrow ? " mp-clip-narrow" : ""}`} role="region" aria-label="Udklipsholder">
      <span className="mp-clip-text">{cb.clip.mode === "copy" ? "Kopieret" : "Klippet"}: <strong>{cb.clip.label}</strong></span>
      {target !== undefined && (
        <button className={`btn btn-primary btn-sm${narrow ? " mpn-touch" : ""}`} onClick={() => void cb.paste(target)}>
          <ClipboardPaste size={14} /> Indsæt i „{cb.name(target)}“
        </button>
      )}
      <button className={`btn btn-ghost btn-sm${narrow ? " mpn-touch" : ""}`} onClick={cb.clear} aria-label="Ryd udklipsholderen"><X size={14} /> Ryd</button>
    </div>
  );
}
