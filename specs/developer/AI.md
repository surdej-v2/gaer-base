# AI i Gær Developer — chat og forslag overalt

Status: fase 0 (backend), fase 1 (shell, oversigt, ⌘K), fase 2 (pipeline-editoren), fase 3 (MCP-testbænken) og fase 4 (API-dokumentationen) er bygget. Fase 5–6 er planlagt. Designet ligger på canvasset „Developer med AI“
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

## Fase 1 — shell, oversigt og ⌘K (bygget)

| Del | Hvor |
| --- | --- |
| Panelet | `web/src/developer/ai/`: `context.tsx` (`AiProvider`, `useDevAi`, `useAiContext(kind, id)`, `useAiProposalHandler(tool, fn)`), `AiDock.tsx` (panelet: „Kender:“-chips fra `/api/admin/dev/ai/context`, samtalen med `Chat` fra `ai.tsx` mod `/api/admin/dev/ai/chat`, forslag som ingen editor tog), `tools.ts` (værktøjerne i browseren), `patterns.tsx` (`AskAiButton`, `ExplainButton`, `AiSuggestions`); styling `.dev-ai-*` i `developer.css`. På telefon er panelet et ark nedefra |
| Shell | `DeveloperShell.tsx`: „Spørg AI“ i topbaren, „AI-assistent“ i sidebarens bund, ⌘J, Esc lukker; ⌘K tilbyder „Spørg AI: …“ — først når forespørgslen ligner et spørgsmål, ellers sidst; Tab spørger med det samme. Alt skjules, når `/api/ai/status` siger `enabled: false` |
| Sidernes kontekst | `useAiContext` på Oversigt, Pipeline-editor, Kørsel, Datasæt, Artefakt, Endpoint, Operation, Multisource, API og Flow |
| Oversigt | „Hvad vil du bygge?“ → `POST /api/admin/dev/ai/route` (nøgleord, AI når den er slået til) → studiets side med `?beskrivelse=` (Ny pipeline og Datastudie udfylder beskrivelsen, Nyt flow viser den, API-studie åbner formularen); „AI foreslår“ (`AiSuggestions`); „Venter på dig“ viser ændringerne og AI-resuméet; „Forklar“ på beskeder om en kørsel |
| AI-resumé | Migration 0044: `dev_artifact_versions.ai_summary`; `artifacts.submit` lægger jobbet `developer.ai_summary` i kø, når en version sendes til godkendelse og AI er sat op; `assist.summarize_change` (kun kontraktændringerne); forbruget tæller på den, der sendte |
| Pipeline | „Forklar fejlen“ på en fejlet kørsel; forslag for pipelinen øverst i editoren |

Undervejs rettet: `Chat` i `ai.tsx` returnerede værdien af `scrollIntoView` fra en effekt; nyere browsere giver et Promise
tilbage, og React fejlede, når en samtale blev lukket.

## Fase 2 — Pipeline-editor (bygget)

