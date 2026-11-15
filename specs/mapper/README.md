# Mapper (genveje i sidepanelet)

A personal, hierarchical store of shortcuts in the browser extension's side panel: `/extension/mapper`, the tab
**Mapper** right after **Side**. Feature `mapper`, ring 1 (Internt) when it ships; `/admin/features` moves it out.
Defined by an interactive mockup; this is how it is built in Gær.

## Decided

| Question | Decision |
| --- | --- |
| Where do folders live? | In Træstrukturer (`specs/meta/`): the user's private tree. The root is the node `~/mapper` (label „Mapper“), made the first time it is needed; folders are private nodes under it (depth 2–8). No new tree table. |
| What is a shortcut? | A URL object (`meta_objects`, kind `url`, normalised like every tag) tagged with one or more Mapper folders. Tagged with the root itself = at the top level. The same shortcut may lie in several folders. |
| Emner | The shortcut's *other* tags (shared dimensions, the rest of the private tree), shown as a tree: dimension › trail › node. Edited in „Ret“ with `TagEditor`. |
| Websteder | Computed in the browser: registrable domain (`example.co.uk`, not `co.uk`) › host. |
| Titles | A URL object is shared by everyone who tagged it and its title belongs to its creator. When another user saves the same URL with their own title, Mapper keeps it in `mapper_titles` and shows it to them only. Renaming as the creator changes the object's title (meta's rule). |
| Pins | `mapper_pins` (user, node), only the user's own folders. „Fastgjort“ at the top of the tree. |
| Views | After Baker's `ObjectTable` in Surdej: layout (list, kort, tabel) + sort (egen rækkefølge, navn, websted, senest tilføjet) + columns (Websted, Placeret i, Tilføjet) + a text filter. Built in: Standard, Kort, Tabel, Senest tilføjet. Saved in `mapper_views`: *Mine visninger* (only the owner) and *Delt med teamet* (`shared`: every user may choose it, only the owner edits it, a meta admin may remove it). The chosen view and the preview placement are remembered per browser (localStorage). |
| „Egen rækkefølge“ | The order the shortcuts were added in (assignment time). Manual drag-and-drop ordering is not built. |
| Preview | Clicking a shortcut opens it next to the list when the content pane is at least 760 px wide, under it otherwise (Auto / Højre / Under); expand hides the list. Before framing, `GET /api/mapper/frame-check` reads only the page's response headers through the SSRF guard (`webhooks.check_url`, every redirect checked, http refused): `X-Frame-Options` DENY/SAMEORIGIN, a CSP `frame-ancestors` without `*`, `Content-Disposition: attachment` or an error status → „Kan ikke vises her“ + „Åbn i ny fane“ (and „Prøv alligevel“). HTML is framed with `sandbox`; a PDF without it (the browser's PDF viewer does not run in a sandboxed frame). Results are cached 10 minutes; 60 checks a minute per user. Integration `mapper_preview` in `app/integrations.py`. |
| CSP | Only the document of `/extension/mapper` gets `frame-src 'self' https:` (`FRAME_ANY_HTTPS` in `main.py`); everything else keeps `frame-src 'self'` + Office. The side panel opens Mapper with a full page load (`reloadDocument` on the tab), since a client-side navigation would keep the CSP of the page it came from. |
| Import | „Importér favoritter“: Kilde (this browser through `chrome.bookmarks`, an exported `.html` file parsed in the browser, or an empty store) → Vælg mapper (check boxes with counts, Bevar mappestrukturen, Spring dubletter over with the count) → Placering (a new folder „Fra browseren“ or the top level, Fastgør Bogmærkelinjen, live preview) → Færdig („N genveje i M mapper“). One call, `POST /api/mapper/import`, one transaction: folders with the same name under the same parent are reused (importing twice does not double the tree), duplicates are skipped by normalised URL, non-web addresses are counted and skipped, folders deeper than the tree allows are folded into the deepest one. Shown the first time Mapper has no root; „Start med et tomt lager“ makes the root so it does not come back. |
| Bookmarks permission | `bookmarks` is an *optional* permission in `extension/manifest.json`, asked for by the side panel (`GET_BOOKMARKS` in `sidepanel.js`) when the user picks „Fra denne browser“. If the browser will not ask from there, the options page has „Giv adgang til bogmærker“. An older extension answers „Ukendt kommando“ and the UI says to update it. |
| Narrow panel | Below 640 px (the usual side panel) a drill-down layout (`MapperNarrow.tsx`): header with back („Tilbage til …“), title and counts, search, tree overview, views (a dot when changed); breadcrumb chips; Fastgjort chips and „Alle genveje“ at the root; 52 px folder rows, 60 px shortcut rows with a 44 px open-in-tab button and PDF/Ny badges; a bottom bar „Gem denne side her“ + pin; views, sorting and the preview as bottom sheets; the tree overview as a full-panel layer; a 3-step onboarding that always imports into „Fra browseren“. Touch targets ≥ 44 px. |
| Full-screen tree | A toggle next to the tree heading: the tree fills the body (max 920 px), with „Fold alt ud“, „Fold alt sammen“ and „Vis genveje i træet“ (the open folders' shortcuts as leaves). A folder row toggles; „Åbn“ opens it; a shortcut leaves full screen, opens its folder and previews it. All three listings. |
| Dark mode | The app had none. `styles.css` now has an opt-in `.theme-auto` (follows `prefers-color-scheme`, `color-scheme: light dark`) with tokens (`--hm-surface`, `--hm-hover`, `--hm-selected`, `--hm-accent-text`, `--hm-track`, `--hm-icon`) and dark overrides for the shared components and tree rows. `ExtensionLayout` turns it on for `/extension/mapper` only; Mapper's CSS uses tokens only. |
| AI | „Foreslå emner automatisk“ is shown but disabled („Kommer senere“). |

## In the code

- `server/app/migrations/0040_mapper.sql` — `mapper_titles`, `mapper_pins`, `mapper_views`; feature `mapper` in ring 1.
- `server/app/mapper.py` — root and folders (through `app.meta`), shortcuts (`save_url`, the same audit lines as tagging), import, views, `frame_check`.
- `server/app/routes/mapper.py` — `/api/mapper`: `GET ""` (everything the tab shows), `folders` (create, rename/move, delete with `force`, `pin`), `shortcuts` (add, rename/move, remove from a folder or `everywhere`), `import`, `views`, `frame-check`.
- `web/src/mapper/` — `Mapper.tsx` (wide layout, views, list/kort/tabel), `MapperNarrow.tsx`, `GroupTree.tsx` (ARIA tree with leaves), `Preview.tsx`, `ImportWizard.tsx` (reuses `TreeView` for the check boxes), `bookmarks.ts` (Chrome tree and Netscape HTML), `model.ts` (listings, counts, sorting, built-in views), `dialogs.tsx`, `api.ts`, `mapper.css`.
- `extension/` — `GET_BOOKMARKS` in `sidepanel.js`, `optional_permissions` in `manifest.json` (version 0.2.0), the fallback button in `options.html`/`options.js`; `extension.bookmarks()` in `web/src/bridge/extension.ts`.
- Audit: `create`/`update`/`delete` on `meta_nodes`, `meta_objects` (via meta, `via: "mapper"` on objects made here), `mapper_titles`, `mapper_pins`, `mapper_views`; `mapper.import` with the counts. Event `mapper.import` (ref = the root's node id, value = shortcuts added).
- GDPR: `mapper_titles`, `mapper_pins`, `mapper_views` in the export; erasure cascades (shared views of an erased user go too). The folders and shortcuts are the private tree and its objects, covered by meta.
- Tests: `server/tests/test_mapper.py`.

## Not yet

- Drag-and-drop ordering of shortcuts and folders, and moving folders from the UI (the API takes `parent_id`).
- Sharing views with a group instead of everyone; views in `/admin/data/transfer`.
- AI suggestions of Emner on import.
- Mapper on `/m` (the narrow layout is ready for it; the route is not there).
- Live favicons (not fetched on purpose: nothing goes to other sites before the user opens a page).
- The frame check is best effort: a page may still refuse a frame in the browser (for example a `frame-ancestors` that lists our origin but not the extension), or a site may answer the check with a bot wall; „Prøv alligevel“ covers both.
