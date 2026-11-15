// /extension/mapper — Mapper: the user's own folders with shortcuts in the side panel (specs/mapper/).
// Left a tree with three listings of the same shortcuts (Mapper = own folders, Emner = their other tags, Websteder =
// domain → host), right the chosen folder: breadcrumb, subfolders, the shortcuts in a saved view (list, cards, table)
// and a preview of the page. A full-screen tree gives the overview. Narrow panels (below 640 px, the usual side panel)
// get their own drill-down layout: MapperNarrow.tsx.
// Copy, cut and paste (clipboard.tsx) from the "…" menus, the folder header and ⌘/Ctrl+C/X/V on a focused row; drag and
// drop moves (⌥/Alt copies) shortcuts and folders onto folders in the tree or the tiles, and reorders shortcuts in a
// folder (the folder's sort becomes manual). Egenskaber (dialogs.tsx) sets a folder's name, labels and orderings.
import {
  BookmarkPlus, ChevronsDownUp, ChevronsUpDown, ClipboardPaste, Copy, Download, ExternalLink, FolderPlus, LayoutGrid, List, Maximize2,
  Minimize2, Pin, PinOff, Plus, Scissors, Search, Settings2, Table2, Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type HTMLAttributes, type KeyboardEvent } from "react";
import { ApiError, errorText } from "../api";
import { extension, isExtension, type PageInfo } from "../bridge/extension";
import { Alert, fmtDay, Loading } from "../ui";
import { mapperApi, type Layout, type MapperState, type SavedView, type Shortcut, type ViewSpec } from "./api";
import { ClipPill, clipKey, useMapperClipboard, type ClipItem } from "./clipboard";
import { AddShortcut, AskDialog, EditShortcut, FolderPicker, FolderProperties, RowMenu, Toast, type Ask, type MenuItem } from "./dialogs";
import { GroupIcon, GroupTree, Letter } from "./GroupTree";
import { ImportWizard } from "./ImportWizard";
import { MapperNarrow } from "./MapperNarrow";
import {
  ALL, BUILTIN, COLUMNS, LISTINGS, SORTS, bareHost, descendantsOf, folderKey, isPdf, local, matches, normalizeSpec, placedIn,
  sameSpec, sortShortcuts, treeFor, type Group, type Listing,
} from "./model";
import { openInTab, Preview, previewable, type Placement } from "./Preview";
import "./mapper.css";

const WIDE = 640;        // tree and content side by side from here
const PREVIEW_RIGHT = 760; // auto: the preview goes right of the list when the content pane is this wide
export const MOD = /Mac|iPhone|iPad/.test(navigator.userAgent) ? "⌘" : "Ctrl+";

/** Copies text to the browser's clipboard ("Kopiér adresse"). */
export async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
}

