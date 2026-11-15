// Mapper in a narrow side panel (about 360–420 px): drill-down instead of tree and content side by side.
// Header: back to the parent, the folder's title and counts, search, the tree overview and views (a dot when the view
// is changed). Under it the listing (Mapper / Emner / Websteder). Folders are 52 px rows, shortcuts 60 px rows with a
// 44 px "open in a new tab" button; in a Mapper folder a bottom bar saves the current page there. Views, the preview and
// the tree overview are sheets and layers over the content. Every touch target is at least 44 px.
import {
  BookmarkPlus, ChevronLeft, ChevronRight, ClipboardPaste, Copy, Ellipsis, ExternalLink, FolderInput, FolderPlus, FolderTree, Link, List, Pencil,
  Pin, PinOff, Plus, Scissors, Search, Settings2, SlidersHorizontal, Trash2, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, errorText } from "../api";
import type { PageInfo } from "../bridge/extension";
import { Alert } from "../ui";
import { mapperApi, type MapperState, type SavedView, type Shortcut, type ViewSpec } from "./api";
import { ClipPill, type ClipItem, type MapperClipboard } from "./clipboard";
import { AddShortcut, AskDialog, EditShortcut, FolderPicker, FolderProperties, Sheet, Toast, type Ask } from "./dialogs";
import { GroupIcon, GroupTree, Letter } from "./GroupTree";
import {
  ALL, BUILTIN, LISTINGS, SORTS, bareHost, descendantsOf, isPdf, matches, normalizeSpec, placedIn, sameSpec, sortShortcuts, treeFor, type Group, type Listing,
} from "./model";
import { openInTab, Preview, previewable } from "./Preview";

const NEW_DAYS = 7;

export type NarrowProps = {
  state: MapperState;
  views: SavedView[];
  reload: () => Promise<void>;
  inExtension: boolean;
  tab: PageInfo | null;
  listing: Listing;
  setListing: (l: Listing) => void;
  spec: ViewSpec;
  setSpec: (s: ViewSpec) => void;
  viewId: string;
  setViewId: (id: string) => void;
  onImport: () => void;
  /** A folder to open first (after an import). */
  start?: string | null;
  /** Copy / cut / paste, shared with the wide layout (clipboard.tsx). */
  cb: MapperClipboard;
  toast: string | null;
  setToast: (text: string | null) => void;
};

/** What the "…" sheet of a row acts on. */
type Acting = { kind: "shortcut"; s: Shortcut } | { kind: "folder"; g: Group };

function shortUrl(url: string): string {
  return url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
}

function isNew(s: Shortcut): boolean {
  return Date.now() - new Date(s.added_at).getTime() < NEW_DAYS * 86_400_000;
}

