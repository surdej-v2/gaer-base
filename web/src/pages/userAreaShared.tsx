// Shared by Mit område (/user) and the dashboard (/): the overview's types, names, avatars and activity text.
// Kept apart from UserArea.tsx so the dashboard does not pull in that page (and its deep-link anchors).

export type Role = "reader" | "author";
export type Person = { id: number; display_name: string | null; email: string | null };
export type Stats = { clips: number; clips_week: number; files: number; bytes: number };
export type Activity = {
  ts: string; action: string; table: string | null; subject: string | null; role: Role | null; mine: boolean;
  actor: { id: number; name: string | null } | null;
};
export type Shared = { id: number; owner: Person; role: Role; role_label: string; created_at: string; expires_at: string | null };
export type Overview = {
  user: Person; stats: Stats; activity: Activity[]; shared_with_me: Shared[];
  access: { readers: number; authors: number; pending: number; people: { display_name: string | null; email: string; role: Role }[] };
};

export const ROLE_TEXT: Record<Role, { label: string; lower: string; desc: string }> = {
  reader: { label: "Læser", lower: "læser", desc: "Kan se dit overblik, dine klip og filer og hente filerne. Kan ikke ændre noget." },
  author: { label: "Forfatter", lower: "forfatter", desc: "Kan også oprette og redigere klip og uploade filer i dit område. Sletning kræver dig." },
};

export function name(p: { display_name?: string | null; email?: string | null } | null | undefined): string {
  return p?.display_name || p?.email || "Ukendt bruger";
}

export function initials(text: string): string {
  const parts = text.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

export function Avatar({ text, tone, large }: { text: string; tone?: "blue" | "amber" | "green"; large?: boolean }) {
  return <span className={`avatar ${tone ?? ""} ${large ? "lg" : ""}`} aria-hidden>{initials(text)}</span>;
}

export function activityText(a: Activity): string {
  const s = a.subject ? `“${a.subject}”` : "";
  const role = a.role ? ROLE_TEXT[a.role]?.lower : null;
  switch (`${a.action}:${a.table}`) {
    case "create:clips": return `gemte klippet ${s}`;
    case "update:clips": return `redigerede klippet ${s}`;
    case "delete:clips": return `slettede klippet ${s}`;
    case "file.upload:files": return `uploadede ${s}`;
    case "file.write:files": return `gemte ${s}`;
    case "file.move:files": return `flyttede ${s}`;
    case "file.delete:files": return `slettede ${s}`;
    case "file.download:files": return `hentede ${s}`;
    case "file.folder_create:file_folders": return `oprettede mappen ${s}`;
    case "file.folder_delete:file_folders": return `slettede mappen ${s}`;
    case "create:user_delegations": return `gav ${a.subject ?? "en person"} adgang${role ? ` som ${role}` : ""}`;
    case "update:user_delegations": return `ændrede adgangen for ${a.subject ?? "en person"}${role ? ` til ${role}` : ""}`;
    case "delete:user_delegations": return a.mine ? `fjernede adgangen for ${a.subject ?? "en person"}` : "opgav sin adgang til dit område";
    case "delegation.accepted:user_delegations": return `åbnede dit område for første gang${role ? ` (${role})` : ""}`;
    default: return `${a.action} ${s}`.trim();
  }
}