export default function Mapper() {
  const [state, setState] = useState<MapperState | null>(null);
  const [views, setViews] = useState<SavedView[]>([]);
  const [inExtension, setInExtension] = useState(false);
  const [tab, setTab] = useState<PageInfo | null>(null);
  const [listing, setListing] = useState<Listing>(() => (local.get("listing") as Listing) || "mapper");
  const [selected, setSelected] = useState<string>(ALL);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [full, setFull] = useState(false);
  const [leaves, setLeaves] = useState(true);
  const [q, setQ] = useState("");
  const [preview, setPreview] = useState<Shortcut | null>(null);
  const [placement, setPlacementState] = useState<Placement>(() => (local.get("placement") as Placement) || "auto");
  const [expanded, setExpanded] = useState(false);
  const [importing, setImporting] = useState(false);
  const [viewId, setViewId] = useState(() => local.get("view") || "b:standard");
  const [spec, setSpec] = useState<ViewSpec>(BUILTIN[0].spec);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [editing, setEditing] = useState<{ s: Shortcut; from: number | null } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const clearToast = useCallback(() => setToast(null), []);
  const [props, setProps] = useState<number | null | undefined>(undefined);   // Egenskaber: a folder id, null = the root
  const [picking, setPicking] = useState<{ item: ClipItem; copy: boolean } | null>(null);
  const [layoutPick, setLayoutPick] = useState<Layout | null>(null);           // the layout chosen in this folder
  const [adding, setAdding] = useState(false);                                  // "Tilføj genvej"
  const [dropAt, setDropAt] = useState<string | null>(null);
  const dragged = useRef<ClipItem | null>(null);
  const [, setChecked] = useState(0);
  // Re-render once a preview check is known, so pages that refuse frames lose their "Forhåndsvis" hint.
  const onChecked = useCallback(() => setChecked((n) => n + 1), []);
  // The panel's width decides the layout; a callback ref, since each layout renders its own container.
  const [width, setWidth] = useState(() => window.innerWidth - 28);
  const [boxEl, box] = useState<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, v] = await Promise.all([mapperApi.state(), mapperApi.views()]);
      setState(s);
      setViews(v);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
    isExtension().then(async (yes) => {
      setInExtension(yes);
      if (yes) setTab(await extension.pageInfo().catch(() => null));
    });
    return extension.onTabChanged(setTab);
  }, [load]);

  useEffect(() => {
    if (!boxEl) return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(boxEl);
    return () => ro.disconnect();
  }, [boxEl]);

  // The saved view (built in or from the server) once the views are known.
  useEffect(() => {
    const v = BUILTIN.find((b) => b.id === viewId) ?? views.find((x) => `v:${x.id}` === viewId);
    if (v) setSpec(normalizeSpec(v.spec));
  }, [viewId, views]); // eslint-disable-line react-hooks/exhaustive-deps

  const cb = useMapperClipboard({ state, reload: load, onDone: setToast, onError: setError });
  const tree = useMemo(() => (state ? treeFor(listing, state) : null), [state, listing]);
  // The first level starts open, once per listing.
  const [opened, setOpened] = useState<Set<Listing>>(new Set());
  useEffect(() => {
    if (!tree || opened.has(listing)) return;
    setOpened((o) => new Set(o).add(listing));
    setOpen((o) => new Set([...o, ...tree.children(null).filter((g) => tree.children(g.key).length).map((g) => g.key)]));
  }, [tree, listing, opened]);
  const wide = width >= WIDE;
  useEffect(() => {
    if (tree && selected !== ALL && !tree.groups.has(selected)) setSelected(ALL);
  }, [tree, selected]);

  const setPlacement = (p: Placement) => {
    setPlacementState(p);
    local.set("placement", p);
  };
  const chooseListing = (l: Listing) => {
    setListing(l);
    setSelected(ALL);
    local.set("listing", l);
  };
  const chooseView = (id: string) => {
    setViewId(id);
    local.set("view", id);
  };

  const openGroup = (key: string) => {
    setSelected(key);
    setLayoutPick(null);
    setFull(false);
    setExpanded(false);
    if (tree) setOpen((o) => new Set([...o, ...tree.trail(key).map((g) => g.key)]));
  };

  const run = async (fn: () => Promise<string | void>) => {
    setError(null);
    setNotice(null);
    try {
      const text = await fn();
      if (text) setNotice(text);
      await load();
    } catch (e) {
      setError(errorText(e));
    }
  };

  if (!state || !tree) return error ? <Alert>{error}</Alert> : <Loading />;

  const first = state.root === null;
  if (first || importing) {
    return (
      <div className={`mp${wide ? "" : " mp-narrow"}`} ref={box}>
        <ImportWizard state={state} inExtension={inExtension} first={first} compact={!wide} onCancel={first ? undefined : () => setImporting(false)}
          onDone={async (r) => {
            setImporting(false);
            await load();
            if (r?.target_id) {
              setListing("mapper");
              openGroup(folderKey(r.target_id));
            }
          }} />
      </div>
    );
  }

  if (!wide) {
    return (
      <div className="mp mp-narrow" ref={box}>
        {state.truncated && <Alert kind="warn">Du har mange genveje; kun de første 5.000 vises.</Alert>}
        <MapperNarrow state={state} views={views} reload={load} inExtension={inExtension} tab={tab} listing={listing} setListing={chooseListing}
          spec={spec} setSpec={setSpec} viewId={viewId} setViewId={chooseView} onImport={() => setImporting(true)}
          start={selected !== ALL ? selected : null} cb={cb} toast={toast} setToast={setToast} />
      </div>
    );
  }

  const group = selected === ALL ? null : tree.groups.get(selected) ?? null;
  const folderId = listing === "mapper" ? (group?.folderId ?? null) : null;  // where "Gem denne side" saves
  const order = new Map(state.shortcuts.map((s, i) => [s.id, i]));
  const searching = q.trim().length > 0;
  const base = searching ? state.shortcuts.filter((s) => matches(s, q))
    : group ? tree.direct(group.key) : listing === "mapper" ? state.shortcuts : state.shortcuts.filter((s) => listing === "websteder" || s.tags.length);
  const folder = group?.folderId !== undefined ? state.folders.find((f) => f.id === group.folderId) ?? null : null;
  // "Mappens sortering" and the default layout come from the folder (or, on Alle genveje in Mapper, the root).
  const inMapperFolder = listing === "mapper" && !searching && (!group || folder !== null);
  const pasteTo: number | null | undefined = inMapperFolder ? folderId : undefined;
  const folderLayout = listing === "mapper" && !searching ? (folder ? folder.layout : group ? null : state.root?.layout ?? null) : null;
  const view: ViewSpec = { ...spec, layout: layoutPick ?? folderLayout ?? spec.layout };
  const shown = sortShortcuts(base.filter((s) => matches(s, spec.filter)), spec.sort, order,
    folder ? { id: folder.id, sort: folder.shortcut_sort } : null);
  const setViewSpec = (next: ViewSpec) => {
    if (next.layout !== view.layout) setLayoutPick(next.layout);
    setSpec(next);
  };
  const tiles = searching ? [] : tree.children(group?.key ?? null);
  const folderLabel = (id: number | null) => (id === null ? "Mapper" : state.folders.find((f) => f.id === id)?.label ?? "mappen");

  const saveTab = () => tab && run(async () => {
    const r = await mapperApi.addShortcut(tab.url, tab.title, folderId);
    return r.added ? `„${r.title}“ er gemt i ${folderLabel(folderId)}.` : `„${r.title}“ ligger allerede i ${folderLabel(folderId)}.`;
  });

  const newFolder = (parent: number | null) => setAsk({
    title: parent === null ? "Ny mappe" : "Ny undermappe", label: "Navn", value: "", confirm: "Opret",
    onSubmit: async (label) => {
      const f = await mapperApi.createFolder(label, parent);
      await load();
      openGroup(folderKey(f.id));
    },
  });

  const newShortcut = () => setAdding(true);

  const removeShortcut = (s: Shortcut) => {
    // In a folder: out of that folder. In "Alle genveje", a search or another listing: out of every folder.
    const inFolder = listing === "mapper" && !searching && group?.folderId !== undefined;
    const from = group?.folderId ?? null;
    const text = inFolder ? `Fjern „${s.title}“ fra ${folderLabel(from)}?` : `Fjern „${s.title}“ fra alle dine mapper? Emner på genvejen bliver.`;
    if (!confirm(text)) return;
    if (preview?.id === s.id) setPreview(null);
    void run(async () => {
      await mapperApi.removeShortcut(s.id, inFolder ? from : null, !inFolder);
      return `„${s.title}“ er fjernet.`;
    });
  };

  const deleteFolder = (g: Group) => run(async () => {
    try {
      await mapperApi.deleteFolder(g.folderId!);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 409) throw e;
      if (!confirm(`${e.message}\n\nSlet „${g.label}“ med alle undermapper? Genveje, der kun ligger her, slettes også.`)) return;
      await mapperApi.deleteFolder(g.folderId!, true);
    }
    setSelected(g.parent ?? ALL);
    return `„${g.label}“ er slettet.`;
  });

  const showPreview = (s: Shortcut) => {
    if (!s.url.startsWith("https://") && !s.url.startsWith("http://")) return openInTab(s.url);
    setPreview(s);
    setExpanded(false);
  };

  // ── Clipboard, menus, drag and drop ──
  const itemOf = (s: Shortcut): ClipItem => ({
    kind: "shortcut", id: s.id, label: s.title,
    from: listing === "mapper" && !searching && group?.folderId !== undefined ? group.folderId : s.folders.includes(null) ? null : s.folders[0] ?? null,
  });
  const folderItem = (g: Group): ClipItem => ({ kind: "folder", id: g.folderId!, label: g.label,
    from: state.folders.find((f) => f.id === g.folderId)?.parent_id ?? null });

  const shortcutMenu = (s: Shortcut): MenuItem[] => {
    const inFolder = listing === "mapper" && !searching && group?.folderId !== undefined;
    return [
      { label: "Kopiér", hint: `${MOD}C`, onSelect: () => cb.copy(itemOf(s)) },
      { label: "Klip", hint: `${MOD}X`, onSelect: () => cb.cut(itemOf(s)) },
      { label: "Kopiér adresse", onSelect: () => void copyText(s.url).then(() => setToast("Adressen er kopieret."), () => setError("Adressen kunne ikke kopieres")) },
      { label: "Flyt til …", onSelect: () => setPicking({ item: itemOf(s), copy: false }) },
      { label: "Ret …", onSelect: () => setEditing({ s, from: itemOf(s).from }) },
      { label: inFolder ? "Fjern fra mappen" : "Fjern fra alle mapper", danger: true, onSelect: () => removeShortcut(s) },
    ];
  };

  const keyOnShortcut = (s: Shortcut) => (e: KeyboardEvent) => {
    const k = clipKey(e);
    if (k === "copy") cb.copy(itemOf(s));
    else if (k === "cut") cb.cut(itemOf(s));
    else return;
    e.preventDefault();
    e.stopPropagation();
  };
  const keyOnContent = (e: KeyboardEvent) => {
    if (clipKey(e) === "paste" && cb.clip && pasteTo !== undefined) {
      e.preventDefault();
      void cb.paste(pasteTo);
    }
  };
  const keyOnTree = (key: string, kind: string, e: KeyboardEvent): boolean => {
    const k = clipKey(e);
    if (!k || listing !== "mapper") return false;
    const g = key === ALL ? null : tree.groups.get(key);
    if (k === "paste") {
      if (!cb.clip) return false;
      void cb.paste(g?.folderId ?? null);
    } else if (g?.folderId !== undefined && kind !== "all") {
      if (k === "copy") cb.copy(folderItem(g));
      else cb.cut(folderItem(g));
    } else return false;
    e.preventDefault();
    return true;
  };

  // Drop targets: a folder in the tree, a tile, "Alle genveje" (= the top level). ⌥/Alt copies.
  const canDnd = listing === "mapper" && !searching;
  const dropOn = (key: string, to: number | null): HTMLAttributes<HTMLElement> => !canDnd ? {} : {
    onDragOver: (e: DragEvent) => {
      if (!dragged.current) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = e.altKey ? "copy" : "move";
      if (dropAt !== key) setDropAt(key);
    },
    onDragLeave: () => setDropAt((k) => (k === key ? null : k)),
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      const item = dragged.current;
      dragged.current = null;
      setDropAt(null);
      if (item) void cb.transfer(item, to, e.altKey);
    },
  };
  const dragFrom = (item: () => ClipItem): HTMLAttributes<HTMLElement> => !canDnd ? {} : {
    draggable: true,
    onDragStart: (e: DragEvent) => {
      dragged.current = item();
      e.dataTransfer.effectAllowed = "copyMove";
      e.dataTransfer.setData("text/plain", dragged.current.label);
    },
    onDragEnd: () => {
      dragged.current = null;
      setDropAt(null);
    },
  };
  const badDrop = (to: number | null) => dragged.current?.kind === "folder" && to !== null && descendantsOf(state, dragged.current.id).has(to);
  const dropClass = (key: string, to: number | null) => (dropAt === key ? (badDrop(to) ? " mp-drop-bad" : " mp-drop") : "");

  // Reorder: a shortcut dropped on another in the same folder goes before it (after it on the lower half).
  const reorderable = listing === "mapper" && !searching && folder !== null;
  const reorderOn = (s: Shortcut): HTMLAttributes<HTMLElement> => !reorderable ? {} : {
    onDragOver: (e: DragEvent) => {
      const d = dragged.current;
      if (!d || d.kind !== "shortcut" || d.from !== folder!.id || d.id === s.id) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "move";
      if (dropAt !== `s:${s.id}`) setDropAt(`s:${s.id}`);
    },
    onDragLeave: () => setDropAt((k) => (k === `s:${s.id}` ? null : k)),
    onDrop: (e: DragEvent) => {
      const d = dragged.current;
      if (!d || d.kind !== "shortcut" || d.from !== folder!.id) return;
      e.preventDefault();
      e.stopPropagation();
      dragged.current = null;
      setDropAt(null);
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;
      const all = sortShortcuts(tree.direct(group!.key), spec.sort, order, { id: folder!.id, sort: folder!.shortcut_sort }).map((x) => x.id);
      const ids = all.filter((id) => id !== d.id);
      ids.splice(ids.indexOf(s.id) + (after ? 1 : 0), 0, d.id);
      void run(async () => {
        await mapperApi.orderShortcuts(folder!.id, ids);
        if (spec.sort !== "folder") setSpec({ ...spec, sort: "folder" });
        setToast(`Rækkefølgen i „${folder!.label}“ er gemt.`);
      });
    },
  };
  const rowProps = (s: Shortcut): HTMLAttributes<HTMLElement> => ({
    ...dragFrom(() => itemOf(s)), ...reorderOn(s), onKeyDown: keyOnShortcut(s),
  });

  // ── Tree pane ──
  const treePane = (
    <section className={`mp-pane${full ? " mp-full" : ""}`} aria-label="Træ">
      <div className="segmented" role="tablist" aria-label="Visning af træet">
        {LISTINGS.map((l) => (
          <button key={l.id} role="tab" aria-selected={listing === l.id} className={listing === l.id ? "active" : ""} onClick={() => chooseListing(l.id)}>{l.label}</button>
        ))}
      </div>
      <div className="mp-head">
        <h3>{LISTINGS.find((l) => l.id === listing)!.label}</h3>
        {listing === "mapper" && (
          <button className="mp-icon" onClick={() => newFolder(null)} aria-label="Ny mappe" title="Ny mappe"><FolderPlus size={16} /></button>
        )}
        <button className="mp-icon" onClick={() => setImporting(true)} aria-label="Importér favoritter" title="Importér favoritter"><Download size={16} /></button>
        <button className={`mp-icon${full ? " active" : ""}`} onClick={() => setFull((f) => !f)}
          aria-label={full ? "Afslut fuld skærm" : "Vis træet i fuld skærm"} title={full ? "Afslut fuld skærm" : "Vis træet i fuld skærm"} aria-pressed={full}>
          {full ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        </button>
      </div>
      {full && (
        <div className="mp-toolbar">
          <button className="btn btn-outline btn-sm" onClick={() => setOpen(new Set(tree.groups.keys()))}><ChevronsUpDown size={14} /> Fold alt ud</button>
          <button className="btn btn-outline btn-sm" onClick={() => setOpen(new Set())}><ChevronsDownUp size={14} /> Fold alt sammen</button>
          <label><input type="checkbox" checked={leaves} onChange={(e) => setLeaves(e.target.checked)} /> Vis genveje i træet</label>
        </div>
      )}
      <GroupTree tree={tree} state={state} selected={selected} open={open} full={full} leaves={full && leaves}
        onToggle={(k) => setOpen((o) => {
          const n = new Set(o);
          if (n.has(k)) n.delete(k);
          else n.add(k);
          return n;
        })}
        onOpen={openGroup}
        onShortcut={(s, key) => {
          openGroup(key);
          showPreview(s);
        }}
        onRowKey={keyOnTree}
        rowAttrs={(key, kind) => {
          if (listing !== "mapper" || full) return {};
          const g = key === ALL ? null : tree.groups.get(key);
          const to = g?.folderId ?? null;
          if (kind === "all") return { ...dropOn("t:root", null), className: dropClass("t:root", null) } as HTMLAttributes<HTMLDivElement>;
          return { ...dragFrom(() => folderItem(g!)), ...dropOn(`t:${key}`, to), className: dropClass(`t:${key}`, to) } as HTMLAttributes<HTMLDivElement>;
        }} />
      {listing === "mapper" && !state.folders.length && <p className="small muted" style={{ margin: 0 }}>Ingen mapper endnu. Opret en, eller importér dine favoritter.</p>}
      {listing === "emner" && !tree.groups.size && <p className="small muted" style={{ margin: 0 }}>Ingen af dine genveje har emner endnu. Giv dem emner under „Ret“, eller under Tags.</p>}
    </section>
  );

  // ── Content pane ──
  const crumbs = group ? tree.trail(group.key) : [];
  const total = searching ? shown.length : group ? tree.total(group.key) : base.length;
  const nouns = listing === "mapper" ? ["undermappe", "undermapper", "mappe", "mapper"]
    : listing === "emner" ? ["underemne", "underemner", "emne", "emner"] : ["vært", "værter", "websted", "websteder"];
  const tileNoun = tiles.length === 1 ? nouns[group ? 0 : 2] : nouns[group ? 1 : 3];
  const rightSide = placement === "right" || (placement === "auto" && width - 272 >= PREVIEW_RIGHT);

  const list = (
    <ShortcutList items={shown} spec={view} state={state} current={preview?.id ?? null} onPreview={showPreview}
      menu={shortcutMenu} rowProps={rowProps} dropAt={dropAt} />
  );

  const contentPane = (
    <section className="mp-pane" aria-label="Indhold" onKeyDown={keyOnContent}>
      <nav className="mp-crumbs" aria-label="Brødkrummer">
        <button onClick={() => { setSelected(ALL); setQ(""); }}>{searching ? "Alle genveje" : LISTINGS.find((l) => l.id === listing)!.label}</button>
        {!searching && crumbs.map((c) => <span key={c.key}>› <button onClick={() => openGroup(c.key)}>{c.label}</button></span>)}
        {!searching && group && <span>› {group.label}</span>}
      </nav>
      <div className="mp-title">
        <h2>{searching ? `Søgning: „${q.trim()}“` : group ? group.label : "Alle genveje"}</h2>
        <span className="small muted">
          {searching ? `${shown.length} genveje` : `${tiles.length ? `${tiles.length} ${tileNoun} · ` : ""}${total} genveje i alt`}
        </span>
        {group?.pinned && <span className="pill">Fastgjort</span>}
      </div>
      {!searching && (
        <div className="mp-actions">
          {listing === "mapper" && inExtension && (
            <button className="btn btn-primary btn-sm" disabled={!tab?.readable} onClick={saveTab} title={tab?.readable ? tab.title : "Kun almindelige websider (http og https)"}>
              <BookmarkPlus size={14} /> Gem denne side
            </button>
          )}
          {listing === "mapper" && (
            <button className={`btn ${inExtension ? "btn-outline" : "btn-primary"} btn-sm`} onClick={newShortcut}><Plus size={14} /> Tilføj genvej</button>
          )}
          {listing === "mapper" && <button className="btn btn-outline btn-sm" onClick={() => newFolder(group?.folderId ?? null)}><FolderPlus size={14} /> {group ? "Ny undermappe" : "Ny mappe"}</button>}
          {inMapperFolder && (
            <button className="btn btn-ghost btn-sm" onClick={() => setProps(group?.folderId ?? null)}><Settings2 size={14} /> Egenskaber</button>
          )}
          {listing === "mapper" && group?.folderId !== undefined && (
            <>
              <button className="btn btn-ghost btn-sm" onClick={() => cb.copy(folderItem(group))}><Copy size={14} /> Kopiér mappe</button>
              <button className="btn btn-ghost btn-sm" onClick={() => cb.cut(folderItem(group))}><Scissors size={14} /> Klip mappe</button>
            </>
          )}
          {cb.clip && pasteTo !== undefined && (
            <button className="btn btn-outline btn-sm" onClick={() => void cb.paste(pasteTo)}><ClipboardPaste size={14} /> Indsæt</button>
          )}
          {listing === "mapper" && group?.folderId !== undefined && (
            <>
              <button className="btn btn-ghost btn-sm" onClick={() => run(async () => { await mapperApi.pin(group.folderId!, !group.pinned); })}>
                {group.pinned ? <><PinOff size={14} /> Frigør</> : <><Pin size={14} /> Fastgør</>}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => deleteFolder(group)}><Trash2 size={14} /> Slet</button>
            </>
          )}
        </div>
      )}
      {tiles.length > 0 && !expanded && (
        <div className="mp-tiles">
          {tiles.map((t) => (
            <button key={t.key} className={`mp-tile${t.folderId !== undefined ? dropClass(`tile:${t.key}`, t.folderId) : ""}`} onClick={() => openGroup(t.key)}
              {...(t.folderId !== undefined ? { ...dragFrom(() => folderItem(t)), ...dropOn(`tile:${t.key}`, t.folderId) } : {})}
              onKeyDown={(e) => {
                if (t.folderId === undefined || listing !== "mapper") return;
                const k = clipKey(e);
                if (k === "copy") cb.copy(folderItem(t));
                else if (k === "cut") cb.cut(folderItem(t));
                else if (k === "paste" && cb.clip) void cb.paste(t.folderId);
                else return;
                e.preventDefault();
                e.stopPropagation();
              }}>
              <GroupIcon g={t} />
              <span className="mp-label">{t.label}</span>
              <span className="mp-count">{tree.total(t.key)}</span>
            </button>
          ))}
        </div>
      )}
      {!expanded && <ViewBar spec={view} base={spec} setSpec={setViewSpec} viewId={viewId} setViewId={chooseView} views={views} reload={load}
        folderLayout={folderLayout} />}
      <div className={`mp-split${preview && rightSide && !expanded ? " mp-right" : ""}`}>
        {!expanded && (shown.length ? list : <p className="small muted" style={{ margin: 0 }}>{searching ? "Ingen genveje passer." : spec.filter ? "Ingen genveje passer til filteret." : "Ingen genveje her endnu."}</p>)}
        {preview && (
          <Preview shortcut={preview} placement={placement} onPlacement={setPlacement} expanded={expanded} onExpanded={setExpanded}
            onClose={() => { setPreview(null); setExpanded(false); }} onChecked={onChecked} />
        )}
      </div>
    </section>
  );

  return (
    <div className="mp" ref={box}>
      <label className="mp-search">
        <Search size={15} aria-hidden="true" />
        <span className="sr-only">Søg i alle genveje</span>
        <input type="search" placeholder="Søg i alle genveje" value={q} onChange={(e) => {
          setQ(e.target.value);
          if (e.target.value.trim()) setFull(false);
        }} />
      </label>
      {state.truncated && <Alert kind="warn">Du har mange genveje; kun de første 5.000 vises.</Alert>}
      {full ? treePane : <div className="mp-body mp-wide">{treePane}{contentPane}</div>}
      <Alert kind="success">{notice}</Alert>
      <Alert>{error}</Alert>
      <ClipPill cb={cb} target={pasteTo} />
      <Toast text={toast} onDone={clearToast} />
      {props !== undefined && (
        <FolderProperties state={state} folderId={props} onClose={() => setProps(undefined)}
          onSaved={async (text) => { setProps(undefined); setLayoutPick(null); setToast(text); await load(); }} />
      )}
      {picking && (
        <FolderPicker state={state} title={`Flyt „${picking.item.label}“ til …`} current={picking.item.from}
          exclude={picking.item.kind === "folder" ? descendantsOf(state, picking.item.id) : undefined}
          onClose={() => setPicking(null)} onPick={(to) => { const p = picking; setPicking(null); void cb.transfer(p.item, to, p.copy); }} />
      )}
      {adding && (
        <AddShortcut state={state} folderId={folderId} onClose={() => setAdding(false)}
          onSaved={async (text) => { setAdding(false); setToast(text); await load(); }} />
      )}
      {ask && <AskDialog ask={ask} onClose={() => setAsk(null)} />}
      {editing && (
        <EditShortcut item={editing.s} from={editing.from} state={state} onClose={() => setEditing(null)}
          onSaved={async (text) => { setEditing(null); setNotice(text); await load(); }} />
      )}
    </div>
  );
}

