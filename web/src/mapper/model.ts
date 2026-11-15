// Mapper's model, computed in the browser from GET /api/mapper: the three listings of the same shortcuts
//   mapper     the user's own folders (private nodes under ~/mapper)
//   emner      the other tags the shortcuts have (shared dimensions, the rest of the private tree)
//   websteder  domain → host, worked out from the addresses
// plus recursive counts, search, sorting and the built-in views.
import type { ChildSort, Column, Folder, MapperState, Shortcut, ShortcutSort, SortKey, ViewSpec } from "./api";

export type Listing = "mapper" | "emner" | "websteder";
export const LISTINGS: { id: Listing; label: string }[] = [
  { id: "mapper", label: "Mapper" },
  { id: "emner", label: "Emner" },
  { id: "websteder", label: "Websteder" },
];

/** "Alle genveje", the row at the top of every listing. */
export const ALL = "all";

export type Group = {
  key: string;
  label: string;
  parent: string | null;
  kind: "folder" | "dimension" | "tag" | "domain" | "host";
  folderId?: number;
  color?: string | null;
  pinned?: boolean;
};

export type ListingTree = {
  listing: Listing;
  groups: Map<string, Group>;
  children: (key: string | null) => Group[];
  /** Shortcuts directly in a group ("root" = the top level of Mapper). */
  direct: (key: string) => Shortcut[];
  /** Distinct shortcuts in the group and everything below it. */
  total: (key: string) => number;
  /** The group's ancestors, top first. */
  trail: (key: string) => Group[];
};

const collator = new Intl.Collator("da", { sensitivity: "base", numeric: true });

type Order = (a: Group, b: Group) => number;

function build(listing: Listing, groups: Group[], direct: Map<string, Shortcut[]>, orderFor?: (parent: string | null) => Order): ListingTree {
  const byKey = new Map(groups.map((g) => [g.key, g]));
  const kids = new Map<string | null, Group[]>();
  for (const g of groups) {
    const parent = g.parent && byKey.has(g.parent) ? g.parent : null;
    const list = kids.get(parent) ?? [];
    list.push(g);
    kids.set(parent, list);
  }
  for (const [parent, list] of kids) list.sort(orderFor?.(parent) ?? ((a, b) => collator.compare(a.label, b.label)));
  const totals = new Map<string, Set<number>>();
  const collect = (key: string): Set<number> => {
    const cached = totals.get(key);
    if (cached) return cached;
    const ids = new Set((direct.get(key) ?? []).map((s) => s.id));
    totals.set(key, ids); // guards against cycles
    for (const c of kids.get(key) ?? []) for (const id of collect(c.key)) ids.add(id);
    return ids;
  };
  return {
    listing,
    groups: byKey,
    children: (key) => kids.get(key) ?? [],
    direct: (key) => direct.get(key) ?? [],
    total: (key) => collect(key).size,
    trail: (key) => {
      const out: Group[] = [];
      let cur = byKey.get(key);
      const seen = new Set<string>();
      while (cur?.parent && byKey.has(cur.parent) && !seen.has(cur.parent)) {
        seen.add(cur.parent);
        cur = byKey.get(cur.parent)!;
        out.unshift(cur);
      }
      return out;
    },
  };
}

function push(map: Map<string, Shortcut[]>, key: string, s: Shortcut) {
  const list = map.get(key) ?? [];
  if (!list.includes(s)) list.push(s);
  map.set(key, list);
}

export const folderKey = (id: number | null) => (id === null ? "root" : `f:${id}`);

export function mapperTree(state: MapperState): ListingTree {
  const groups: Group[] = state.folders.map((f) => ({
    key: folderKey(f.id), label: f.label, parent: f.parent_id === null ? null : folderKey(f.parent_id), kind: "folder",
    folderId: f.id, pinned: f.pinned,
  }));
  const byKey = new Map(state.folders.map((f) => [folderKey(f.id), f]));
  const direct = new Map<string, Shortcut[]>();
  for (const s of state.shortcuts) for (const f of s.folders) push(direct, folderKey(f), s);
  // Each folder orders its subfolders by its own child_sort (Egenskaber); the top level by the root's.
  return build("mapper", groups, direct, (parent) => childOrder(
    parent === null ? state.root?.child_sort ?? null : byKey.get(parent)?.child_sort ?? null,
    (g) => byKey.get(g.key) as Folder,
  ));
}

/** Subfolder order: label (alphabetical, the default), sort_key (then label) or manual (sort_order). */
export function childOrder(sort: ChildSort | null, folderOf: (g: Group) => Folder): Order {
  return (a, b) => {
    const fa = folderOf(a), fb = folderOf(b);
    if (sort === "manual") return fa.sort_order - fb.sort_order || collator.compare(a.label, b.label);
    if (sort === "sort_key") {
      if (fa.sort_key && fb.sort_key) return collator.compare(fa.sort_key, fb.sort_key) || collator.compare(a.label, b.label);
      if (fa.sort_key || fb.sort_key) return fa.sort_key ? -1 : 1;  // folders without a key last
    }
    return collator.compare(a.label, b.label);
  };
}