| Del | Hvor |
| --- | --- |
| Regler | `devkit/pipeline_hints.py`: `hints()` — en `$match`, der kun læser felter fra før et `$lookup`, kan flyttes op over det (opslaget er en left join, der aldrig overskriver en kolonne, så rækkerne er de samme; testen sammenligner forhåndsvisningerne); `describe()` — hvad et trin gør på dansk; `heuristic_completion()` — grå tekst: læg et tal sammen, som ikke er aggregeret endnu (`$group`), eller tag et felt med, der mangler (`$project`) |
| Endpoints | `POST /api/admin/dev/pipelines/help` (regler, ingen AI: forslag, beskrivelser, grå tekst for det valgte trin); `POST /api/admin/dev/pipelines/complete` („Andet forslag“: AI's tilføjelse, beholdt kun hvis pipelinen stadig kan kompileres, ellers reglens); `POST /api/admin/dev/pipelines/{id}/note` (ændringsnote ud fra kontraktforskellen mod den udgivne version — serverens patch/minor/major, teksten skrevet af AI eller listen af ændringer) |
| Editoren | `Pipeline.tsx`: forslag med stiplet kant over trinene („Brug“ / „Afvis“), beskrivelse under det valgte trin, grå tekst under feltet (Tab tager den, Esc skjuler, „Andet forslag“ med AI), „Forklar trinnet“, „Forklar fejlen“ når seneste kørsel fejlede, ændringsnote der følger versionen til godkendelse. Et `propose_pipeline_change` for den åbne pipeline lægges i editoren med „Fortryd“ (`useAiProposalHandler`; for en anden pipeline bliver det i panelet). Intet gemmes, før brugeren trykker Gem kladde eller Send til godkendelse |

## Fase 3 — MCP-testbænk (bygget)

| Del | Hvor |
| --- | --- |
| Forslag | `devkit/mcp_testgen.py`, regler på det, der kendes uden at se data: endpointets tools og parametre, antal kald pr. tool i `mcp_log` (kun antal; de loggede værdier er maskerede), de gemte testcases og testkontiene. Tre slags: *tool uden test* (et direkte kald med toolets første eksempel, eller uden argumenter, når intet er påkrævet — ellers udfylder brugeren dem), *kanttilfælde* (en ukendt værdi i en tekst-parameter skal give et tomt svar, ikke en fejl), *adgang* (et gult eller rødt tool kaldt som ekstern testkonto må ikke vise personfelter i klartekst; uden en ekstern testkonto siger forslaget, at den skal oprettes). Platformens starter-tools (`whoami`, `access_*`, kladder) er ikke med |
| Nye kontroller | `mcp_studio.judge`: `max_rows`, `protected` (afvist, ingen rækker eller maskerede felter) og `only_tools` (AI kalder ingen andre tools) ud over `min_rows`, `contains`, `tools`, `args`, `answer_contains` |
| AI | `ai_questions()` skriver et naturligt spørgsmål pr. tool uden test (kun når brugeren beder om det); `variants()` omskriver et spørgsmål; `explain()` forklarer en fejlet testcase ud fra problemerne og forskellen i toolbeskrivelser mellem udgivet version og kladde (uden AI: problemerne og „Beskrivelsen af X er ændret i kladden“). AI-testen i testbænken går nu gennem `consumption.check` og tæller på udvikleren (surface `developer`), også når den kører som testkonto |
| Endpoints | `GET /api/admin/dev/mcp/{key}/suggestions[?ai=true]` (dækning pr. tool + forslag), `POST /api/admin/dev/mcp/{key}/variants`, `GET /api/admin/dev/mcp/{key}/testcases/{id}/explain`. Forslag gemmes med det eksisterende `POST /{key}/testcases`, som logger i audit-loggen |
| Testbænken | `McpStudie.tsx`: på fanen Gemte testcases dækning pr. tool og forslag med flueben og redigerbare argumenter („Tilføj N valgte“, „Lav spørgsmål med AI“), „Forklar“ på en fejlet testcase med forskellen i beskrivelsen og et link til endpointet; i AI-testen kontroller at gemme med (samme argumenter, ingen andre tools, svaret skal nævne …) og „Lav 3 varianter af spørgsmålet“ |

## Fase 4 — API-dokumentation (bygget)

| Del | Hvor |
| --- | --- |
| Tabel | Migration 0045: `dev_api_docs (api_artifact_id, major, section, draft_text, draft_source rule\|ai\|human, approved_text, approved_by, approved_at)`. Afsnit: `intro` (Kom i gang), `operation:<METHOD> <path>`, `migration` (fra majoren før) og `errors` (Fejl og grænser). Godkendte tekster er med i GDPR-eksporten (`developer_api_docs_approved`); `approved_by` bliver null ved sletning |
| Regler | `devkit/api_docs.py` skriver hvert afsnit ud fra det, der er kendt: definitionen og kontrakten (operationer, parametre, felter, hvilke felter der maskeres, limit, Idempotency-Key), kontraktforskellen til majoren før, dens udfasningsdato og antallet af nøgler, der stadig kalder den, og fejl pr. status i `dev_api_log` de seneste 30 dage (kun antal og andel — aldrig nøgler, IP-adresser, bruger-id'er eller konkrete stier). Kun kald, der når en operation, logges; auth-fejl og ukendte stier er derfor ikke i tallene |
| AI | `ai_texts()` omskriver regelteksterne med de samme fakta (samme nøgler, ingen nye afsnit); uden AI står regelteksterne |
| Kladder og godkendelse | Jobbet `developer.docs` skriver kladder, når en version af et API udgives (`apis.on_publish`); „Skriv alt igen“ gør det med det samme. Et afsnit, hvor kladden er den godkendte tekst, eller hvor en person har en kladde, overskrives ikke. En ejer (A eller R) godkender; alle kan rette (kladde fra en person) og afvise. Godkendelse, rettelse, afvisning og „Skriv alt igen“ logges i audit-loggen (`dev.api.docs.*`) |
| Udgivelse | `api_docs.apply()` lægger de godkendte tekster i OpenAPI-dokumentet på `/gaer/v<major>/<slug>/openapi.json`: Kom i gang som `info.description`, en operations tekst som dens `description`, vejledningerne som `info.x-gaer-guides` |
| Eksempler | curl, Python og TypeScript pr. operation, lavet ud fra OpenAPI-dokumentet og operationens første eksempel |
| Endpoints og UI | `GET /api/admin/dev/apis/{id}/docs`, `POST …/docs/generate`, `POST …/docs/{approve\|edit\|reject}`; siden Dokumentation (`ApiStudie.tsx`) viser afsnittene med godkendt tekst og kladde, Godkend/Ret/Afvis, eksemplerne, nøglerne på den gamle major (kun i admin) og fejltallene. Et `propose_doc_text` for det åbne API lægges i afsnittets editor, ikke gemt |

## Fase 5 — Action-studie

- `assist.draft_flow(description)` → udløser og trin, valideret af flow-validatoren; trin tages ét ad gangen.
- Risikotjek som regler: approval uden frist, gruppe med ét medlem, personfelter i mail mod klassifikationen.

## Fase 6 — telefon

- Under 768 px bliver `AiDock` et ark nedefra (lukket, halvt, fuldt).

## Tests

`server/tests/test_developer_ai.py`: kontekst uden værdier, valg af studie med og uden AI, AI-resumé på en version, der venter, i `test_developer_ai_pipeline.py` flytningen af en `$match` (samme rækker), beskrivelser, grå tekst med og uden AI og ændringsnoten, og i `test_developer_ai_mcp.py` forslag, dækning, de nye kontroller, AI-spørgsmål og varianter, forklaringen af en fejlet testcase og budgettet i AI-testen, og i `test_developer_ai_apidocs.py` kladder ud fra reglerne, godkendelse og udgivelse i OpenAPI, vejledningen til en ny major, fejl ud fra tal og AI-omskrivning, citerede værdier fjernet fra fejltekster, budget nået → 429
(også i `assist.complete`), AI slået fra → 404 og forslag virker stadig, `/api/ai/chat` afviser surface `developer`,
afvisning og fortryd med audit, afvisninger i GDPR-eksporten og slettet med brugeren.
