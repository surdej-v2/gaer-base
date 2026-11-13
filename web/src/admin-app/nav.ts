// Side menu of the /admin app. Paths are relative to the router basename "/admin".
import {
  Activity, Archive, ArrowLeftRight, ShieldAlert, BookOpen, LockKeyhole, MessageSquare, Reply, Send, SlidersHorizontal, LayoutDashboard, Route as RouteIcon, SquareTerminal, UsersRound, Waypoints, Mail, KeySquare, Network, Wrench, Bot, ChartColumn, Cookie, FileSearch, Flag, FolderOpen, Gauge, Globe, Library, LogIn, MousePointerClick, PlugZap, Puzzle, RadioTower, ScrollText, Server,
  Settings, Smartphone, Table2, Plug, Timer, TrendingUp, Users, VenetianMask, Webhook, type LucideIcon,
} from "lucide-react";

export type NavBadge = "impersonation" | "features" | "mcp" | "replies" | "reports";
export type NavItem = { path: string; label: string; icon: LucideIcon; badge?: NavBadge };
export type NavGroup = { label: string; items: NavItem[] };

// Grouped by job, so each administrative role (server/app/admin_roles.py) mostly sees whole groups:
// Produkt → Adoption, Support → Brugere og support, Indhold, Produkt, Data, Privatliv → Privatliv og compliance, Drift → Drift og integrationer.
export const NAV: NavGroup[] = [
  {
    label: "Overblik",
    items: [
      { path: "/", label: "Statistik", icon: ChartColumn },
      { path: "/analytics", label: "Analytics", icon: TrendingUp },
      { path: "/heatmaps", label: "Heatmaps", icon: MousePointerClick },
    ],
  },
  {
    label: "Adoption",
    items: [
      { path: "/adoption", label: "Adoption", icon: LayoutDashboard },
      { path: "/adoption/journeys", label: "Forløb", icon: RouteIcon },
      { path: "/adoption/routes", label: "Ruter og URL'er", icon: Waypoints },
      { path: "/adoption/console", label: "Live-strøm", icon: SquareTerminal },
      { path: "/adoption/access", label: "Adgang til forløb", icon: UsersRound },
    ],
  },
  {
    label: "Brugere og support",
    items: [
      { path: "/users", label: "Brugere og roller", icon: Users },
      { path: "/groups", label: "Grupper", icon: UsersRound },
      { path: "/impersonation", label: "Efterligning", icon: VenetianMask, badge: "impersonation" },
      { path: "/logs/auth", label: "Auth-log", icon: LogIn },
    ],
  },
  {
    label: "Beskeder",
    items: [
      { path: "/messages", label: "Beskeder", icon: MessageSquare },
      { path: "/messages/send", label: "Send besked", icon: Send },
      { path: "/messages/replies", label: "Svar", icon: Reply, badge: "replies" },
      { path: "/messages/moderation", label: "Moderering", icon: ShieldAlert, badge: "reports" },
      { path: "/messages/delivery", label: "Levering", icon: Activity },
      { path: "/messages/settings", label: "Kanaler og regler", icon: SlidersHorizontal },
    ],
  },
  {
    label: "Indhold",
    items: [
      { path: "/web", label: "Website", icon: Globe },
      { path: "/files", label: "Filer", icon: FolderOpen },
      { path: "/knowledge", label: "Viden", icon: Library },
      { path: "/meta", label: "Træstrukturer", icon: Network },
      { path: "/app-stores", label: "App Stores", icon: Smartphone },
    ],
  },
  {
    label: "Produkt",
    items: [
      { path: "/features", label: "Features og ringe", icon: Flag, badge: "features" },
      { path: "/ai", label: "AI", icon: Bot },
      { path: "/consumption", label: "AI-forbrug", icon: Gauge },
      { path: "/webhooks", label: "Webhooks", icon: Webhook },
    ],
  },
  {
    label: "Data",
    items: [
      { path: "/data", label: "Katalog", icon: BookOpen },
      { path: "/data/explore", label: "Udforsk", icon: Table2 },
      { path: "/data/connect", label: "Forbind API", icon: Plug },
      { path: "/data/tools", label: "MCP-værktøjer", icon: Wrench },
      { path: "/data/transfer", label: "Eksport og import", icon: ArrowLeftRight },
    ],
  },
  {
    label: "Privatliv og compliance",
    items: [
      { path: "/gdpr", label: "GDPR-forespørgsler", icon: FileSearch },
      { path: "/access", label: "Adgangsanmodninger", icon: KeySquare },
      { path: "/consent", label: "Samtykke og cookies", icon: Cookie },
      { path: "/retention", label: "Sletning af logdata", icon: Archive },
      { path: "/jobs", label: "Jobs og oprydning", icon: Timer },
      { path: "/logs/audit", label: "Audit-log", icon: ScrollText },
    ],
  },
  {
    label: "Drift og integrationer",
    items: [
      { path: "/mail", label: "Mail", icon: Mail },
      { path: "/secrets", label: "Hemmeligheder", icon: LockKeyhole },
      { path: "/mcp", label: "MCP-servere", icon: PlugZap, badge: "mcp" },
      { path: "/integrations", label: "Udvidelse og Office", icon: Puzzle },
      { path: "/webhooks", label: "Webhooks", icon: Webhook },
      { path: "/nats", label: "NATS", icon: RadioTower },
      { path: "/resources", label: "Ressourcer", icon: Server },
      { path: "/settings", label: "Indstillinger", icon: Settings },
    ],
  },
];

