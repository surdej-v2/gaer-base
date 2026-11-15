# Træstrukturer (metadata)

Ported from Surdej's `member-meta`. Design and examples: the Design canvas „Træstrukturer i Gær“ (five artboards:
renderings, single select, multi select, creating a node, component and API), built on the Surdej canvas
„Surdej Metadata-system“.

## Decided

| Question | Decision |
| --- | --- |
| What do other tables store? | **Node ids.** A path (`/org/drift/support`) is for showing, for URLs and for MCP; it changes when a node is renamed (slug) or moved. |
| What does a checked parent mean? | Per field: `cascade="node"` for **tags** (the node only), `cascade="subtree"` for **filters and access** (the whole branch, also nodes created later). The server expands a subtree with `GET /api/meta/expand` or `meta.expand_subtree()`. |
| Who creates the top level? | **Admins** with the admin role Indhold (or Fuld) and a valid second factor. Everyone creates below that, unless the dimension is **locked** (then admins only). |
| Private trees | `~/…` = `/~/<user id>/…`, only the owner sees and edits it. Exported and erased with the user (GDPR). |
| Deleting | Archive keeps stored values readable (hidden from pickers). Delete needs `force` when there are nodes or tags below; objects left without tags go too. |
| Tagging | Objects (`url`: exact, prefix pattern ending in `*`, or a host; `entity`: `type:id`; `note`) are found by kind + normalised reference, so tagging the same URL twice adds tags to one object. Everyone who can see a node may tag and untag with it (as in Surdej); title and description belong to the creator or a meta admin. You only see tags on nodes you can see, so private tags stay private. The last tag removed deletes the object. |

## In the code

- `server/app/migrations/0022_meta.sql` — `meta_dimensions`, `meta_nodes` (materialised `path`, `depth` 1–8, `owner_id` for private nodes).
- `server/app/meta.py` — paths and slugs, rules (`create_rule`), tree/search/resolve/expand, create/update (move rewrites the subtree)/delete.
- `server/app/routes/meta.py` — `/api/meta/*` for users; `routes/admin_meta.py` — `/api/admin/meta/*` (section `/meta`).
- `web/src/tree/` — `TreeView` (ARIA tree, keyboard), `TreeSelect` (single/multi, `root`, `includeRoot`, `selectable`, `cascade`, `max`, `allowCreate`, `variant`), `useTree` (lazy loading), `api.ts`.
- `server/app/migrations/0023_meta_objects.sql` + `server/app/meta_objects.py` — `meta_objects`, `meta_assignments`; URL normalisation (lower-case scheme and host, no fragment or tracking parameters, sorted query, no trailing slash), tag/set/untag, objects at a node (or its subtree), `/context`, `/resolve-url` (exact › longest pattern › host, at most 3), `/objects/by-ref`.
- `web/src/tree/TagEditor.tsx` — tags of one object across dimensions (one multi-select per tree in use), `TagChips`.
- `/extension/meta` (the **Tags** tab in the side panel, `web/src/extension/ExtensionMeta.tsx`) — what the current page is tagged with (`/resolve-url`), tagging it as the page, everything under its folder (`…/*`) or the whole domain, editing the page's tags, and browsing a tag's context (`ContextView` in `web/src/tree/MetaContext.tsx`). The address is looked up automatically only on domains the user has allowed (`/api/bridge-consent`); elsewhere only when they press „Slå siden op“.
- `/extension/mapper` (Mapper, `specs/mapper/`) — the user's own folders of shortcuts: private nodes under `~/mapper`, shortcuts as URL objects tagged with them, their other tags as „Emner“.
- `/developer/trae` — examples against the real API, including tagging, URL look-up and the context of a node; `/admin/meta` — dimensions, editing shared nodes, what is tagged under a node.
- Audit: `create`/`update`/`delete` on `meta_nodes`, `meta_dimensions` and `meta_objects` (tagging is an `update` with `tagged`/`untagged` node ids). Webhooks: `meta.node.created|updated|deleted`, `meta.object.tagged|untagged` (shared nodes, ids only). Event: `meta.node_created`.
- GDPR: export has the private tree, nodes and objects the user created and the tags they set; erasure removes the private tree, the objects only tagged there, and sets `created_by` to null on shared content.

## Not yet (from Surdej, or open)

- AI path suggestions for „Tag denne side“ (`/suggest`, optional through `ai.py`, only on allowed domains since it reads the page).
- Live titles and deep links for Gær entities (`clip:`, `file:`), and tagging from those pages; `/sites` reverse-domain nodes with key/value data.
- The generation DSL and YAML paste in `/admin/meta`, manual drag-and-drop ordering, merging nodes.
- Using `labels` and `child_sort` in the generic tree: migration 0041 stores labels per language (`labels`, e.g. `{"en": …}`), a `sort_key` and `child_sort` per parent (label / sort_key / manual; null = `sort_order`, then label) and a manual order of the objects at a node (`meta_assignments.sort_order`), editable through `PATCH /api/meta/nodes/{id}`; Mapper applies them, `/api/meta/tree` and `TreeView` still order by `sort_order`, then label.
- Proposals from users who may not create (a „Foreslå til admin“ flow), MCP tools for search and tagging.
- Visibility per branch beyond private trees.
