// The tree of a Mapper listing: "Alle genveje", Fastgjort, then the folders (or topics, or websites), with counts and
// the WAI-ARIA tree keyboard pattern (↑ ↓ → ← Home End Enter). In full screen (and in the narrow panel's overview) the
// open folders can show their shortcuts as leaves. Rows reuse the look of tree/TreeView (tree.css).
import { Folder, FolderOpen, Globe, List, Pin, Tag } from "lucide-react";
import { useMemo, useRef, useState, type HTMLAttributes, type KeyboardEvent, type ReactNode } from "react";
import "../tree/tree.css";
import type { MapperState, Shortcut } from "./api";
import { ALL, bareHost, hostHue, isPdf, letterOf, type Group, type ListingTree } from "./model";
import "./mapper.css";

export function GroupIcon({ g, open }: { g: Group; open?: boolean }) {
  if (g.kind === "folder") return open ? <FolderOpen size={16} className="mp-ficon" aria-hidden="true" /> : <Folder size={16} className="mp-ficon" aria-hidden="true" />;
  if (g.kind === "domain" || g.kind === "host") return <Globe size={15} className="mp-ficon" aria-hidden="true" />;
  if (g.kind === "dimension") return <span className="mp-dot" style={{ background: g.color ?? "#94a3b8" }} aria-hidden="true" />;
  return <Tag size={14} className="mp-ficon" aria-hidden="true" />;
}

export function Letter({ s }: { s: Shortcut }) {
  return <span className="mp-letter" style={{ "--mp-hue": hostHue(s.host) } as React.CSSProperties} aria-hidden="true">{letterOf(s)}</span>;
}

// ── The tree: "Alle genveje", Fastgjort, then the listing; in full screen with the shortcuts as leaves ─────────────

type Row =
  | { kind: "all"; id: string }
  | { kind: "pin"; id: string; g: Group }
  | { kind: "group"; id: string; g: Group; level: number; open: boolean; hasChildren: boolean }
  | { kind: "leaf"; id: string; s: Shortcut; key: string; level: number };

export type RowKind = "all" | "pin" | "group";

