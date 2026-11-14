// The navigation of Gær Developer (specs/developer/NAVIGATION.md, NAV-1 … NAV-3): one sidebar with a global level
// (Studier, Fælles) and local pages under the active item. Items are routes in web/src/routes.json.
import {
  ArrowLeftRight, BookOpen, Cable, CircleCheck, Code, Database, FileText, LayoutGrid, Plug, Workflow, type LucideIcon,
} from "lucide-react";

export type LocalPage = { path: string; label: string; end?: boolean };
export type NavItem = { key: string; path: string; label: string; icon: LucideIcon; local: LocalPage[]; badge?: "approvals" };
export type NavGroup = { label: string; items: NavItem[] };

export const NAV: NavGroup[] = [
  {
    label: "Studier",
    items: [
      { key: "oversigt", path: "/developer", label: "Oversigt", icon: LayoutGrid, local: [] },
      { key: "datastudie", path: "/developer/datastudie", label: "Datastudie", icon: Database, local: [
        { path: "/developer/datastudie", label: "Endpoints", end: true },
        { path: "/developer/datastudie/multisource", label: "Multisource-kilder" },
        { path: "/developer/datastudie/operationer", label: "Operationer" },
      ] },
      { key: "pipeline", path: "/developer/pipeline", label: "Pipeline", icon: Workflow, local: [
        { path: "/developer/pipeline", label: "Pipelines", end: true },
        { path: "/developer/pipeline/snapshots", label: "Snapshots" },
        { path: "/developer/pipeline/koersler", label: "Kørsler" },
        { path: "/developer/pipeline/planer", label: "Planer" },
      ] },
      { key: "mcp", path: "/developer/mcp", label: "MCP-studie", icon: Plug, local: [
        { path: "/developer/mcp", label: "Endpoints", end: true },
        { path: "/developer/studio/mcp", label: "Endpoints i kode" },
        { path: "/developer/mcp/testbaenk", label: "Testbænk" },
        { path: "/developer/mcp/klienter", label: "Klienter" },
        { path: "/developer/mcp/eksterne", label: "Eksterne servere" },
        { path: "/developer/mcp/drift", label: "Overvågning" },
      ] },
      { key: "api", path: "/developer/api", label: "API-studie", icon: Code, local: [
        { path: "/developer/api", label: "API'er", end: true },
        { path: "/developer/api/noegler", label: "Nøgler" },
        { path: "/developer/api/konsol", label: "Testkonsol" },
        { path: "/developer/api/dokumentation", label: "Dokumentation" },
        { path: "/developer/api/drift", label: "Overvågning" },
      ] },
    ],
  },
  {
    label: "Fælles",
    items: [
      { key: "katalog", path: "/developer/katalog", label: "Datakatalog", icon: FileText, local: [
        { path: "/developer/katalog", label: "Datasæt", end: true },
        { path: "/developer/katalog/lineage", label: "Lineage" },
      ] },
      { key: "forbindelser", path: "/developer/forbindelser", label: "Forbindelser", icon: Cable, local: [
        { path: "/developer/forbindelser", label: "Databaser", end: true },
        { path: "/developer/studio/api", label: "Live-kilder" },
        { path: "/developer/forbindelser/cache", label: "Cache" },
      ] },
      { key: "godkendelser", path: "/developer/godkendelser", label: "Godkendelser", icon: CircleCheck, badge: "approvals", local: [
        { path: "/developer/godkendelser", label: "Venter på mig", end: true },
        { path: "/developer/godkendelser/historik", label: "Historik" },
      ] },
      { key: "eksport", path: "/developer/eksport", label: "Eksport og import", icon: ArrowLeftRight, local: [] },
    ],
  },
];

export const HELP: NavItem = { key: "hjaelp", path: "/developer/hjaelp", label: "Hjælp", icon: BookOpen, local: [
  { path: "/developer/hjaelp", label: "Om Developer", end: true },
  { path: "/developer/prompts", label: "Promptkatalog" },
  { path: "/developer/trae", label: "Træstrukturer" },
  { path: "/developer/studio", label: "Studio (klassisk)" },
] };

const ALL = [...NAV.flatMap((g) => g.items), HELP];

/** Pages that belong to an item without being in its local list (detail pages). */
const EXTRA: [RegExp, string][] = [
  [/^\/developer\/artefakter\//, ""],
];

function matches(pathname: string, p: LocalPage | { path: string; end?: boolean }): boolean {
  return p.end ? pathname === p.path || pathname === p.path + "/" : pathname === p.path || pathname.startsWith(p.path + "/");
}

/** The global item a path belongs to (the longest matching path wins). */
export function activeItem(pathname: string): NavItem | null {
  for (const [re, key] of EXTRA) if (re.test(pathname)) return ALL.find((i) => i.key === key) ?? null;
  let best: NavItem | null = null;
  let len = -1;
  for (const item of ALL) {
    const paths = [item.path, ...item.local.map((l) => l.path)];
    for (const p of paths) {
      const hit = p === "/developer" ? pathname === "/developer" || pathname === "/developer/" : pathname === p || pathname.startsWith(p + "/");
      if (hit && p.length > len) {
        best = item;
        len = p.length;
      }
    }
  }
  return best;
}

export function activeLocal(pathname: string, item: NavItem | null): LocalPage | null {
  if (!item) return null;
  let best: LocalPage | null = null;
  for (const l of item.local) if (matches(pathname, { ...l, end: false }) && (!best || l.path.length > best.path.length)) best = l;
  return best;
}

/** Breadcrumb (NAV-4): Developer / Datastudie / Endpoints. */
export function breadcrumb(pathname: string, extra?: string | null): { label: string; path?: string }[] {
  const item = activeItem(pathname);
  const crumbs: { label: string; path?: string }[] = [{ label: "Developer", path: "/developer" }];
  if (item && item.key !== "oversigt") crumbs.push({ label: item.label, path: item.path });
  const local = activeLocal(pathname, item);
  if (local && local.path !== item?.path) crumbs.push({ label: local.label, path: local.path });
  else if (local && item && item.local.length > 0 && local.path === item.path) crumbs.push({ label: local.label, path: local.path });
  if (extra) crumbs.push({ label: extra });
  return crumbs;
}

export type Command = { label: string; hint: string; path: string; keywords?: string };

/** Static commands for the palette (NAV-11): every page and the main actions. */
export function staticCommands(): Command[] {
  const out: Command[] = [];
  for (const item of ALL) {
    out.push({ label: item.label, hint: "Studie", path: item.path });
    for (const l of item.local) if (l.path !== item.path) out.push({ label: `${item.label} · ${l.label}`, hint: "Side", path: l.path });
  }
  out.push({ label: "Ny pipeline", hint: "Handling", path: "/developer/pipeline/ny", keywords: "opret pipeline" });
  out.push({ label: "Nyt endpoint i Datastudie", hint: "Handling", path: "/developer/datastudie/ny", keywords: "opret mcp endpoint guide" });
  out.push({ label: "Ny forbindelse", hint: "Handling", path: "/developer/forbindelser?ny=1", keywords: "opret database" });
  out.push({ label: "Til Gær base", hint: "Handling", path: "/", keywords: "tilbage app" });
  return out;
}

export function score(cmd: Command, q: string): number {
  const text = `${cmd.label} ${cmd.hint} ${cmd.keywords ?? ""}`.toLowerCase();
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return 0;
  let s = 0;
  for (const w of words) {
    const i = text.indexOf(w);
    if (i < 0) return -1;
    s += i === 0 ? 3 : text[i - 1] === " " ? 2 : 1;
  }
  return s;
}
