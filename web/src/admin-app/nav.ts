// Side menu of the /admin app. Paths are relative to the router basename "/admin".
import {
  Archive, Bot, ChartColumn, Cookie, FileSearch, Flag, FolderOpen, Globe, LogIn, MousePointerClick, Puzzle, ScrollText,
  Settings, Smartphone, TrendingUp, Users, VenetianMask, type LucideIcon,
} from "lucide-react";

export type NavBadge = "impersonation" | "features";
export type NavItem = { path: string; label: string; icon: LucideIcon; badge?: NavBadge };
export type NavGroup = { label: string; items: NavItem[] };

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
    label: "Adgang",
    items: [
      { path: "/users", label: "Brugere og roller", icon: Users },
      { path: "/impersonation", label: "Efterligning", icon: VenetianMask, badge: "impersonation" },
      { path: "/logs/auth", label: "Auth-log", icon: LogIn },
      { path: "/logs/audit", label: "Audit-log", icon: ScrollText },
    ],
  },
  {
    label: "Produkt",
    items: [
      { path: "/features", label: "Features og ringe", icon: Flag, badge: "features" },
      { path: "/app-stores", label: "App Stores", icon: Smartphone },
      { path: "/web", label: "Website", icon: Globe },
      { path: "/files", label: "Filer", icon: FolderOpen },
    ],
  },
  {
    label: "Integrationer",
    items: [
      { path: "/ai", label: "AI", icon: Bot },
      { path: "/integrations", label: "Udvidelse og Office", icon: Puzzle },
    ],
  },
  {
    label: "System",
    items: [
      { path: "/gdpr", label: "GDPR-forespørgsler", icon: FileSearch },
      { path: "/retention", label: "Sletning af logdata", icon: Archive },
      { path: "/consent", label: "Samtykke og cookies", icon: Cookie },
      { path: "/settings", label: "Indstillinger", icon: Settings },
    ],
  },
];

export const ALL_ITEMS = NAV.flatMap((g) => g.items);
