# AI i Gær Developer — chat og forslag overalt

Status: fase 0 er bygget (backend). Fase 1–6 er planlagt. Designet ligger på canvasset „Developer med AI“
(artboards: Oversigt, Pipeline-editor, ⌘K, MCP-testbænk, Action-studie, API-dokumentation, AI-mønstre, telefon).

## Mål

AI-chat og AI-forslag i alle studier i `/developer`, bygget på det AI-lag, der findes (`app/ai.py`,
`app/devkit/assist.py`, `web/src/ai.tsx`) — ikke et nyt.

## Regler (gælder alle faser)

- **AI-1 Kun metadata og maskerede rækker.** Konteksten bygges af serveren ud fra `{kind, id}`; fri tekst fra browseren
  kommer aldrig ind i prompten (som `page` i `app/ai.py`). Felter sendes med navn, type og klassifikation, aldrig værdier.
  Fejltekster sendes med citerede værdier fjernet. Definitioner (pipelines, flows) sendes som kode; forbindelser og
  API-nøgler kun med navn og status.
- **AI-2 AI gemmer aldrig selv.** Forslag lægges i editoren som kladde; brugeren gemmer med de eksisterende endpoints,
  som logger i audit-loggen. Serveren afgør stadig patch/minor/major.
- **AI-3 Én adgangskontrol.** `consumption.check()` bruges af `/api/ai/chat`, `/api/admin/dev/ai/chat` og
  `assist.complete()`: AI slået til, kill switch `ai`, brugerens grænse og budget. Forbrug tæller på surface `developer`.
- **AI-4 Alt virker uden AI.** Forslag findes af regler i koden; AI skriver kun forklaringer og kladder.
  UI skjuler AI-knapper, når `DevMe.ai` er false, og viser „(AI er slået fra)“.
- **AI-5 Prompts gemmes ikke** (som i `app/ai.py`). Afviste forslag gemmes pr. bruger (`dev_ai_dismissals`), er med i
  GDPR-eksporten, slettes med brugeren og ryddes efter `retention_policies`.

## Fase 0 — fundament (bygget)

| Del | Hvor |
| --- | --- |
| Fælles adgangskontrol | `consumption.check(conn, user_id)` → `None` eller `(status, tekst)`; brugt af `routes/ai.py`, `routes/developer_ai.py` og `devkit/assist.complete` |
| Surface `developer` | `ai.Surface`, `ai.SURFACE_TEXT`, `ai.TOOLS["developer"]` (registreres af `devkit/ai_tools.py`), `ai.pages_for` giver /developer-siderne; `/api/ai/chat` afviser surface `developer` |
| Kontekst fra serveren | `devkit/ai_context.py`: `overview`, `artifact:<id>`, `dataset:<id>`, `run:<id>` → `(label, tekst, chips)` |
| Chat | `POST /api/admin/dev/ai/chat` `{messages, ref: {kind, id}, page}` → samme SSE-strøm som `/api/ai/chat` |
| Kontekst-chips | `GET /api/admin/dev/ai/context?kind=…&id=…` → `{label, chips}` til „Kender:“ i panelet |
| Værktøjer | `devkit/ai_tools.py`: læse-værktøjer (`catalog_search`, `dataset_get`, `lineage`, `artifact_get`, `run_get`) og forslags-værktøjer (`propose_pipeline_change`, `propose_testcases`, `propose_doc_text`, `propose_flow_steps`). Browseren kører dem: læsning mod `/api/admin/dev/…` med brugerens session, forslag som kladde i editoren |
| Forslag | `devkit/ai_suggestions.py` + `GET /api/admin/dev/ai/suggestions?scope=overview\|artifact:<id>`: kørsler der fejlede, datasæt uden beskrivelse, MCP-endpoints uden testcases |
| Afvisninger | `POST /api/admin/dev/ai/suggestions/dismiss`, `DELETE …/dismiss/{key}` (fortryd); tabel `dev_ai_dismissals` (migration 0043), audit `dev.ai.suggestion.dismissed` / `.restored`, event `developer.ai.dismissed`, retention `dev_ai_dismissals` |

## Fase 1 — shell, oversigt og ⌘K (frontend)

- `web/src/developer/ai/`: `AiProvider` (åbent/lukket, ⌘J, `useAiContext({kind, id, label})`), `AiDock` (bygger på `Chat`
  fra `ai.tsx`), mønsterkomponenter `Suggestion`, `InlineSuggestion`, `GhostText`, `ExplainButton`, `AiDiff`,
  `AiBudgetNotice`; styling `.dev-ai-*` i `developer.css`.
- `DeveloperShell.tsx`: „Spørg AI“ i topbaren, punkt i sidebarens bund, ⌘J; paletten svarer på spørgsmål („Svar fra AI“).
- `Overview.tsx`: „Hvad vil du bygge?“ (`POST /api/admin/dev/ai/route`), rækken „AI foreslår“, AI-resumé på det, der
  venter (kolonne `ai_summary` på `dev_artifact_versions`).
- Ingen nye routes — panelet er ikke en side.

## Fase 2 — Pipeline-editor

- Regel i compileren: `$match` på felter, der ikke kommer fra `$lookup`, kan flyttes tidligere → advarsel med en ændring.
- Grå tekst: `POST /api/admin/dev/pipelines/{id}/complete` (trin + katalogets kolonner).
- „Forklar trinnet“, „Forklar fejlen“ (fra `dev_pipeline_run_phases`), ændringsnote fra kontraktforskellen.

## Fase 3 — MCP-testbænk

- Foreslåede testcases fra kaldsloggen og tools uden test; kontroller i `dev_mcp_testcases.expect`
  (`tool`, `args`, `answer_contains`, `masked`); omskrevet uden personoplysninger.
- Forklaring af en fejlet testcase (toolbeskrivelse udgivet vs. kladde), dækning pr. tool, „Lav 3 varianter“.

## Fase 4 — API-dokumentation

- Tabel `dev_api_docs (api_id, major, section, text, status, source, approved_by, approved_at)`, godkendelse i audit-loggen.
- Job `developer.docs` skriver teksterne, når en version godkendes; eksempler fra beståede testcases; vejledning til skift af
  major fra `contracts.py`; afsnit om fejl fra `dev_api_log` (kun stier som skabeloner, aldrig nøgler, IP-adresser, bruger-id'er).

## Fase 5 — Action-studie

- `assist.draft_flow(description)` → udløser og trin, valideret af flow-validatoren; trin tages ét ad gangen.
- Risikotjek som regler: approval uden frist, gruppe med ét medlem, personfelter i mail mod klassifikationen.

## Fase 6 — telefon

- Under 768 px bliver `AiDock` et ark nedefra (lukket, halvt, fuldt).

## Tests

`server/tests/test_developer_ai.py`: kontekst uden værdier, citerede værdier fjernet fra fejltekster, budget nået → 429
(også i `assist.complete`), AI slået fra → 404 og forslag virker stadig, `/api/ai/chat` afviser surface `developer`,
afvisning og fortryd med audit, afvisninger i GDPR-eksporten og slettet med brugeren.
