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

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

/** Character references in the file's text and attributes, decoded to plain text (never parsed as markup). */
function decodeText(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, ref: string) => {
    if (ref[0] === "#") {
      const code = ref[1] === "x" || ref[1] === "X" ? parseInt(ref.slice(2), 16) : parseInt(ref.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    }
    return ENTITIES[ref.toLowerCase()] ?? whole;
  });
}

function readAttrs(raw: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of raw.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
    out[m[1].toLowerCase()] = decodeText(m[2] ?? m[3] ?? m[4] ?? "");
  }
  return out;
}

/** The Netscape bookmark file: <DL> lists of <DT><H3>folder</H3><DL>…</DL> and <DT><A HREF>bookmark</A>.
 * Read with a small tokenizer instead of an HTML parser: the file is someone else's markup and only these three
 * tags and their text matter, so nothing in it is ever turned into DOM. */
export function fromHtml(html: string): BmFolder[] {
  const root: BmFolder = { key: nextKey(), label: "Bogmærker", bar: false, folders: [], items: [] };
  const stack: BmFolder[] = [];
  let pending: BmFolder | null = null; // the folder whose <DL> comes next
  let open: { tag: "h3" | "a"; text: string; attrs: Record<string, string> } | null = null;
  let sawList = false;
  for (const m of html.matchAll(/<(\/?)([a-z0-9]+)\b([^>]*)>|([^<]+)/gi)) {
    const [, closing, rawTag, rawAttrs, text] = m;
    if (text !== undefined) {
      if (open) open.text += text;
      continue;
    }
    const tag = rawTag.toLowerCase();
    const into = stack[stack.length - 1] ?? root;
    if (tag === "dl") {
      if (!closing) {
        sawList = true;
        stack.push(pending ?? into);
        pending = null;
      } else {
        stack.pop();
        pending = null;
      }
    } else if (tag === "h3" || tag === "a") {
      if (!closing) {
        open = { tag, text: "", attrs: readAttrs(rawAttrs) };
      } else if (open?.tag === tag) {
        const label = decodeText(open.text).trim();
        if (tag === "h3") {
          const sub: BmFolder = {
            key: nextKey(), label: label || "Uden navn",
            bar: open.attrs.personal_toolbar_folder === "true", folders: [], items: [],
          };
          into.folders.push(sub);
          pending = sub;
        } else if (open.attrs.href) {
          into.items.push({ url: open.attrs.href, title: label });
        }
        open = null;
      }
    }
  }
  if (!sawList) throw new Error("Filen ligner ikke en eksport af bogmærker (.html)");
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
