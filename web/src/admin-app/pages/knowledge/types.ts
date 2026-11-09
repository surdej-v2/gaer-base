// Viden in /admin (specs/knowledge/README.md): the shapes of /api/admin/knowledge.
import type { Tone } from "../../theme";

export type DocStatus = "pending" | "processing" | "indexed" | "skipped" | "failed" | "error";

export type SpaceSummary = {
  id: number; name: string; description: string; created_at: string; updated_at: string; deleted_at: string | null;
  documents: number; indexed: number; working: number; failed: number; agents: number;
};

export type AgentSummary = {
  id: number; slug: string; name: string; description: string; status: "draft" | "published"; access: "all" | "members";
  version_major: number; version_minor: number; space_ids: number[]; updated_at: string;
};

export type Overview = {
  spaces: SpaceSummary[]; bin: SpaceSummary[]; agents: AgentSummary[]; bin_days: number;
  stats: { chunks: number; conversations_30d: number; questions_30d: number };
  ai: { enabled: boolean; embeddings: boolean; embed_model: string | null };
};

export type Doc = {
  id: number; space_id: number; filename: string; path: string; size_bytes: number; status: DocStatus; error: string;
  page_count: number; chunk_count: number; char_count: number; embedded: boolean; created_at: string; indexed_at: string | null;
};

export type Space = SpaceSummary & {
  documents: Doc[]; agents: { id: number; slug: string; name: string; status: string; version_major: number; version_minor: number }[];
};

export type LogEntry = { id: number; document_id: number | null; level: "info" | "warn" | "error"; stage: string; message: string; created_at: string };

export type Citation = { n: number; document_id: number; filename: string; space_id: number; section: string | null; page: number | null };
export type Answer = { answer: string; citations: Citation[]; method: string };

export type Agent = AgentSummary & {
  instructions: string; welcome: string; suggested_prompts: string[]; unpublished_changes: boolean;
  versions: { id: number; major: number; minor: number; note: string; created_at: string; created_by: string | null }[];
  members: { user_id: number; privilege: "use" | "manage"; display_name: string | null; email: string | null }[];
};

export const STATUS: Record<DocStatus, [label: string, tone: Tone]> = {
  pending: ["I kø", "gray"],
  processing: ["Behandles", "blue"],
  indexed: ["Indekseret", "green"],
  skipped: ["Sprunget over", "gray"],
  failed: ["Kan ikke læses", "red"],
  error: ["Fejl", "orange"],
};

export const version = (a: { version_major: number; version_minor: number }) => `v${a.version_major}.${a.version_minor}`;

export const initialsOf = (name: string) =>
  name.split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase() || "V";

export const srOnly = { position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap" } as const;
