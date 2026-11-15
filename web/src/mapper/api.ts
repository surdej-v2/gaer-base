// Mapper: the client for /api/mapper (server/app/mapper.py). Folders are private meta nodes under ~/mapper, a shortcut
// is a URL object tagged with one or more of them (null = the top level).
import { api, query } from "../api";

export type ChildSort = "label" | "sort_key" | "manual";
export type ShortcutSort = "manual" | "name" | "site" | "added";

/** A folder's properties ("Egenskaber"): meta's name, English label, sort key and child order (child_sort, null =
 * alphabetical in Mapper), and Mapper's own shortcut sort and layout (null = follow the view). */
export type FolderProps = {
  en_label: string | null;
  sort_key: string | null;
  child_sort: ChildSort | null;
  shortcut_sort: ShortcutSort | null;
  layout: Layout | null;
};

export type Folder = FolderProps & { id: number; parent_id: number | null; label: string; depth: number; sort_order: number; pinned: boolean };
export type Root = { id: number; label: string; child_sort: ChildSort | null; shortcut_sort: ShortcutSort | null; layout: Layout | null };

export type Tag = {
  id: number;
  label: string;
  dimension: string;
  dimension_label: string;
  color: string | null;
  trail: { id: number; label: string }[];
  private: boolean;
  parent_id: number | null;
};

export type Shortcut = {
  id: number;
  url: string;
  title: string;
  host: string;
  /** The folders it lies in; null = the top level of Mapper. */
  folders: (number | null)[];
  /** The manual order in each of those folders (same index as folders; null = not ordered yet). */
  positions: (number | null)[];
  /** When it was put in each of those folders. */
  placed: string[];
  added_at: string;
  mine: boolean;
  /** Its other tags (shared dimensions, the rest of the private tree): the "Emner" listing. */
  tags: Tag[];
};

export type MapperState = { root: Root | null; folders: Folder[]; shortcuts: Shortcut[]; truncated: boolean };

export type Layout = "list" | "grid" | "table";
/** "folder" = Mappens sortering: each folder's own shortcut sort (Egenskaber). */
export type SortKey = "folder" | "name" | "site" | "added";
export type Column = "site" | "folder" | "added";
export type ViewSpec = { layout: Layout; sort: SortKey; columns: Column[]; filter: string };
export type SavedView = { id: number; name: string; shared: boolean; spec: ViewSpec; mine: boolean; owner_name: string | null; updated_at: string };

export type FrameCheck = { url: string; framable: boolean; kind: "html" | "pdf" | "other"; reason: string | null; final_url: string };

export type ImportFolder = { key: string; parent: string | null; label: string; bar?: boolean };
export type ImportItem = { folder: string | null; url: string; title: string };
export type ImportBody = {
  target: "new" | "top";
  target_label?: string;
  keep_structure: boolean;
  skip_duplicates: boolean;
  pin_bar: boolean;
  folders: ImportFolder[];
  items: ImportItem[];
};
export type ImportResult = {
  folders_created: number; folders_reused: number; shortcuts_created: number; shortcuts_added: number;
  duplicates: number; invalid: number; pinned: number; root_id: number; target_id: number | null;
};

export const mapperApi = {
  state: () => api.get<MapperState>("/api/mapper"),
  createFolder: (label: string, parentId: number | null) => api.post<Folder>("/api/mapper/folders", { label, parent_id: parentId }),
  renameFolder: (id: number, label: string) => api.patch<Folder>(`/api/mapper/folders/${id}`, { label }),
  saveFolder: (id: number, body: Partial<FolderProps> & { label?: string }) => api.patch<Folder>(`/api/mapper/folders/${id}`, body),
  orderChildren: (id: number, ids: number[]) => api.put<{ order: number[] }>(`/api/mapper/folders/${id}/children-order`, { ids }),
  orderShortcuts: (id: number, ids: number[]) => api.put<{ order: number[] }>(`/api/mapper/folders/${id}/shortcut-order`, { ids }),
  copyFolder: (id: number, to: number | null) =>
    api.post<{ id: number; label: string; folders: number; shortcuts: number }>(`/api/mapper/folders/${id}/copy`, { to_id: to }),
  copyShortcut: (id: number, to: number | null) => api.post<Shortcut>(`/api/mapper/shortcuts/${id}/copy`, { to_id: to }),
  moveFolder: (id: number, parentId: number | null) => api.patch<Folder>(`/api/mapper/folders/${id}`, { parent_id: parentId }),
  deleteFolder: (id: number, force = false) => api.del<{ ok: boolean }>(`/api/mapper/folders/${id}` + query({ force: force || undefined })),
  pin: (id: number, pinned: boolean) => api.put<{ id: number; pinned: boolean }>(`/api/mapper/folders/${id}/pin`, { pinned }),
  addShortcut: (url: string, title: string | null, folderId: number | null) =>
    api.post<Shortcut & { created: boolean; added: boolean }>("/api/mapper/shortcuts", { url, title, folder_id: folderId }),
  renameShortcut: (id: number, title: string) => api.patch<Shortcut>(`/api/mapper/shortcuts/${id}`, { title }),
  moveShortcut: (id: number, from: number | null, to: number | null) =>
    api.patch<Shortcut>(`/api/mapper/shortcuts/${id}`, { move: { from_id: from, to_id: to } }),
  removeShortcut: (id: number, folder: number | null, everywhere = false) =>
    api.del<{ removed: number; deleted: boolean; in_mapper: boolean }>(
      `/api/mapper/shortcuts/${id}` + query({ folder: folder ?? undefined, everywhere: everywhere || undefined })),
  import: (body: ImportBody) => api.post<ImportResult>("/api/mapper/import", body),
  views: () => api.get<SavedView[]>("/api/mapper/views"),
  createView: (name: string, shared: boolean, spec: ViewSpec) => api.post<SavedView>("/api/mapper/views", { name, shared, spec }),
  updateView: (id: number, body: { name?: string; shared?: boolean; spec?: ViewSpec }) => api.patch<SavedView>(`/api/mapper/views/${id}`, body),
  deleteView: (id: number) => api.del<{ ok: boolean }>(`/api/mapper/views/${id}`),
  frameCheck: (url: string) => api.get<FrameCheck>("/api/mapper/frame-check" + query({ url })),
};
