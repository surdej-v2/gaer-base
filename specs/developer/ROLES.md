# Udvikler and Udvikler-admin (DS-2, MCP-2, afsnit 10 "Roller")

## Choice: two new admin roles

Gær already has one model for "who may use which part of the administration": `users.role = 'admin'` plus one or
more **admin roles** in `users.admin_roles` (`server/app/admin_roles.py`). The least invasive way to add the spec's
two roles is to make them admin roles:

| Role | Opens | May also |
| --- | --- | --- |
| `udvikler` (Udvikler) | Statistik and `/developer` (admin section `/developer`, API `/api/admin/studio` and `/api/admin/dev`) | create and change drafts of datasets, pipelines, operations, multisource sources, MCP endpoints and APIs; submit them |
| `udvikler_admin` (Udvikler-admin) | the same | create and change connections, external MCP servers and API keys, and give or take the role Udvikler |
| `data` (existing) | as before, and `/developer` | counts as Udvikler |
| `fuld` (existing) | everything | counts as Udvikler-admin, and is the only one who gives Udvikler-admin |

Why this and not a separate column:

- `require_admin` already does MFA for /admin (second factor after the grace logins), audits every read
  (`admin.read`) and checks the section table per path. A developer works with connections, secrets and personal
  data, so the same protection is right.
- One table (`SECTIONS` + `ROLE_SECTIONS`) still drives both the menu (`admin_sections` on `/api/auth/me`) and the
  check, so they cannot drift apart.
- Nothing changes for existing admins.

What an admin with only `udvikler` sees in `/admin`: Statistik (open to every admin) — nothing else.

## Rules

- **Udvikler-admin only** (`admin_roles.DEV_ADMIN_WRITES`): changes (not reads) under `/api/admin/dev/connections`,
  `/api/admin/dev/upstreams`, `/api/admin/dev/developers` and `/api/admin/dev/api-keys`.
- **External users never get the roles.** `PUT /api/admin/dev/developers/{id}` refuses them, and a trigger
  (`users_developer_roles`, migration 0031) refuses any write that gives an external user `udvikler` or
  `udvikler_admin` — also from /admin/users or SQL. To make a developer external, take the role first.
- **Giving the role.** `PUT /api/admin/dev/developers/{id}` with `none | udvikler | udvikler_admin`, step-up (a second
  factor within `STEPUP_MINUTES`). A plain internal user who gets Udvikler becomes an admin with only that role;
  taking it again makes them a user if no other admin role is left. Udvikler-admin cannot change a Fuld admin or
  another Udvikler-admin, and nobody changes their own roles here. Audited as `role.changed` with `via: developer`.
- **Publishing always goes through approval** when the artifact's approval flow is on; the role decides who may make
  drafts, RACI decides who approves.
