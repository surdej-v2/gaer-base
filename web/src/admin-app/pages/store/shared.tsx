// Shared parts of /admin/data/store (server/app/datastore.py, routes/datastore.py, routes/admin_datastore.py).
import type { ReactNode } from "react";
import type { Person, Role } from "../secrets/shared";

export type { Person, Role };
export type Level = "read" | "write" | "manage" | null;
export type Option = { key: string; label: string };
export type GroupMeta = { id: number; slug: string; name: string; members: (Person & { group_role: string })[]; is_member: boolean };
export type StoreMeta = {
  groups: GroupMeta[]; people: Person[]; me: number; full: boolean;
  kinds: Option[]; versioning: Option[]; minor_rules: Option[]; major_rules: Option[]; share_levels: Option[];
};

export type Container = {
  id: number; kind: "nosql" | "kv"; slug: string; ref: string; name: string; description: string; group_id: number | null;
  group_name: string | null; personal: boolean; visibility: "private" | "shared"; versioning: "none" | "revisions" | "semver";
  minor_rule: string; major_rule: string; sunset_days: number; json_schema: Record<string, unknown> | null;
  default_ttl_seconds: number | null; current_version: string | null; my_level: Level; items: number;
  pending_release?: string | null; created_at: string; updated_at: string; archived_at: string | null;
};

export type Approval = { approver: Person | null; roles: string[]; decision: "pending" | "approved" | "rejected"; note: string | null; decided_at: string | null };
export type Release = {
  id: number; container_id: number; version: string; major: number; minor: number; kind: "major" | "minor";
  status: "pending" | "approved" | "rejected" | "cancelled" | "expired"; approval_rule: string; fallback: boolean; note: string;
  breaking: string[]; changes: { added?: string[]; changed?: string[]; removed?: string[]; counts?: Record<string, number>; schema_changed?: boolean };
  item_count: number; proposed_by: Person | null; created_at: string; expires_at: string; decided_at: string | null;
  sunset_at: string | null; items_purged_at: string | null; approvals: Approval[]; can_decide: boolean; own: boolean;
  container_name?: string;
};

export const KIND_LABEL: Record<string, string> = { nosql: "NoSQL", kv: "KV" };
export const VERSIONING_LABEL: Record<string, string> = { none: "Ingen", revisions: "Løbende", semver: "Major/minor" };
export const LEVEL_LABEL: Record<string, string> = { read: "Læse", write: "Skrive", manage: "Ansvarlig" };
export const RELEASE_STATUS: Record<string, [string, string]> = {
  pending: ["Venter", "#9a3412"], approved: ["Godkendt", "#166534"], rejected: ["Afvist", "#b91c1c"],
  cancelled: ["Annulleret", "#475569"], expired: ["Udløbet", "#475569"],
};
export const RULE_LABEL: Record<string, string> = {
  auto: "Automatisk", one_r: "Én R eller A", a: "A", a_and_all_c: "A og alle C", a_and_one_c: "A og én C", a_only: "Kun A",
  personal: "Personlig — straks",
};

const PILL: Record<string, [string, string]> = {
  blue: ["rgba(37,99,235,0.1)", "#1d4ed8"], dark: ["#0f172a", "#ffffff"], green: ["rgba(22,163,74,0.12)", "#166534"],
  gray: ["#f1f5f9", "#475569"], orange: ["rgba(249,115,22,0.12)", "#9a3412"], red: ["rgba(220,38,38,0.1)", "#b91c1c"],
  major: ["#9a3412", "#ffffff"],
};

export function Tag({ tone, children }: { tone: keyof typeof PILL; children: ReactNode }) {
  const [bg, fg] = PILL[tone];
  return <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 9999, background: bg, color: fg, whiteSpace: "nowrap" }}>{children}</span>;
}

export function KindTag({ kind }: { kind: string }) {
  return <Tag tone={kind === "kv" ? "dark" : "blue"}>{KIND_LABEL[kind] ?? kind}</Tag>;
}

export function VisibilityTag({ c }: { c: Pick<Container, "visibility" | "personal"> & { share_count?: number } }) {
  if (c.visibility === "shared") return <Tag tone="green">Delt{c.share_count ? ` · ${c.share_count} ${c.share_count === 1 ? "gruppe" : "grupper"}` : ""}</Tag>;
  return <Tag tone="gray">Privat · {c.personal ? "kun mig" : "gruppe"}</Tag>;
}

export function KindBadge({ kind }: { kind: "major" | "minor" }) {
  return <Tag tone={kind === "major" ? "major" : "blue"}>{kind === "major" ? "MAJOR" : "MINOR"}</Tag>;
}