export function GroupTree({ tree, state, selected, open, full, leaves, onToggle, onOpen, onShortcut, rowAttrs, onRowKey }: {
  tree: ListingTree; state: MapperState; selected: string; open: Set<string>; full: boolean; leaves: boolean;
  onToggle: (key: string) => void; onOpen: (key: string) => void; onShortcut: (s: Shortcut, key: string) => void;
  /** Extra attributes per row (drag and drop in the wide layout); className is added to the row's own. */
  rowAttrs?: (key: string, kind: RowKind) => HTMLAttributes<HTMLDivElement>;
  /** A key on a row before the tree's own keys (⌘/Ctrl+C/X/V); true when it was handled. */
  onRowKey?: (key: string, kind: RowKind, e: KeyboardEvent) => boolean;
}) {
  const [focus, setFocus] = useState<string | null>(null);
  const refs = useRef(new Map<string, HTMLDivElement>());
  const indent = full ? 26 : 18;

  const rows = useMemo(() => {
    const out: Row[] = [{ kind: "all", id: ALL }];
    for (const g of tree.listing === "mapper" ? [...tree.groups.values()].filter((g) => g.pinned) : []) out.push({ kind: "pin", id: `pin:${g.key}`, g });
    const walk = (parent: string | null, level: number) => {
      for (const g of tree.children(parent)) {
        const direct = leaves ? tree.direct(g.key) : [];
        const hasChildren = tree.children(g.key).length > 0 || direct.length > 0;
        const isOpen = open.has(g.key) && hasChildren;
        out.push({ kind: "group", id: g.key, g, level, open: isOpen, hasChildren });
        if (isOpen) {
          walk(g.key, level + 1);
          for (const s of direct) out.push({ kind: "leaf", id: `${g.key}#${s.id}`, s, key: g.key, level: level + 1 });
        }
      }
    };
    walk(null, 1);
    if (leaves && tree.listing === "mapper") for (const s of tree.direct("root")) out.push({ kind: "leaf", id: `root#${s.id}`, s, key: ALL, level: 1 });
    return out;
  }, [tree, open, leaves]);

  const act = (row: Row) => {
    if (row.kind === "all") onOpen(ALL);
    else if (row.kind === "pin") onOpen(row.g.key);
    else if (row.kind === "leaf") onShortcut(row.s, row.key);
    else if (full) row.hasChildren ? onToggle(row.g.key) : onOpen(row.g.key);
    else onOpen(row.g.key);
  };

  const move = (id: string | undefined) => {
    if (!id) return;
    setFocus(id);
    refs.current.get(id)?.focus();
  };

  const onKey = (e: KeyboardEvent, i: number) => {
    const row = rows[i];
    if (row.kind !== "leaf" && onRowKey?.(row.kind === "all" ? ALL : row.g.key, row.kind, e)) return;
    const keys: Record<string, () => void> = {
      ArrowDown: () => move(rows[i + 1]?.id),
      ArrowUp: () => move(rows[i - 1]?.id),
      Home: () => move(rows[0]?.id),
      End: () => move(rows[rows.length - 1]?.id),
      ArrowRight: () => {
        if (row.kind === "group" && row.hasChildren && !row.open) onToggle(row.g.key);
        else if (row.kind === "group" && row.open) move(rows[i + 1]?.id);
      },
      ArrowLeft: () => {
        if (row.kind === "group" && row.open) return onToggle(row.g.key);
        if (row.kind === "group" || row.kind === "leaf") {
          const level = row.level;
          move(rows.slice(0, i).reverse().find((r) => r.kind === "group" && r.level === level - 1)?.id);
        }
      },
      Enter: () => act(row),
      " ": () => act(row),
    };
    if (keys[e.key]) {
      e.preventDefault();
      keys[e.key]();
    }
  };

  const tabStop = focus && rows.some((r) => r.id === focus) ? focus : (rows.find((r) => r.id === selected) ?? rows[0]).id;
  const pinsStart = rows.findIndex((r) => r.kind === "pin");
  const treeStart = rows.findIndex((r) => r.kind === "group");

  return (
    <div role="tree" aria-label="Mapper og genveje" className="tr-tree mp-tree">
      {rows.map((row, i) => {
        const extra = row.kind === "leaf" ? {} : rowAttrs?.(row.kind === "all" ? ALL : row.g.key, row.kind) ?? {};
        const { className: extraClass = "", ...extraAttrs } = extra;
        const common = {
          ...extraAttrs,
          ref: (el: HTMLDivElement | null) => {
            if (el) refs.current.set(row.id, el);
            else refs.current.delete(row.id);
          },
          role: "treeitem",
          tabIndex: row.id === tabStop ? 0 : -1,
          onKeyDown: (e: KeyboardEvent) => onKey(e, i),
          onFocus: () => setFocus(row.id),
          onClick: () => act(row),
        };
        const heading: ReactNode = i === pinsStart ? <div className="mp-sub" role="presentation">Fastgjort</div>
          : i === treeStart && pinsStart >= 0 ? <div className="mp-sub" role="presentation">Alle mapper</div> : null;
        if (row.kind === "all") {
          return (
            <div key={row.id} {...common} aria-level={1} aria-selected={selected === ALL}
              className={`tr-row mp-all${selected === ALL && !full ? " tr-selected" : ""} ${extraClass}`} style={{ paddingLeft: 4 }}>
              <span className="tr-chevron tr-leaf" aria-hidden="true" />
              <List size={16} className="mp-ficon" aria-hidden="true" />
              <span className="tr-label">Alle genveje</span>
              <span className="mp-count">{state.shortcuts.length}</span>
            </div>
          );
        }
        if (row.kind === "pin") {
          return (
            <div key={row.id} style={{ display: "contents" }}>
              {heading}
              <div {...common} aria-level={1} aria-selected={selected === row.g.key}
                className={`tr-row${selected === row.g.key && !full ? " tr-selected" : ""} ${extraClass}`} style={{ paddingLeft: 4 }}>
                <span className="tr-chevron tr-leaf" aria-hidden="true" />
                <Pin size={14} className="mp-ficon" aria-hidden="true" />
                <span className="tr-label">{row.g.label}</span>
                <span className="mp-count">{tree.total(row.g.key)}</span>
              </div>
            </div>
          );
        }
        if (row.kind === "leaf") {
          const { s } = row;
          return (
            <div key={row.id} {...common} aria-level={row.level} className="tr-row mp-leaf" title={s.url}
              style={{ paddingLeft: `${(row.level - 1) * indent + 4 + 24}px` }}>
              <Letter s={s} />
              <span className="tr-label">{s.title}</span>
              {isPdf(s.url) && <span className="mp-badge">PDF</span>}
              <span className="mp-host">{bareHost(s.host)}</span>
            </div>
          );
        }
        const { g } = row;
        const isSel = selected === g.key && !full;
        return (
          <div key={row.id} style={{ display: "contents" }}>
            {heading}
            <div {...common} aria-level={row.level} aria-expanded={row.hasChildren ? row.open : undefined} aria-selected={isSel}
              className={`tr-row${isSel ? " tr-selected" : ""} ${extraClass}`} style={{ paddingLeft: `${(row.level - 1) * indent + 4}px` }}>
              {row.hasChildren ? (
                <span className={`tr-chevron${row.open ? " tr-open" : ""}`} aria-hidden="true"
                  onClick={(e) => { e.stopPropagation(); onToggle(g.key); }}>
                  <svg width="14" height="14" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" /></svg>
                </span>
              ) : <span className="tr-chevron tr-leaf" aria-hidden="true" />}
              <GroupIcon g={g} open={row.open} />
              <span className="tr-label">{g.label}</span>
              {full && (
                <button type="button" className="mp-open-btn" tabIndex={-1} aria-label={`Åbn ${g.label}`}
                  onClick={(e) => { e.stopPropagation(); onOpen(g.key); }}>Åbn</button>
              )}
              <span className="mp-count">{tree.total(g.key)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

