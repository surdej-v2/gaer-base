# Handoff: Viden — vidensrum, indeksering og agenter

## Overview
En ny funktion **Viden** (kodenavn `knowledge`), der bringer OpsPilot fra Surdej v1
(`modules/member-opspilot`) ind i Gær i en generisk form:

1. En admin opretter et **vidensrum** og uploader filer — enkeltvis eller som én ZIP.
2. Serveren **udtrækker tekst** (PDF, Word, PowerPoint, Excel, CSV, HTML, Markdown, tekst), deler den i
   afsnit med overskrifter og **indekserer** dem: fuldtekst i Postgres altid, og vektorer når en
   embedding-model er sat op.
3. En admin bygger en **agent** uden kode: instruktioner, hvilke vidensrum den må bruge, startspørgsmål og en
   velkomst. Agenten afprøves, udgives som en uforanderlig version og får sin egen rute.
4. Brugere chatter med agenten på **`/extension/agent/:slug`** — i browserudvidelsens sidepanel eller i en fane.
   Hvert svar kommer kun fra de udvalgte vidensrum og har **kildehenvisninger**, der åbner originalfilen.

Design: Design-canvasset „Knowledge“ (https://claude.ai/artifact/HLsiog3NktMrZQhk3xJtBU) med siderne
*Architecture*, *Admin · /admin/knowledge* og *Agent · /extension/agent/\**. Canvasset er privat, indtil
ejeren deler det. Tekster i canvasset er engelske og eksempeldata; UI'et i Gær er dansk.

## Hvad der er anderledes end Surdej v1
| Surdej v1 (OpsPilot) | Gær (Viden) |
| --- | --- |
| Egen Node-worker, Prisma, NATS | Routes i `server/app/routes/knowledge*.py`, raw SQL, `app.jobs` |
| pgvector (`vector`-kolonne) | `real[]` med normaliserede vektorer; cosinus = prikprodukt i SQL. Ingen udvidelse kræves, så `postgres:16` virker uændret. Fuldtekst (`tsvector`) altid som fallback |
| Udtræk via egne extractor-containere | Udtræk i Python: standardbiblioteket for Office/HTML/tekst, `pypdf` for PDF |
| Azure OpenAI | OVHcloud AI Endpoints (OpenAI-kompatibelt, samme `AI_*`-indstillinger som AI-chatten). Uden `AI_API_KEY` svarer agenten med de mest relevante uddrag i stedet for en formuleret tekst |
| Bytes i databasen | Originalfiler i Object Storage (`knowledge/<uuid>`), aldrig i databasen |
| KOBI-UI på egen sti | `/extension/agent/:slug` i appen på `/`, `chrome: own`, kan indlejres |

## Data (migration `0015_knowledge.sql`)
| Tabel | Indhold |
| --- | --- |
| `knowledge_spaces` | navn, beskrivelse, oprettet af, `deleted_at` (papirkurv) |
| `knowledge_documents` | én fil: navn, sti i ZIP, type, størrelse, `storage_key`, sha256, status (`pending`, `processing`, `indexed`, `skipped`, `failed`, `error`), fejltekst, sider, afsnit |
| `knowledge_texts` | den fulde udtrukne Markdown pr. dokument, så man kan se præcis hvad der blev indekseret |
| `knowledge_chunks` | afsnit: rækkefølge, overskrift, side, tekst, tokens, `tsv` (genereret), `embedding real[]`, model |
| `knowledge_ingest_log` | kun-tilføjelse-log pr. vidensrum og dokument: niveau, trin, besked |
| `knowledge_agents` | arbejdskopien: slug, navn, beskrivelse, instruktioner, `space_ids`, startspørgsmål, velkomst, adgang (`all`/`members`), status, version |
| `knowledge_agent_versions` | uforanderligt snapshot pr. udgivelse (`major.minor`, note) — det er snapshottet, brugerne chatter med |
| `knowledge_agent_members` | bruger + rettighed (`use`/`manage`) når adgang er `members` |
| `knowledge_conversations`, `knowledge_messages` | brugerens samtaler med en agent, med kildehenvisninger pr. svar |

Personlige data: samtaler, beskeder og medlemskaber er brugerens og er med i GDPR-eksporten; de slettes via
`on delete cascade`, når brugeren slettes. `created_by`/`uploaded_by` sættes til `null`.

Opbevaring (`retention_policies`): `knowledge_ingest_log` (30 dage), `knowledge_conversations` (180 dage efter
seneste besked). Vidensrum i papirkurven slettes endeligt efter 30 dage af jobbet `knowledge.bin` (dagligt),
som også fjerner filerne i Object Storage.

## Pipeline (job `knowledge.ingest`)
Upload → filen gemmes i Object Storage → `knowledge_documents` (`pending`) → job i samme transaktion. En ZIP pakkes
ud på serveren (højst 1.000 filer, højst 200 MB udpakket, stier renses), og hver fil bliver sit eget dokument og job.

Pr. dokument:
1. **Udtræk** → Markdown. PDF får sidemarkører, så henvisninger kan pege på en side. Ukendt format → `skipped`.
   Krypteret eller ødelagt fil → `failed` (prøves ikke igen). Andre fejl → jobbet prøves igen (backoff i `app.jobs`).
2. **Afsnit**: overskriftsbevidst opdeling, ca. 500 tokens med 50 tokens overlap; for store blokke (tabeller) skæres hårdt.
3. **Embeddings** (valgfrit): `POST {AI_BASE_URL}/embeddings` med `KNOWLEDGE_EMBED_MODEL` i bundter af 32.
   Fejler det, indekseres dokumentet med fuldtekst alene, og loggen får en advarsel.
4. **Status** `indexed` med antal sider og afsnit. Kørslen er idempotent: tidligere afsnit slettes først.

Hvert trin skriver i `knowledge_ingest_log`, som vises live under vidensrummet.

## Svar (samme kode for preview, appen og senere API/MCP)
1. Spørgsmålet embeddes (hvis vektorer findes i agentens rum), ellers bygges en fuldtekst-forespørgsel.
2. De 8 bedste afsnit i agentens vidensrum hentes; vektor-træf under 0,2 i cosinus droppes.
3. Systemprompt = agentens instruktioner + den faste regel, som instruktionerne ikke kan fjerne:
   *brug kun de nummererede uddrag, henvis med [1], [2] …, og sig det ærligt, hvis svaret ikke står der*.
4. Svaret gemmes med én kildehenvisning pr. dokument: dokument, overskrift og side.
5. AI-forbrug tæller med i brugerens månedlige kvote (`ai_usage`, surface `knowledge`).

## Ruter
| Rute | Hvem | Hvad |
| --- | --- | --- |
| `/admin/knowledge` | admin (sektion `/knowledge`, rolle Indhold eller Fuld) | Overblik: tal, vidensrum, agenter, papirkurv |
| `/admin/knowledge/spaces/:id` | admin | Upload (filer eller ZIP), dokumenter med status, prøv igen, udtrukket tekst, log |
| `/admin/knowledge/agents/:id` | admin | Agent-bygger: identitet, rute, instruktioner, vidensrum, startspørgsmål, adgang og medlemmer, udgiv, versioner, „Prøv agenten“ |
| `/extension/agent` | logget ind | De agenter, brugeren har adgang til |
| `/extension/agent/:slug` | logget ind + adgang til agenten | Chat med samtalehistorik og kildehenvisninger. Kan indlejres i sidepanelet |

API:
- `/api/admin/knowledge/…` — alt i admin; `require_admin`, auditeret.
- `/api/knowledge/agents/…` — brugerens side: agentens profil, spørg, samtaler, link til kildefil.
  Kun udgivne agenter; adgang `all` = alle loggede ind, `members` = medlemmer (og admins).

## Audit
`create`/`update`/`delete` på vidensrum, dokumenter, agenter og medlemmer; `knowledge.publish`,
`knowledge.promote`, `knowledge.restore`, `knowledge.purge`, `file.upload` for hver uploadet fil. Spørgsmål logges
ikke i audit-loggen (de er brugerens samtale), men tæller i `ai_usage`.

## Faser
- **Fase 1 (denne PR)**: data, pipeline, fuldtekst + vektorer, admin-sider, agent-bygger med versioner og
  medlemmer, `/extension/agent/:slug`, GDPR, opbevaring, tests.
- **Fase 2**: testcases med regex-påstande og testkørsler (canvas: *Test cases*, *Test runner*), feedback
  („Rapportér et problem“), billedbeskrivelser med vision-model, kildeviser med sidevisning, statistik pr. agent
  (canvas: *Dashboard*), konfiguration for agent-managere direkte på agentens rute.
- **Fase 3**: agenter som MCP-værktøjer på `/mcp` og nøglebaseret API, flere kilder (dokumentbibliotek, web-crawl).