/** Folders of the user's Mapper in tree order (for pickers), with their level. */
export function folderList(state: MapperState): { folder: Folder; level: number }[] {
  const tree = mapperTree(state);
  const out: { folder: Folder; level: number }[] = [];
  const byId = new Map(state.folders.map((f) => [f.id, f]));
  const walk = (key: string | null, level: number) => {
    for (const g of tree.children(key)) {
      out.push({ folder: byId.get(g.folderId!)!, level });
      walk(g.key, level + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** The folder ids below a folder (to refuse moving a folder into itself). */
export function descendantsOf(state: MapperState, id: number): Set<number> {
  const out = new Set<number>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const f of state.folders) if (f.parent_id !== null && out.has(f.parent_id) && !out.has(f.id)) {
      out.add(f.id);
      grew = true;
    }
  }
  return out;
}

export function emnerTree(state: MapperState): ListingTree {
  const groups = new Map<string, Group>();
  const direct = new Map<string, Shortcut[]>();
  for (const s of state.shortcuts) {
    for (const t of s.tags) {
      const dim = `d:${t.dimension}`;
      if (!groups.has(dim)) groups.set(dim, { key: dim, label: t.dimension_label, parent: null, kind: "dimension", color: t.color });
      let parent = dim;
      for (const a of t.trail) {
        const k = `n:${a.id}`;
        if (!groups.has(k)) groups.set(k, { key: k, label: a.label, parent, kind: "tag", color: t.color });
        parent = k;
      }
      const k = `n:${t.id}`;
      if (!groups.has(k)) groups.set(k, { key: k, label: t.label, parent, kind: "tag", color: t.color });
      push(direct, k, s);
    }
  }
  return build("emner", [...groups.values()], direct);
}

// Second-level domains under a country code: example.co.uk is one site, not co.uk.
const SECOND_LEVEL = new Set(["co", "com", "org", "net", "gov", "ac", "edu", "or", "ne", "go", "gv", "nhs", "ltd", "plc"]);

export function bareHost(host: string): string {
  return host.toLowerCase().replace(/^www\./, "");
}

export function domainOf(host: string): string {
  const h = bareHost(host);
  if (/^[\d.]+$/.test(h) || h.includes(":")) return h;
  const parts = h.split(".");
  if (parts.length <= 2) return h;
  const n = parts.length;
  return parts[n - 1].length === 2 && SECOND_LEVEL.has(parts[n - 2]) ? parts.slice(-3).join(".") : parts.slice(-2).join(".");
}

export function websitesTree(state: MapperState): ListingTree {
  const groups = new Map<string, Group>();
  const direct = new Map<string, Shortcut[]>();
  const hostsOf = new Map<string, Set<string>>();
  for (const s of state.shortcuts) {
    const d = domainOf(s.host);
    const set = hostsOf.get(d) ?? new Set();
    set.add(bareHost(s.host));
    hostsOf.set(d, set);
  }
  for (const s of state.shortcuts) {
    const d = domainOf(s.host);
    const h = bareHost(s.host);
    const dk = `dom:${d}`;
    if (!groups.has(dk)) groups.set(dk, { key: dk, label: d, parent: null, kind: "domain" });
    // A host of its own only when the domain has more than one, or the host is not the domain itself.
    if (h === d) {
      push(direct, dk, s);
    } else {
      const hk = `h:${h}`;
      if (!groups.has(hk)) groups.set(hk, { key: hk, label: h, parent: dk, kind: "host" });
      push(direct, hk, s);
    }
  }
  return build("websteder", [...groups.values()], direct);
}

export function treeFor(listing: Listing, state: MapperState): ListingTree {
  return listing === "mapper" ? mapperTree(state) : listing === "emner" ? emnerTree(state) : websitesTree(state);
}

/** Every group key below a group (for "Fold alt ud"). */
export function allKeys(tree: ListingTree): string[] {
  return [...tree.groups.keys()];
}

// ── Lists ──────────────────────────────────────────────────────────────────

export function matches(s: Shortcut, q: string): boolean {
  const needle = q.trim().toLocaleLowerCase("da");
  if (!needle) return true;
  return [s.title, s.url, ...s.tags.map((t) => t.label)].some((x) => x.toLocaleLowerCase("da").includes(needle));
}

/** The position of a shortcut in a folder's manual order (null = not ordered). */
export function positionIn(s: Shortcut, folder: number | null): number | null {
  const i = s.folders.indexOf(folder);
  return i < 0 ? null : s.positions?.[i] ?? null;
}

/** Sorts a list. "folder" (Mappens sortering) uses the folder's own shortcut sort; its "manual" is the positions from
 * drag and drop, then the order added. Outside a folder (Alle genveje, Emner, Websteder, search) it is the order added. */
export function sortShortcuts(list: Shortcut[], sort: SortKey, order: Map<number, number>,
                              folder?: { id: number | null; sort: ShortcutSort | null } | null): Shortcut[] {
  const out = [...list];
  const by: ShortcutSort = sort === "folder" ? folder?.sort ?? "manual" : sort;
  if (by === "name") out.sort((a, b) => collator.compare(a.title, b.title));
  else if (by === "site") out.sort((a, b) => collator.compare(bareHost(a.host), bareHost(b.host)) || collator.compare(a.title, b.title));
  else if (by === "added") out.sort((a, b) => b.added_at.localeCompare(a.added_at) || b.id - a.id);
  else if (folder) {
    out.sort((a, b) => {
      const pa = positionIn(a, folder.id), pb = positionIn(b, folder.id);
      if (pa !== null && pb !== null && pa !== pb) return pa - pb;
      if ((pa === null) !== (pb === null)) return pa === null ? 1 : -1;
      // Not ordered yet: by when they were put in this folder.
      const ta = a.placed?.[a.folders.indexOf(folder.id)] ?? "", tb = b.placed?.[b.folders.indexOf(folder.id)] ?? "";
      return ta.localeCompare(tb) || (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0);
    });
  } else out.sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0));
  return out;
}