// ── The list in a view: list, cards or table ───────────────────────────────

function ShortcutList({ items, spec, state, current, onPreview, menu, rowProps, dropAt }: {
  items: Shortcut[]; spec: ViewSpec; state: MapperState; current: number | null;
  onPreview: (s: Shortcut) => void; menu: (s: Shortcut) => MenuItem[];
  rowProps: (s: Shortcut) => HTMLAttributes<HTMLElement>; dropAt: string | null;
}) {
  const actions = (s: Shortcut) => (
    <span className="mp-item-actions" onClick={(e) => e.stopPropagation()}>
      <button className="mp-icon" onClick={() => openInTab(s.url)} aria-label={`Åbn ${s.title} i ny fane`} title="Åbn i ny fane"><ExternalLink size={15} /></button>
      <RowMenu label={`Handlinger for ${s.title}`} items={menu(s)} />
    </span>
  );
  const drop = (s: Shortcut) => (dropAt === `s:${s.id}` ? " mp-drop-line" : "");
  const hint = (s: Shortcut) => previewable(s) ? <span className="mp-hint" aria-hidden="true">Forhåndsvis</span> : null;
  const keyOpen = (s: Shortcut) => (e: KeyboardEvent) => {
    if (e.key === "Enter" && e.target === e.currentTarget) onPreview(s);
    else rowProps(s).onKeyDown?.(e as KeyboardEvent<HTMLElement>);
  };
  const attrs = (s: Shortcut) => {
    const { onKeyDown: _k, ...rest } = rowProps(s);
    void _k;
    return rest;
  };
  const cols = new Set(spec.columns);

  if (spec.layout === "table") {
    return (
      <div className="table-wrap">
        <table className="mp-table">
          <thead>
            <tr>
              <th>Titel</th>
              {COLUMNS.filter((c) => cols.has(c.id)).map((c) => <th key={c.id}>{c.label}</th>)}
              <th><span className="sr-only">Handlinger</span></th>
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id} {...attrs(s)} className={`mp-row${current === s.id ? " mp-current" : ""}${drop(s)}`} tabIndex={0} onClick={() => onPreview(s)} onKeyDown={keyOpen(s)}>
                <td title={s.url}><span className="row" style={{ "--gap": "8px", flexWrap: "nowrap" } as React.CSSProperties}>
                  <Letter s={s} /> <strong style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{s.title}</strong>
                  {isPdf(s.url) && <span className="mp-badge">PDF</span>} {hint(s)}</span></td>
                {cols.has("site") && <td>{bareHost(s.host)}</td>}
                {cols.has("folder") && <td>{placedIn(s, state).join(" · ")}</td>}
                {cols.has("added") && <td>{fmtDay(s.added_at)}</td>}
                <td>{actions(s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (spec.layout === "grid") {
    return (
      <div className="mp-grid">
        {items.map((s) => (
          <div key={s.id} {...attrs(s)} className={`mp-card${current === s.id ? " mp-current" : ""}${drop(s)}`} tabIndex={0} role="button" title={s.url}
            onClick={() => onPreview(s)} onKeyDown={keyOpen(s)}>
            <Letter s={s} />
            {hint(s)}
            <strong>{s.title}</strong>
            <span className="tiny muted">{bareHost(s.host)}{isPdf(s.url) ? " · PDF" : ""}</span>
            {actions(s)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="mp-list">
      {items.map((s) => (
        <div key={s.id} {...attrs(s)} className={`mp-item${current === s.id ? " mp-current" : ""}${drop(s)}`} tabIndex={0} role="button" title={s.url}
          onClick={() => onPreview(s)} onKeyDown={keyOpen(s)}>
          <Letter s={s} />
          <span className="mp-item-text">
            <strong>{s.title}</strong>
            <span>{bareHost(s.host)}{s.tags.length ? ` · ${s.tags.map((t) => t.label).join(", ")}` : ""}</span>
          </span>
          {isPdf(s.url) && <span className="mp-badge">PDF</span>}
          {hint(s)}
          {actions(s)}
        </div>
      ))}
    </div>
  );
}

// ── Views: Indbyggede, Delt med teamet, Mine visninger ─────────────────────

function ViewBar({ spec, base, setSpec, viewId, setViewId, views, reload, folderLayout }: {
  /** spec: what is shown (the folder's layout applied); base: the view as the user set it, for "Ændret" and saving. */
  spec: ViewSpec; base: ViewSpec; setSpec: (s: ViewSpec) => void; viewId: string; setViewId: (id: string) => void; views: SavedView[];
  reload: () => Promise<void>; folderLayout: Layout | null;
}) {
  const [saving, setSaving] = useState<Ask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const builtin = BUILTIN.find((b) => b.id === viewId);
  const saved = views.find((v) => `v:${v.id}` === viewId);
  const baseSpec = builtin?.spec ?? (saved ? normalizeSpec(saved.spec) : BUILTIN[0].spec);
  const changed = !sameSpec(base, baseSpec);
  const shared = views.filter((v) => v.shared);
  const mine = views.filter((v) => v.mine && !v.shared);
  const set = (patch: Partial<ViewSpec>) => setSpec({ ...spec, ...patch });
  const act = async (fn: () => Promise<void>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
      <div className="mp-viewbar">
        <select aria-label="Visning" value={viewId} onChange={(e) => setViewId(e.target.value)}>
          <optgroup label="Indbyggede">{BUILTIN.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</optgroup>
          {shared.length > 0 && <optgroup label="Delt med teamet">{shared.map((v) => <option key={v.id} value={`v:${v.id}`}>{v.name}{v.owner_name && !v.mine ? ` (${v.owner_name})` : ""}</option>)}</optgroup>}
          {mine.length > 0 && <optgroup label="Mine visninger">{mine.map((v) => <option key={v.id} value={`v:${v.id}`}>{v.name}</option>)}</optgroup>}
        </select>
        {changed && <span className="pill mp-changed">Ændret</span>}
        <span className="segmented" role="group" aria-label="Layout">
          {([["list", List, "Liste"], ["grid", LayoutGrid, "Kort"], ["table", Table2, "Tabel"]] as const).map(([id, Icon, label]) => (
            <button key={id} type="button" className={spec.layout === id ? "active" : ""} aria-pressed={spec.layout === id} aria-label={label} title={label}
              onClick={() => set({ layout: id })}><Icon size={15} /></button>
          ))}
        </span>
        {folderLayout && spec.layout === folderLayout && spec.layout !== base.layout && <span className="tiny muted">Mappens layout</span>}
        <select aria-label="Sortering" value={spec.sort} onChange={(e) => set({ sort: e.target.value as ViewSpec["sort"] })}>
          {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
        {changed && saved?.mine && (
          <button className="btn btn-outline btn-sm" onClick={() => act(async () => { await mapperApi.updateView(saved.id, { spec: base }); await reload(); })}>Gem</button>
        )}
        {changed && (
          <button className="btn btn-outline btn-sm" onClick={() => setSaving({
            title: "Gem som visning", label: "Navn", value: "", confirm: "Gem", share: false,
            onSubmit: async (name, share) => {
              const v = await mapperApi.createView(name, share, base);
              await reload();
              setViewId(`v:${v.id}`);
            },
          })}>Gem som visning</button>
        )}
        {saved?.mine && !changed && (
          <button className="mp-icon" aria-label={`Slet visningen ${saved.name}`} title="Slet visningen" onClick={() => {
            if (confirm(`Slet visningen „${saved.name}“?`)) void act(async () => { await mapperApi.deleteView(saved.id); setViewId("b:standard"); await reload(); });
          }}><Trash2 size={15} /></button>
        )}
      </div>
      <div className="mp-viewbar">
        <input type="search" aria-label="Filtrér listen" placeholder="Filtrér listen" value={spec.filter} maxLength={200}
          onChange={(e) => set({ filter: e.target.value })} style={{ flex: "1 1 140px", width: "auto", padding: "5px 8px", fontSize: 13 }} />
        {spec.layout === "table" && (
          <span className="mp-cols" role="group" aria-label="Kolonner">
            {COLUMNS.map((c) => (
              <label key={c.id}><input type="checkbox" checked={spec.columns.includes(c.id)}
                onChange={(e) => set({ columns: e.target.checked ? [...spec.columns, c.id] : spec.columns.filter((x) => x !== c.id) })} /> {c.label}</label>
            ))}
          </span>
        )}
      </div>
      <Alert>{error}</Alert>
      {saving && <AskDialog ask={saving} onClose={() => setSaving(null)} />}
    </div>
  );
}

