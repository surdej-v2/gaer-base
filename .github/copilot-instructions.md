# GitHub Copilot instructions

Read `.gaer/agents/instructions.md` before making changes — it is the shared source for Claude and Copilot.
For the area you work in, also read the matching skill in `.gaer/agents/skills/*/SKILL.md`.

The prompt files in `.github/prompts/gaer-*.prompt.md` run the workflows in `.gaer/agents/workflows/`.

The short version:
- One container: FastAPI server (`server/`) + Vite/React frontend (`web/`), shared managed Postgres, OVHcloud Object Storage.
- Routes (full table in `.gaer/agents/instructions.md`): `/` app (anonymous → `/w/`) · `/w` public website with login
  card · `/login` · `/admin` · `/m` mobile PWA · `/extension` browser side panel · `/office-addin` Office router →
  `/extension/tools/*` · `/api` · `/health`.
- Login is phone + one-time code, three grace logins, then passkey. Never bypass it (`gaer-auth`).
- Every data change writes `audit()` in the same transaction; logs are append-only (`gaer-audit`).
- New migrations are new SQL files in `server/app/migrations/`.
- Personal data must be in the GDPR export and removed on erasure.
- `pytest server` and `npm run build --prefix web` must pass. UI text is Danish.