export function isPdf(url: string): boolean {
  try {
    return new URL(url).pathname.toLowerCase().endsWith(".pdf");
  } catch {
    return false;
  }
}

/** A hue per host for the letter tile (no favicons: nothing is fetched from other sites). The tile's lightness and
 * text colour come from mapper.css, so light and dark mode each get their own variant. */
export function hostHue(host: string): number {
  let h = 0;
  for (const ch of bareHost(host)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 360;
}

export function letterOf(s: Shortcut): string {
  return (bareHost(s.host)[0] ?? s.title[0] ?? "?").toUpperCase();
}

/** "Arbejde › Drift" for each folder a shortcut lies in ("Øverst" for the top level). */
export function placedIn(s: Shortcut, state: MapperState): string[] {
  const byId = new Map(state.folders.map((f) => [f.id, f]));
  return s.folders.map((id) => {
    if (id === null) return "Øverst";
    const names: string[] = [];
    let cur = byId.get(id);
    const seen = new Set<number>();
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id);
      names.unshift(cur.label);
      cur = cur.parent_id === null ? undefined : byId.get(cur.parent_id);
    }
    return names.join(" › ");
  });
}

// ── Views (after Baker's ObjectTable in Surdej) ────────────────────────────

export const COLUMNS: { id: Column; label: string }[] = [
  { id: "site", label: "Websted" },
  { id: "folder", label: "Placeret i" },
  { id: "added", label: "Tilføjet" },
];

export const SORTS: { id: SortKey; label: string }[] = [
  { id: "folder", label: "Mappens sortering" },
  { id: "name", label: "Navn" },
  { id: "site", label: "Websted" },
  { id: "added", label: "Senest tilføjet" },
];

export const BUILTIN: { id: string; name: string; spec: ViewSpec }[] = [
  { id: "b:standard", name: "Standard", spec: { layout: "list", sort: "folder", columns: ["site"], filter: "" } },
  { id: "b:kort", name: "Kort", spec: { layout: "grid", sort: "folder", columns: ["site"], filter: "" } },
  { id: "b:tabel", name: "Tabel", spec: { layout: "table", sort: "name", columns: ["site", "folder", "added"], filter: "" } },
  { id: "b:senest", name: "Senest tilføjet", spec: { layout: "list", sort: "added", columns: ["site", "added"], filter: "" } },
];

export function sameSpec(a: ViewSpec, b: ViewSpec): boolean {
  const cols = (c: Column[]) => [...c].sort().join(",");
  return a.layout === b.layout && a.sort === b.sort && cols(a.columns) === cols(b.columns) && (a.filter ?? "") === (b.filter ?? "");
}

export function normalizeSpec(spec: Partial<ViewSpec> | null | undefined): ViewSpec {
  // "manual" (Egen rækkefølge) was the name before folders had their own sort; it is Mappens sortering now.
  const sort = (spec?.sort as string) === "manual" || !spec?.sort ? "folder" : spec.sort;
  return { layout: spec?.layout ?? "list", sort, columns: spec?.columns ?? [], filter: spec?.filter ?? "" };
}

/** Per-viewer conveniences (chosen view, preview placement). Never required: storage may be blocked. */
export const local = {
  get(key: string): string | null {
    try {
      return window.localStorage.getItem(`gaer.mapper.${key}`);
    } catch {
      return null;
    }
  },
  set(key: string, value: string) {
    try {
      window.localStorage.setItem(`gaer.mapper.${key}`, value);
    } catch {
      /* storage blocked: the default is used next time */
    }
  },
};

export const SHORTCUT_SORTS: { id: ShortcutSort; label: string }[] = [
  { id: "manual", label: "Manuel rækkefølge" },
  { id: "name", label: "Navn" },
  { id: "site", label: "Websted" },
  { id: "added", label: "Senest tilføjet" },
];

export const CHILD_SORTS: { id: ChildSort; label: string }[] = [
  { id: "label", label: "Navn alfabetisk" },
  { id: "sort_key", label: "Sorteringsnøgle" },
  { id: "manual", label: "Manuel" },
];
