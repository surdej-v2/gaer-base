# Navigation in Gær Developer (afsnit 7, NAV-1 … NAV-13)

`/developer` is its own universe: the route `/developer` has `chrome: "own"` in `web/src/routes.json`, so the app's
shell and top bar step aside and `web/src/developer/DeveloperShell.tsx` draws the sidebar, top bar and palette.

The pattern of the /admin side menu (commit a14ebb0, `web/src/admin-app/SideMenu.tsx`) is reused: fixed top (mark,
collapse button, search) and bottom (Hjælp, Til Gær base), only the menu scrolls, sticky group headings, ⌘K. The two
apps share no code (`appBoundary()` in `web/vite.config.ts`), so it is a copy of the pattern, not an import.

| Req | How |
| --- | --- |
| NAV-1 | One sidebar, two levels: the global items (`nav.ts` → `NAV`) and the active item's local pages, unfolded under it |
| NAV-2 | Studier: Oversigt, Datastudie, Pipeline, MCP-studie, API-studie. Fælles: Datakatalog, Forbindelser, Godkendelser, Eksport og import. Hemmeligheder is added when the Secrets studio exists |
| NAV-3 | Local pages per item as in the spec's table. MCP-studie → Endpoints is the existing MCP Studio (`/developer/studio/mcp`), Forbindelser → Live-kilder the existing API Studio (`/developer/studio/api`); the classic Studio, the prompt catalogue and the tree examples sit under Hjælp |
| NAV-4 | Top: "Gær Developer", collapse button, search (⌘K / Ctrl+K). Bottom: Hjælp, Til Gær base. The top bar shows a breadcrumb (`breadcrumb()`; pages add the last part with `useCrumb`) |
| NAV-5 | A handle on the right edge changes the width between 64 and 320 px; under 180 px the texts go and the bar jumps to 64 px |
| NAV-6 | The button, ⌘B / Ctrl+B and a double-click on the handle switch between narrow and the last wide width |
| NAV-7 | Under 1024 px the bar goes narrow by itself and widens again when there is room, unless the user collapsed it |
| NAV-8 | Width and state are saved per user (localStorage key `gaer.developer.sidebar.<user id>`) — per user and browser; no server round trip for a layout preference |
| NAV-9 | Top and bottom fixed; the menu scrolls with a thin scrollbar and a soft shadow at the edge it can scroll towards; the active item is scrolled into view |
| NAV-10 | Godkendelser carries a dot when versions wait for the user's approval or reviews wait for them as owner — also in the narrow bar |
| NAV-11 | ⌘K opens a palette: every page and the main actions (static), plus artifacts and datasets from `/api/admin/dev/search` |
| NAV-12 | `nav` landmarks (sidebar, breadcrumb), `aria-current="page"`, `aria-expanded` on unfolded items, labels and tooltips in the narrow bar (also on keyboard focus: the flyout opens on focus), the handle is a focusable `separator` moved with the arrow keys |
| NAV-13 | Icons are `lucide-react`, the icon set Gær already uses |

Mobile (under 768 px): the bar is hidden behind a menu button in the top bar and opens as a sheet over the content.