/** The menu for an admin: only the sections their roles open (/api/auth/me → admin_sections). */
export function navFor(sections: string[] | undefined): NavGroup[] {
  const open = new Set(sections ?? []);
  return NAV.map((g) => ({ ...g, items: g.items.filter((i) => open.has(i.path)) })).filter((g) => g.items.length > 0);
}

/** Start and end (exclusive) of a run of matching characters in a label. */
export type Range = [start: number, end: number];
export type NavHit = { item: NavItem; ranges: Range[] };
export type NavGroupHit = { group: NavGroup; ranges: Range[]; items: NavHit[] };

/** The words of a menu search, lower-cased. */
export function navTerms(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

/** Every occurrence of the terms in text, merged and in order — the characters to highlight. */
export function matchRanges(text: string, terms: string[]): Range[] {
  const lower = text.toLowerCase();
  const found: Range[] = [];
  for (const t of terms) {
    for (let i = lower.indexOf(t); i >= 0; i = lower.indexOf(t, i + 1)) found.push([i, i + t.length]);
  }
  found.sort((a, b) => a[0] - b[0]);
  const merged: Range[] = [];
  for (const [start, end] of found) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

/**
 * The menu filtered by a search: every word must occur in the item's label, its path or its group's name.
 * Groups without hits disappear. An empty query returns every group with no ranges to highlight.
 */
export function filterNav(groups: NavGroup[], query: string): NavGroupHit[] {
  const terms = navTerms(query);
  return groups
    .map((group) => {
      const inGroup = group.label.toLowerCase();
      const items = group.items
        .filter((item) => {
          const label = item.label.toLowerCase();
          return terms.every((t) => label.includes(t) || item.path.includes(t) || inGroup.includes(t));
        })
        .map((item) => ({ item, ranges: matchRanges(item.label, terms) }));
      return { group, ranges: matchRanges(group.label, terms), items };
    })
    .filter((g) => g.items.length > 0);
}

/** The section a path under /admin belongs to: the longest menu path it starts with ("/" only matches itself). */
export function sectionFor(pathname: string): string {
  const paths = NAV.flatMap((g) => g.items.map((i) => i.path)).sort((a, b) => b.length - a.length);
  return paths.find((p) => (p === "/" ? pathname === "/" : pathname === p || pathname.startsWith(`${p}/`))) ?? pathname;
}

export const ALL_ITEMS = NAV.flatMap((g) => g.items);
