// Side menu of the /admin app. Paths are relative to the router basename "/admin".
import {
  Archive, BookOpen, Mail, KeySquare, Wrench, Bot, ChartColumn, Cookie, FileSearch, Flag, FolderOpen, Gauge, Globe, Library, LogIn, MousePointerClick, PlugZap, Puzzle, RadioTower, ScrollText,
  Settings, Smartphone, Table2, Plug, Timer, TrendingUp, Users, VenetianMask, Webhook, type LucideIcon,
} from "lucide-react";

export type NavBadge = "impersonation" | "features" | "mcp";
export type NavItem = { path: string; label: string; icon: LucideIcon; badge?: NavBadge };
export type NavGroup = { label: string; items: NavItem[] };

// Grouped by job, so each administrative role (server/app/admin_roles.py) mostly sees whole groups:
// Support → Brugere og support, Indhold, Produkt, Data, Privatliv → Privatliv og compliance, Drift → Drift og integrationer.
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
    label: "Brugere og support",
    items: [
      { path: "/users", label: "Brugere og roller", icon: Users },
      { path: "/impersonation", label: "Efterligning", icon: VenetianMask, badge: "impersonation" },
      { path: "/logs/auth", label: "Auth-log", icon: LogIn },
    ],
  },
  {
    label: "Indhold",
    items: [
      { path: "/web", label: "Website", icon: Globe },
      { path: "/files", label: "Filer", icon: FolderOpen },
      { path: "/knowledge", label: "Viden", icon: Library },
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
      { path: "/mcp", label: "MCP-servere", icon: PlugZap, badge: "mcp" },
      { path: "/integrations", label: "Udvidelse og Office", icon: Puzzle },
      { path: "/webhooks", label: "Webhooks", icon: Webhook },
      { path: "/nats", label: "NATS", icon: RadioTower },
      { path: "/settings", label: "Indstillinger", icon: Settings },
    ],
  },
];

/** The menu for an admin: only the sections their roles open (/api/auth/me → admin_sections). */
export function navFor(sections: string[] | undefined): NavGroup[] {
  const open = new Set(sections ?? []);
  return NAV.map((g) => ({ ...g, items: g.items.filter((i) => open.has(i.path)) })).filter((g) => g.items.length > 0);
}

/** The section a path under /admin belongs to: the longest menu path it starts with ("/" only matches itself). */
export function sectionFor(pathname: string): string {
  const paths = NAV.flatMap((g) => g.items.map((i) => i.path)).sort((a, b) => b.length - a.length);
  return paths.find((p) => (p === "/" ? pathname === "/" : pathname === p || pathname.startsWith(`${p}/`))) ?? pathname;
}

export const ALL_ITEMS = NAV.flatMap((g) => g.items);