export function MapperNarrow(p: NarrowProps) {
  const { state, listing, spec } = p;
  const tree = useMemo(() => treeFor(listing, state), [state, listing]);
  const [loc, setLoc] = useState<string | null>(p.start ?? null);   // null: the listing's root · ALL · a group key
  const [searching, setSearching] = useState(false);
  const [q, setQ] = useState("");
  const [layer, setLayer] = useState(false);
  const [leaves, setLeaves] = useState(true);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [sheet, setSheet] = useState(false);
  const [preview, setPreview] = useState<Shortcut | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [editing, setEditing] = useState<{ s: Shortcut; from: number | null } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, setChecked] = useState(0);
  const onChecked = useCallback(() => setChecked((n) => n + 1), []);
  const [acting, setActing] = useState<Acting | null>(null);
  const [picking, setPicking] = useState<ClipItem | null>(null);
  const [props, setProps] = useState<number | null | undefined>(undefined);
  const [adding, setAdding] = useState(false);
  const clearToast = useCallback(() => p.setToast(null), [p]);
  const { cb } = p;

  useEffect(() => {
    if (loc && loc !== ALL && !tree.groups.has(loc)) setLoc(null);
  }, [tree, loc]);

  const listingLabel = LISTINGS.find((l) => l.id === listing)!.label;
  const group = loc && loc !== ALL ? tree.groups.get(loc) ?? null : null;
  const folderId = listing === "mapper" ? (group?.folderId ?? null) : null;
  const inFolderPlace = listing === "mapper" && (loc === null || group?.folderId !== undefined);
  const order = new Map(state.shortcuts.map((s, i) => [s.id, i]));
  const query = searching ? q.trim() : "";
  const allOfListing = listing === "emner" ? state.shortcuts.filter((s) => s.tags.length) : state.shortcuts;
  const base = query ? state.shortcuts.filter((s) => matches(s, query))
    : loc === ALL ? allOfListing : group ? tree.direct(group.key) : listing === "mapper" ? tree.direct("root") : [];
  const folder = group?.folderId !== undefined ? state.folders.find((f) => f.id === group.folderId) ?? null : null;
  const items = sortShortcuts(base.filter((s) => matches(s, spec.filter)), spec.sort, order,
    !query && listing === "mapper" && loc !== ALL ? { id: folder?.id ?? null, sort: folder ? folder.shortcut_sort : state.root?.shortcut_sort ?? null } : null);
  // The folder's own layout (Egenskaber) unless the view is changed here; no table in a narrow panel.
  const folderLayout = !query && listing === "mapper" && loc !== ALL ? (folder ? folder.layout : loc === null ? state.root?.layout ?? null : null) : null;
  const layout = (folderLayout ?? spec.layout) === "grid" ? "grid" : "list";
  const pasteTo: number | null | undefined = inFolderPlace && !query ? folderId : undefined;  const subgroups = query || loc === ALL ? [] : tree.children(group?.key ?? null);
  const pinned = listing === "mapper" ? [...tree.groups.values()].filter((g) => g.pinned) : [];
  const trail = group ? tree.trail(group.key) : [];
  const parent = group ? (trail[trail.length - 1] ?? null) : null;
  const builtin = BUILTIN.find((b) => b.id === p.viewId);
  const saved = p.views.find((v) => `v:${v.id}` === p.viewId);
  const changed = !sameSpec(spec, builtin?.spec ?? (saved ? normalizeSpec(saved.spec) : BUILTIN[0].spec));
  const nouns = listing === "mapper" ? ["undermappe", "undermapper", "mappe", "mapper"]
    : listing === "emner" ? ["underemne", "underemner", "emne", "emner"] : ["vært", "værter", "websted", "websteder"];
  const noun = (n: number) => `${n} ${nouns[(group ? 0 : 2) + (n === 1 ? 0 : 1)]}`;
  const folderLabel = (id: number | null) => (id === null ? "Mapper" : state.folders.find((f) => f.id === id)?.label ?? "mappen");

  const title = query ? "Søgning" : loc === ALL ? "Alle genveje" : group ? group.label : listingLabel;
  const subtitle = query ? `${items.length} genveje` : loc === ALL ? `${allOfListing.length} genveje`
    : group ? `${subgroups.length ? `${noun(subgroups.length)} · ` : ""}${tree.total(group.key)} genveje i alt`
    : `${noun(subgroups.length)} · ${allOfListing.length} genveje i alt`;

  const go = (key: string | null) => {
    setLoc(key);
    setSearching(false);
    setQ("");
    window.scrollTo?.(0, 0);
  };
  const back = () => go(loc === ALL || !group ? null : parent?.key ?? null);

  const run = async (fn: () => Promise<string | void>) => {
    setError(null);
    setNotice(null);
    try {
      const text = await fn();
      if (text) setNotice(text);
      await p.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const saveTab = () => p.tab && run(async () => {
    const r = await mapperApi.addShortcut(p.tab!.url, p.tab!.title, folderId);
    return r.added ? `„${r.title}“ er gemt i ${folderLabel(folderId)}.` : `„${r.title}“ ligger allerede i ${folderLabel(folderId)}.`;
  });
  // Where there is no current tab (/m, a plain browser): the full form (address, title, folder, Emner).
  const newShortcut = () => setAdding(true);
  const newFolder = () => setAsk({
    title: group ? "Ny undermappe" : "Ny mappe", label: "Navn", value: "", confirm: "Opret",
    onSubmit: async (label) => {
      await mapperApi.createFolder(label, folderId);
      await p.reload();
    },
  });
  const removeShortcut = (s: Shortcut) => {
    const inFolder = listing === "mapper" && !query && loc !== ALL;
    const text = inFolder ? `Fjern „${s.title}“ fra ${folderLabel(folderId)}?` : `Fjern „${s.title}“ fra alle dine mapper? Emner på genvejen bliver.`;
    if (!confirm(text)) return;
    setPreview(null);
    void run(async () => {
      await mapperApi.removeShortcut(s.id, inFolder ? folderId : null, !inFolder);
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
    setSheet(false);
    go(g.parent);
    return `„${g.label}“ er slettet.`;
  });
  const showPreview = (s: Shortcut) => {
    setExpanded(false);
    setPreview(s);
  };

  const row = (s: Shortcut) => (
    <div key={s.id} className="mpn-item" role="button" tabIndex={0} title={s.url}
      onClick={() => showPreview(s)} onKeyDown={(e) => e.key === "Enter" && e.target === e.currentTarget && showPreview(s)}>
      <Letter s={s} />
      <span className="mpn-text">
        <span className="mpn-title"><strong>{s.title}</strong>
          {isPdf(s.url) && <span className="mp-badge">PDF</span>}
          {isNew(s) && <span className="mp-badge mpn-new">Ny</span>}
          {previewable(s) && <span className="mp-hint" aria-hidden="true">Forhåndsvis</span>}
        </span>
        <span className="mpn-sub">{query || loc === ALL ? placedIn(s, state).join(" · ") || bareHost(s.host) : shortUrl(s.url)}</span>
      </span>
      <button className="mpn-ib" onClick={(e) => { e.stopPropagation(); openInTab(s.url); }} aria-label={`Åbn ${s.title} i ny fane`}>
        <ExternalLink size={18} />
      </button>
      <button className="mpn-ib" onClick={(e) => { e.stopPropagation(); setActing({ kind: "shortcut", s }); }} aria-label={`Handlinger for ${s.title}`}>
        <Ellipsis size={18} />
      </button>
    </div>
  );

  const card = (s: Shortcut) => (
    <div key={s.id} className="mp-card mpn-card" role="button" tabIndex={0} title={s.url}
      onClick={() => showPreview(s)} onKeyDown={(e) => e.key === "Enter" && e.target === e.currentTarget && showPreview(s)}>
      <Letter s={s} />
      <strong>{s.title}</strong>
      <span className="tiny muted">{bareHost(s.host)}{isPdf(s.url) ? " · PDF" : ""}</span>
    </div>
  );

  const groupRow = (g: Group, label?: string, count?: number) => (
    <div key={g.key} className="mpn-folder-row">
      <button className="mpn-folder" onClick={() => go(g.key)}>
        <GroupIcon g={g} />
        <span className="mpn-label">{label ?? g.label}</span>
        <span className="mp-count">{count ?? tree.total(g.key)}</span>
        <ChevronRight size={18} aria-hidden="true" />
      </button>
      {g.folderId !== undefined && (
        <button className="mpn-ib" onClick={() => setActing({ kind: "folder", g })} aria-label={`Handlinger for mappen ${g.label}`}><Ellipsis size={18} /></button>
      )}
    </div>
  );

  const shortcutItem = (s: Shortcut): ClipItem => ({ kind: "shortcut", id: s.id, label: s.title,
    from: listing === "mapper" && !query && loc !== ALL ? folderId : s.folders.includes(null) ? null : s.folders[0] ?? null });
  const folderItem = (g: Group): ClipItem => ({ kind: "folder", id: g.folderId!, label: g.label,
    from: state.folders.find((f) => f.id === g.folderId)?.parent_id ?? null });
  const sheetBtn = (icon: React.ReactNode, label: string, fn: () => void, danger = false) => (
    <button className={`btn ${danger ? "btn-danger" : "btn-outline"} btn-block mpn-touch mpn-left`} onClick={() => { setActing(null); fn(); }}>{icon} {label}</button>
  );

  return (
    <div className="mpn">
      <header className="mpn-head">
        {searching ? (
          <>
            <label className="mp-search" style={{ flex: 1 }}>
              <Search size={15} aria-hidden="true" />
              <span className="sr-only">Søg i alle genveje</span>
              <input type="search" autoFocus placeholder="Søg i alle genveje" value={q} onChange={(e) => setQ(e.target.value)} />
            </label>
            <button className="btn btn-ghost mpn-touch" onClick={() => { setSearching(false); setQ(""); }}>Annullér</button>
          </>
        ) : (
          <>
            {loc !== null && (
              <button className="mpn-ib" onClick={back} aria-label={`Tilbage til ${loc === ALL || !parent ? listingLabel : parent.label}`}
                title={`Tilbage til ${loc === ALL || !parent ? listingLabel : parent.label}`}>
                <ChevronLeft size={20} />
              </button>
            )}
            <div className="mpn-titles">
              <strong>{title}</strong>
              <span>{subtitle}</span>
            </div>
            <button className="mpn-ib" onClick={() => setSearching(true)} aria-label="Søg"><Search size={18} /></button>
            <button className="mpn-ib" onClick={() => setLayer(true)} aria-label="Oversigt over træet"><FolderTree size={18} /></button>
            <button className="mpn-ib mpn-dotted" onClick={() => setSheet(true)} aria-label={changed ? "Visning og sortering (ændret)" : "Visning og sortering"}>
              <SlidersHorizontal size={18} />
              {changed && <span className="mpn-dot" aria-hidden="true" />}
            </button>
          </>
        )}
      </header>

      <div className="segmented mpn-seg" role="tablist" aria-label="Visning af træet">
        {LISTINGS.map((l) => (
          <button key={l.id} role="tab" aria-selected={listing === l.id} className={listing === l.id ? "active" : ""}
            onClick={() => { p.setListing(l.id); go(null); }}>{l.label}</button>
        ))}
      </div>

      {!query && trail.length > 0 && (
        <nav className="mpn-chips" aria-label="Brødkrummer">
          <button className="chip" onClick={() => go(null)}>{listingLabel}</button>
          {trail.map((t) => <button key={t.key} className="chip" onClick={() => go(t.key)}>{t.label}</button>)}
          <span className="chip mpn-chip-here" aria-current="page">{group!.label}</span>
        </nav>
      )}

      {!query && loc === null && pinned.length > 0 && (
        <div className="mpn-pins" aria-label="Fastgjort">
          <span className="mp-sub">Fastgjort</span>
          <div className="mpn-chips">
            {pinned.map((g) => <button key={g.key} className="chip mpn-touch-chip" onClick={() => go(g.key)}><Pin size={12} aria-hidden="true" /> {g.label}</button>)}
          </div>
        </div>
      )}

      <div className="mpn-list">
        {!query && loc === null && (
          <button className="mpn-folder mp-all" onClick={() => go(ALL)}>
            <List size={16} className="mp-ficon" aria-hidden="true" />
            <span className="mpn-label">Alle genveje</span>
            <span className="mp-count">{allOfListing.length}</span>
            <ChevronRight size={18} aria-hidden="true" />
          </button>
        )}
        {subgroups.map((g) => groupRow(g))}
        {inFolderPlace && !query && (
          <button className="mpn-folder mpn-add" onClick={newFolder}><FolderPlus size={16} aria-hidden="true" /><span className="mpn-label">{group ? "Ny undermappe" : "Ny mappe"}</span></button>
        )}
      </div>

      {items.length > 0 && (layout === "grid" ? <div className="mp-grid mpn-grid">{items.map(card)}</div> : <div className="mpn-list">{items.map(row)}</div>)}
      {!items.length && (query || loc === ALL || group) && (
        <p className="small muted" style={{ margin: "4px 2px" }}>{query ? "Ingen genveje passer." : spec.filter ? "Ingen genveje passer til filteret." : "Ingen genveje her endnu."}</p>
      )}
      {listing === "emner" && !tree.groups.size && <p className="small muted" style={{ margin: "4px 2px" }}>Ingen af dine genveje har emner endnu. Giv dem emner under „Ret“.</p>}

      <Alert kind="success">{notice}</Alert>
      <Alert>{error}</Alert>

      {((inFolderPlace && !query) || cb.clip) && (
        <div className="mpn-bar">
          <ClipPill cb={cb} target={pasteTo} narrow />
          {inFolderPlace && !query && <div className="mpn-bar-row">
          {p.inExtension ? (
            <button className="btn btn-primary mpn-touch" style={{ flex: 1 }} disabled={!p.tab?.readable} onClick={saveTab}
              title={p.tab?.readable ? p.tab.title : "Kun almindelige websider (http og https)"}>
              <BookmarkPlus size={16} /> Gem denne side her
            </button>
          ) : (
            <button className="btn btn-primary mpn-touch" style={{ flex: 1 }} onClick={newShortcut}><Plus size={16} /> Tilføj genvej</button>
          )}
          {group?.folderId !== undefined && (
            <button className={`mpn-ib mpn-pin${group.pinned ? " active" : ""}`} aria-pressed={!!group.pinned}
              aria-label={group.pinned ? `Frigør ${group.label}` : `Fastgør ${group.label}`}
              onClick={() => run(async () => { await mapperApi.pin(group.folderId!, !group.pinned); })}>
              {group.pinned ? <PinOff size={18} /> : <Pin size={18} />}
            </button>
          )}
          </div>}
        </div>
      )}
      <Toast text={p.toast} onDone={clearToast} />

      {acting && (
        <Sheet title={acting.kind === "shortcut" ? acting.s.title : `Mappen „${acting.g.label}“`} onClose={() => setActing(null)}>
          <div className="stack mpn-sheet-body" style={{ "--gap": "6px" } as React.CSSProperties}>
            {acting.kind === "shortcut" ? (() => {
              const s = acting.s;
              const inFolder = listing === "mapper" && !query && loc !== ALL;
              return (
                <>
                  {sheetBtn(<Copy size={16} />, "Kopiér", () => cb.copy(shortcutItem(s)))}
                  {sheetBtn(<Scissors size={16} />, "Klip", () => cb.cut(shortcutItem(s)))}
                  {sheetBtn(<FolderInput size={16} />, "Flyt til …", () => setPicking(shortcutItem(s)))}
                  {sheetBtn(<Link size={16} />, "Kopiér adresse", () => void navigator.clipboard.writeText(s.url).then(() => p.setToast("Adressen er kopieret."), () => setError("Adressen kunne ikke kopieres")))}
                  {sheetBtn(<Pencil size={16} />, "Ret …", () => setEditing({ s, from: shortcutItem(s).from }))}
                  {sheetBtn(<Trash2 size={16} />, inFolder ? "Fjern fra mappen" : "Fjern fra alle mapper", () => removeShortcut(s), true)}
                </>
              );
            })() : (
              <>
                {sheetBtn(<Settings2 size={16} />, "Egenskaber", () => setProps(acting.g.folderId!))}
                {sheetBtn(<Copy size={16} />, "Kopiér mappe", () => cb.copy(folderItem(acting.g)))}
                {sheetBtn(<Scissors size={16} />, "Klip mappe", () => cb.cut(folderItem(acting.g)))}
                {sheetBtn(<FolderInput size={16} />, "Flyt til …", () => setPicking(folderItem(acting.g)))}
                {cb.clip && sheetBtn(<ClipboardPaste size={16} />, `Indsæt i „${acting.g.label}“`, () => void cb.paste(acting.g.folderId!))}
                {sheetBtn(<Trash2 size={16} />, "Slet mappe", () => deleteFolder(acting.g), true)}
              </>
            )}
          </div>
        </Sheet>
      )}
      {picking && (
        <FolderPicker narrow state={state} title={`Flyt „${picking.label}“ til …`} current={picking.from}
          exclude={picking.kind === "folder" ? descendantsOf(state, picking.id) : undefined}
          onClose={() => setPicking(null)} onPick={(to) => { const it = picking; setPicking(null); void cb.transfer(it, to, false); }} />
      )}
      {adding && (
        <AddShortcut narrow state={state} folderId={folderId} onClose={() => setAdding(false)}
          onSaved={async (text) => { setAdding(false); p.setToast(text); await p.reload(); }} />
      )}
      {props !== undefined && (
        <FolderProperties narrow state={state} folderId={props} onClose={() => setProps(undefined)}
          onSaved={async (text) => { setProps(undefined); p.setToast(text); await p.reload(); }} />
      )}

      {sheet && (
        <Sheet title="Visning og sortering" onClose={() => setSheet(false)}>
          <ViewsSheet {...p} group={listing === "mapper" ? group : null} atRoot={listing === "mapper" && loc === null} onClose={() => setSheet(false)}
            onProps={(id) => setProps(id)} onFolderActions={(g) => setActing({ kind: "folder", g })}
            onDelete={deleteFolder} onNewShortcut={newShortcut} onAsk={setAsk} />
        </Sheet>
      )}

      {preview && (
        <div className="mpn-backdrop" role="presentation" onClick={() => setPreview(null)}>
          <div className={`mpn-sheet mpn-preview${expanded ? " mpn-full" : ""}`} onClick={(e) => e.stopPropagation()}>
            <Preview shortcut={preview} variant="sheet" placement="below" onPlacement={() => undefined} expanded={expanded} onExpanded={setExpanded}
              onClose={() => setPreview(null)} onChecked={onChecked}
              footer={(
                <div className="row" style={{ "--gap": "6px" } as React.CSSProperties}>
                  <button className="btn btn-outline mpn-touch" style={{ flex: 1 }}
                    onClick={() => { setEditing({ s: preview, from: listing === "mapper" && loc !== ALL ? folderId : preview.folders[0] ?? null }); setPreview(null); }}>
                    <Pencil size={15} /> Ret
                  </button>
                  <button className="btn btn-outline mpn-touch" style={{ flex: 1 }} onClick={() => removeShortcut(preview)}><Trash2 size={15} /> Fjern</button>
                </div>
              )} />
          </div>
        </div>
      )}

      {layer && (
        <div className="mpn-layer" role="dialog" aria-modal="true" aria-label="Oversigt over træet">
          <header className="mpn-head">
            <div className="mpn-titles"><strong>Oversigt</strong><span>{listingLabel}</span></div>
            <button className="mpn-ib" onClick={() => setLayer(false)} aria-label="Luk oversigten"><X size={20} /></button>
          </header>
          <div className="mp-toolbar">
            <button className="btn btn-outline btn-sm mpn-touch" onClick={() => setOpen(new Set(tree.groups.keys()))}>Fold alt ud</button>
            <button className="btn btn-outline btn-sm mpn-touch" onClick={() => setOpen(new Set())}>Fold sammen</button>
            <label className="mpn-touch"><input type="checkbox" checked={leaves} onChange={(e) => setLeaves(e.target.checked)} /> Vis genveje</label>
          </div>
          <GroupTree tree={tree} state={state} selected={loc ?? ""} open={open} full={false} leaves={leaves}
            onToggle={(k) => setOpen((o) => {
              const n = new Set(o);
              if (n.has(k)) n.delete(k);
              else n.add(k);
              return n;
            })}
            onOpen={(k) => { setLayer(false); go(k); }}
            onShortcut={(s, k) => { setLayer(false); go(k === ALL ? null : k); showPreview(s); }} />
        </div>
      )}

      {ask && <AskDialog ask={ask} onClose={() => setAsk(null)} />}
      {editing && (
        <EditShortcut item={editing.s} from={editing.from} state={state} onClose={() => setEditing(null)}
          onSaved={async (text) => { setEditing(null); setNotice(text); await p.reload(); }} />
      )}
    </div>
  );
}

function ViewsSheet(p: NarrowProps & {
  group: Group | null; atRoot: boolean; onClose: () => void; onProps: (id: number | null) => void; onFolderActions: (g: Group) => void;
  onDelete: (g: Group) => void; onNewShortcut: () => void; onAsk: (a: Ask) => void;
}) {
  const { spec, setSpec, views, viewId } = p;
  const [error, setError] = useState<string | null>(null);
  const builtin = BUILTIN.find((b) => b.id === viewId);
  const saved = views.find((v) => `v:${v.id}` === viewId);
  const changed = !sameSpec(spec, builtin?.spec ?? (saved ? normalizeSpec(saved.spec) : BUILTIN[0].spec));
  const groups: { label: string; items: { id: string; name: string; note?: string }[] }[] = [
    { label: "Indbyggede", items: BUILTIN.map((b) => ({ id: b.id, name: b.name })) },
    { label: "Delt med teamet", items: views.filter((v) => v.shared).map((v) => ({ id: `v:${v.id}`, name: v.name, note: v.mine ? "din" : v.owner_name ?? undefined })) },
    { label: "Mine visninger", items: views.filter((v) => v.mine && !v.shared).map((v) => ({ id: `v:${v.id}`, name: v.name })) },
  ];
  const layout = spec.layout === "table" ? "list" : spec.layout;  // no table in a narrow panel

  return (
    <div className="stack mpn-sheet-body" style={{ "--gap": "14px" } as React.CSSProperties}>
      <div className="stack" style={{ "--gap": "2px" } as React.CSSProperties}>
        {groups.filter((g) => g.items.length).map((g) => (
          <div key={g.label} role="radiogroup" aria-label={g.label}>
            <span className="mp-sub">{g.label}</span>
            {g.items.map((v) => (
              <label key={v.id} className="mpn-radio">
                <input type="radio" name="mpn-view" checked={viewId === v.id} onChange={() => p.setViewId(v.id)} />
                <span>{v.name}{v.note ? <span className="tiny muted"> · {v.note}</span> : null}</span>
                {viewId === v.id && changed && <span className="pill mp-changed">Ændret</span>}
              </label>
            ))}
          </div>
        ))}
      </div>
      <div>
        <span className="mp-sub">Layout</span>
        <div className="segmented" role="group" aria-label="Layout">
          {(["list", "grid"] as const).map((l) => (
            <button key={l} className={`mpn-touch${layout === l ? " active" : ""}`} aria-pressed={layout === l} onClick={() => setSpec({ ...spec, layout: l })}>
              {l === "list" ? "Liste" : "Kort"}
            </button>
          ))}
        </div>
      </div>
      <div role="radiogroup" aria-label="Sortering">
        <span className="mp-sub">Sortering</span>
        {SORTS.map((s) => (
          <label key={s.id} className="mpn-radio">
            <input type="radio" name="mpn-sort" checked={spec.sort === s.id} onChange={() => setSpec({ ...spec, sort: s.id })} /> <span>{s.label}</span>
          </label>
        ))}
      </div>
      <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
        {changed && saved?.mine && (
          <button className="btn btn-outline btn-block mpn-touch" onClick={async () => {
            try {
              await mapperApi.updateView(saved.id, { spec });
              await p.reload();
            } catch (e) {
              setError(errorText(e));
            }
          }}>Gem ændringer i „{saved.name}“</button>
        )}
        {changed && (
          <button className="btn btn-outline btn-block mpn-touch" onClick={() => p.onAsk({
            title: "Gem som visning", label: "Navn", value: "", confirm: "Gem", share: false,
            onSubmit: async (name, share) => {
              const v = await mapperApi.createView(name, share, spec);
              await p.reload();
              p.setViewId(`v:${v.id}`);
            },
          })}>Gem som visning</button>
        )}
        {p.group?.folderId !== undefined && p.group && (
          <>
            <span className="mp-sub">Mappen „{p.group.label}“</span>
            <button className="btn btn-outline btn-block mpn-touch" onClick={() => { p.onClose(); p.onProps(p.group!.folderId!); }}><Settings2 size={15} /> Egenskaber</button>
            <button className="btn btn-outline btn-block mpn-touch" onClick={() => { p.onClose(); p.onFolderActions(p.group!); }}><Copy size={15} /> Kopiér, klip eller flyt mappen …</button>
            <button className="btn btn-outline btn-block mpn-touch" onClick={() => { p.onClose(); p.onNewShortcut(); }}><Plus size={15} /> Tilføj genvej …</button>
            <button className="btn btn-danger btn-block mpn-touch" onClick={() => p.onDelete(p.group!)}><Trash2 size={15} /> Slet mappe</button>
          </>
        )}
        {p.atRoot && (
          <button className="btn btn-outline btn-block mpn-touch" onClick={() => { p.onClose(); p.onProps(null); }}><Settings2 size={15} /> Egenskaber for Mapper</button>
        )}
        <button className="btn btn-ghost btn-block mpn-touch" onClick={() => { p.onClose(); p.onImport(); }}>Importér favoritter …</button>
      </div>
      <Alert>{error}</Alert>
    </div>
  );
}
