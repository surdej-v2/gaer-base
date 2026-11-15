// Browser favourites for Mapper's import: from the extension (chrome.bookmarks, read in the side panel and passed over
// the bridge) or from an exported bookmarks file (the Netscape HTML format every browser writes), parsed here in the
// browser. Nothing is sent before the user has chosen folders; the import is one call (POST /api/mapper/import).
import type { ImportBody, ImportFolder, ImportItem } from "./api";

export type BmItem = { url: string; title: string };
export type BmFolder = { key: string; label: string; bar: boolean; folders: BmFolder[]; items: BmItem[] };

/** What the side panel sends for GET_BOOKMARKS: chrome.bookmarks.getTree() trimmed to what we need. */
export type ChromeNode = { id: string; title: string; url?: string; folderType?: string; children?: ChromeNode[] };

let seq = 0;
const nextKey = () => `b${++seq}`;

export function fromChrome(tree: ChromeNode[]): BmFolder[] {
  const walk = (n: ChromeNode): BmFolder => ({
    key: nextKey(),
    label: n.title || "Uden navn",
    bar: n.folderType === "bookmarks-bar" || n.id === "1",
    folders: (n.children ?? []).filter((c) => !c.url).map(walk),
    items: (n.children ?? []).filter((c) => c.url).map((c) => ({ url: c.url!, title: c.title })),
  });
  // getTree() answers [root]; the root's children are Bogmærkelinje, Andre bogmærker, Mobilbogmærker.
  const tops = tree.length === 1 && !tree[0].url && tree[0].children ? tree[0].children : tree;
  return tops.filter((n) => !n.url).map(walk).filter((f) => countItems(f) > 0 || f.folders.length > 0);
}

/** The Netscape bookmark file: <DL> lists of <DT><H3>folder</H3><DL>…</DL> and <DT><A HREF>bookmark</A>. */
export function fromHtml(html: string): BmFolder[] {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const top = doc.querySelector("dl");
  if (!top) throw new Error("Filen ligner ikke en eksport af bogmærker (.html)");
  const parseList = (dl: Element, into: BmFolder) => {
    for (const dt of Array.from(dl.children)) {
      if (dt.tagName === "P") {
        parseList(dt, into); // some writers put the items inside the <p>
        continue;
      }
      if (dt.tagName !== "DT") continue;
      const h3 = dt.querySelector(":scope > h3");
      const a = dt.querySelector(":scope > a");
      if (h3) {
        const sub: BmFolder = {
          key: nextKey(), label: (h3.textContent ?? "").trim() || "Uden navn",
          bar: h3.getAttribute("personal_toolbar_folder") === "true", folders: [], items: [],
        };
        // The folder's list is inside the <dt>, or (other parsers) right after it.
        const list = dt.querySelector(":scope > dl") ?? (dt.nextElementSibling?.tagName === "DL" ? dt.nextElementSibling : null);
        if (list) parseList(list, sub);
        into.folders.push(sub);
      } else if (a?.getAttribute("href")) {
        into.items.push({ url: a.getAttribute("href")!, title: (a.textContent ?? "").trim() });
      }
    }
  };
  const root: BmFolder = { key: nextKey(), label: "Bogmærker", bar: false, folders: [], items: [] };
  parseList(top, root);
  // Loose bookmarks at the top (Chrome's "Andre bogmærker" end up there) get a folder of their own.
  const out = [...root.folders];
  if (root.items.length) out.push({ key: nextKey(), label: "Andre bogmærker", bar: false, folders: [], items: root.items });
  if (!out.length) throw new Error("Der er ingen bogmærker i filen");
  return out;
}

export function countItems(f: BmFolder): number {
  return f.items.length + f.folders.reduce((n, c) => n + countItems(c), 0);
}

export function walkFolders(folders: BmFolder[], fn: (f: BmFolder, parent: BmFolder | null) => void, parent: BmFolder | null = null) {
  for (const f of folders) {
    fn(f, parent);
    walkFolders(f.folders, fn, f);
  }
}

/** Roughly the server's normalisation (scheme and host lower case, no fragment, no tracking, no trailing slash),
 * enough to count duplicates before the import. The server decides. */
export function roughUrl(raw: string): string | null {
  try {
    const u = new URL(raw.trim());
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    u.hash = "";
    for (const k of [...u.searchParams.keys()]) if (k.toLowerCase().startsWith("utm_") || ["fbclid", "gclid"].includes(k.toLowerCase())) u.searchParams.delete(k);
    let s = u.toString();
    if (!u.search && s.endsWith("/")) s = s.slice(0, -1);
    return s;
  } catch {
    return null;
  }
}

export type Plan = { body: ImportBody; items: number; folders: number; duplicates: number; invalid: number };

export type PlanOptions = {
  selected: Set<string>;
  keepStructure: boolean;
  skipDuplicates: boolean;
  target: "new" | "top";
  targetLabel: string;
  pinBar: boolean;
  existing: Set<string>;
};

/** The chosen folders → the import call. A chosen folder whose parent is not chosen hangs on the nearest chosen
 * ancestor (or the target). Only the chosen folders' own bookmarks go along. */
export function plan(tree: BmFolder[], o: PlanOptions): Plan {
  const folders: ImportFolder[] = [];
  const items: ImportItem[] = [];
  const seen = new Set(o.existing);
  let duplicates = 0;
  let invalid = 0;
  const walk = (list: BmFolder[], parent: string | null) => {
    for (const f of list) {
      const on = o.selected.has(f.key);
      if (on) {
        folders.push({ key: f.key, parent, label: f.label, bar: f.bar });
        for (const it of f.items) {
          const norm = roughUrl(it.url);
          if (!norm) {
            invalid++;
            continue;
          }
          if (seen.has(norm)) {
            duplicates++;
            if (o.skipDuplicates) continue;
          }
          seen.add(norm);
          items.push({ folder: f.key, url: it.url, title: it.title });
        }
      }
      walk(f.folders, on ? f.key : parent);
    }
  };
  walk(tree, null);
  return {
    body: {
      target: o.target, target_label: o.targetLabel.trim() || "Fra browseren", keep_structure: o.keepStructure,
      skip_duplicates: o.skipDuplicates, pin_bar: o.pinBar, folders: o.keepStructure ? folders : [],
      items: o.keepStructure ? items : items.map((i) => ({ ...i, folder: null })),
    },
    items: items.length,
    folders: o.keepStructure ? folders.length : 0,
    duplicates,
    invalid,
  };
}

/** The tree the import will make, for the live preview on "Placering". */
export type PreviewNode = { label: string; count: number; pinned: boolean; children: PreviewNode[] };

export function previewTree(p: Plan): PreviewNode[] {
  const nodes = new Map<string, PreviewNode>();
  const tops: PreviewNode[] = [];
  for (const f of p.body.folders) {
    const n: PreviewNode = { label: f.label, count: 0, pinned: !!f.bar && p.body.pin_bar, children: [] };
    nodes.set(f.key, n);
    (f.parent && nodes.get(f.parent)?.children || tops).push(n);
  }
  let loose = 0;
  for (const i of p.body.items) {
    const n = i.folder ? nodes.get(i.folder) : undefined;
    if (n) n.count++;
    else loose++;
  }
  if (p.body.target === "new") return [{ label: p.body.target_label || "Fra browseren", count: loose, pinned: false, children: tops }];
  return loose ? [...tops, { label: "(øverst i Mapper)", count: loose, pinned: false, children: [] }] : tops;
}
