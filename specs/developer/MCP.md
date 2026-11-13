# MCP-studie (fase 4): test, adgang og drift

Code: `app/devkit/mcp_studio.py`, `app/routes/developer_mcp.py` (`/api/admin/dev/mcp`), `web/src/developer/McpStudie.tsx`,
migration 0034.

| Req | How |
| --- | --- |
| MCP-1 | `/developer/mcp`: every endpoint (built-in, made in code in MCP Studio, made in Datastudie) with slug, name, audience, version, owners (A and R) and status |
| MCP-2 | Endpoints are made by Udvikler, Udvikler-admin and admins with Data/Fuld (the admin section `/developer`) |
| MCP-3 … MCP-6 | see RUNTIME.md (operation tools, levels, drafts for acting tools are the existing `@draft` tools) |
| MCP-7 | "Tjek beskrivelse" (`/datastudie/check-tool`): AI when on, a length and examples check otherwise |
| MCP-9 | Testbænk → Direkte kald: as yourself or one of your test accounts (`/admin/impersonation`; an external test account sees what an external user sees), against the published version or the draft (an operation tool runs the operation's draft), with the answer, masked fields, time and cache status |
| MCP-10 | AI-test: the endpoint's tool definitions and instructions go to the AI layer, the tools it picks run as the chosen account (at most 4). Saved test cases: direct (tool, arguments, `min_rows`, `contains`) or AI (question, expected tools and arguments, `answer_contains`). A major of an endpoint cannot be submitted until every saved test case has passed against that draft (an artifact gate); test cases for a tool that is not in the draft fail |
| MCP-11 | Clients log in with OAuth against Gær base (unchanged); the user's rights and grants apply |
| MCP-12 | Klienter: who used the endpoint with which client, calls today, revoke (the client's sessions for that user; the token has scope `mcp`, so it ends that client's access to every `mcp` endpoint — said in the UI). Access requests for the endpoint are listed and handled in /admin/access |
| MCP-13 | The endpoint page in Datastudie shows the MCP URL with a copy button and how to add it as a connector |
| MCP-14 | Every call is logged in `mcp_log` (arguments on non-white fields masked, cache status). Job `developer.metrics` (every minute) aggregates per minute into `dev_call_metrics`; Overvågning shows calls today, error rate, p50/p95 and cache hit rate |
| MCP-15 | Alarms to R: error rate over the endpoint's limit (default 5 %, at least 20 calls in 15 min) and unusual use (over 5× the normal 15-minute volume and 100 calls); at most once an hour per kind. Rate limits per endpoint, user and client (calls per minute) in `mcp_endpoints.limits`, token buckets (Redis or Postgres) |

Not built: external MCP servers (MCP-8) — see README.md.
