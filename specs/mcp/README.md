# Handoff: Gær MCP-servere

To MCP-servere i Gær-containeren:

- **`/mcp` — appen.** For alle brugere. Claude, Copilot eller ChatGPT kan læse og gemme brugerens egne klip
  og filer, se hvilke features brugeren har, og bede om en GDPR-eksport. Altid som den bruger, der loggede ind.
- **`/mcp/admin` — admin af appen.** Kun admin-rollen. Statistik, brugere og roller, efterligning,
  features og ringe, logs, GDPR, indstillinger og en læse-database. Alle ændringer kommer tilbage som en
  kladde, der skal bekræftes med `confirm_draft`.

Begge bruger den almindelige Gær-login (telefon, engangskode, passkey) via OAuth 2.0 PKCE. Ingen ny login-vej.
Modellen er taget fra happy-groups PR 14 (superadmin-MCP) og tilpasset Gær: Python, én container, én
instans pr. kunde, roller `user` og `admin`.

| Fil | Hvad |
|---|---|
| `README.md` | Denne fil |
| `SPEC.md` | Backend-kontrakt: endpoints, auth, kladder, audit, deep links, database, verify |
| `catalog.json` | Kilden til sandhed: servere, domæner, 49 værktøjer, eksempel-prompts, deep links |
| `CLAUDE_CODE_PROMPT.md` | Startprompt til Claude Code med rækkefølge og regler |

Designet til /admin-hubben er sektionen *MCP-servere* i `ToerGaer Admin.dc.html`. Det er en prototype
med mock-data — byg den i `web/src/admin/` med appens egne komponenter.

## Hub-skærmen (`/admin/mcp`)
- **Serverskifter** øverst: Appen (`/mcp`) / Admin (`/mcp/admin`). Alt nedenfor følger valget.
- **Oversigt:** endpoint, scope, hvem der må, skrivninger, tal (værktøjer, læs, kladde, med deep link),
  "Prøv at spørge" med værktøjschips, domænekort.
- **Værktøjer:** søgning, filter Alle / Læs / Skriv / Kladde, domænechips; inspektør med parametre,
  deep link der opdateres mens man skriver, Kør / Opret kladde, resultat-panel og kladdekort med årsag.
- **Kladder:** alle kladder med status (afventer, bekræftet, annulleret, udløbet).
- **Monitor:** live kald (tid, værktøj, bruger, status, ms) med filter og Live/Pause.
- **Forbind:** Claude Code, Claude Desktop og VS Code (`.vscode/mcp.json`) med kopiér-knap.
