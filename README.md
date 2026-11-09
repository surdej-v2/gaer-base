# Gær — Surdej i letvægt

Én container med en Python-server og en Vite-frontend, koblet til managed Postgres og Object Storage på
OVHcloud. Login med e-mail og engangskode, passkeys, sikkerhedsnøgler og godkendelsesapps (MFA til /admin), og fuld
audit- og auth-log.

Gær-base er skabelonen. Hver kunde får altid en fork i sin egen GitHub-organisation og henter opdateringer
herfra ind i sin fork.

## Indhold

| | |
| --- | --- |
| **Login** | E-mail → engangskode (SMS er opt-in med `SMS_LOGIN=1`; koden gemt som hash, udløber, kan bruges én gang, spærring efter for mange forsøg). Alle tilbydes en passkey; brugere kan selv slå **Kræv passkey** (MFA) til. **/admin kræver MFA** efter tre grace logins: passkey, sikkerhedsnøgle (YubiKey) eller godkendelsesapp (TOTP), og sikkerhedshandlinger kræver en frisk bekræftelse. Første bruger bliver superadmin — eller, med `BOOTSTRAP_ADMIN_EMAIL`, kun den adresse (anbefalet i produktion). |
| **Logning** | Auth-log og audit-log i Postgres. Kun tilføjelse: app-brugeren har ikke UPDATE/DELETE, og en trigger stopper UPDATE. Sletning kun fra /admin med en separat databasebruger, og sletningen logges. |
| **/admin** | Brugere og roller · auth-log · audit-log (CSV) · analytics · heatmaps · GDPR-forespørgsler · sletning af logdata · samtykke og cookies · filer · website · udvidelse og Office · indstillinger. Alt i /admin skrives i audit-loggen. |
| **Analytics og heatmaps** | Opsamles af Python-serveren og gemmes i en partitioneret tabel i containerens egen database. Ingen tredjepart. Kun anonyme sidevisninger uden samtykke. |
| **Filer** | Object Storage. Upload gennem serveren, download via links, der udløber efter få minutter. |
| **/w** | Offentligt website, server-renderet fra Markdown-sider på dansk og engelsk. Anonyme besøgende på `/` sendes hertil. |
| **/m** | Mobil-app (PWA) med bundmenu, kamera-upload og klip. |
| **/extension** | Browserudvidelse (Chrome/Edge sidepanel), der kan læse den aktuelle side og gemme den som klip — med samtykke pr. domæne. |
| **/office-addin** | Office-tilføjelser til Word, Excel, PowerPoint og Outlook. Én router finder værten og viser det rigtige værktøj: indsæt klip, gem markering eller mail som klip, svar med klip. |
| **AI-chat (valgfri)** | Chat i browserudvidelsen (med siden som kontekst), Word og Outlook (med dokumentet/mailen som kontekst; AI'en kan indsætte tekst, sætte emne, åbne svar) og /m. Bruger OVHcloud AI Endpoints, så teksten behandles i Europa. Slås til med `AI_API_KEY`. Samtaler gemmes ikke; kun forbrug, med en månedlig kvote pr. bruger i /admin/ai. |
| **AI-udvikling** | Skills og slash commands til Claude Code og GitHub Copilot: `/gaer-boot`, `/gaer-init`, `/gaer-dev`, `/gaer-db`, `/gaer-mix`, `/gaer-new-feature`, `/gaer-pr`, `/gaer-pr-review`, `/gaer-release`, `/gaer-recover`, `/gaer-whatsup`. |

## Ruter

Én server og ét domæne. Stien afgør, hvilken flade der svarer. Alle deler login, database og API.

<!-- routes:start -->
<!-- Genereret fra web/src/routes.json af npm run app-map --prefix web — ret manifestet, ikke tabellen. -->
| Rute | Hvem | Hvad |
| --- | --- | --- |
| `/` | logget ind | Appens forside. Anonyme sendes til `/w/`. |
| `/w/…` | alle | Offentligt website: Markdown-sider fra `/admin/web` (dansk/engelsk). Forsiden er Markdown-siden eller „Facit · 246“ (valgt i `/admin/web`) og slutter altid med et login-kort; på en ny instans står der, at den første, der logger ind, bliver administrator. |
| `/login` | anonyme | E-mail → engangskode → anden faktor, hvis „Kræv passkey“ er slået til, ellers tilbud om passkey. Viser, når man bliver den første admin. Også `/extension/login` og `/m/login`. |
| `/klip`, `/filer`, `/konto`, `/privatliv` | logget ind (privatliv: alle) | Klip, filer, konto (profil, passkeys, sikkerhedsnøgler, godkendelsesapps, „Kræv passkey“, sessioner), privatliv og GDPR. |
| `/forbind`, `/go` | logget ind | **MCP-samtykke** (`/forbind`: en klient beder om adgang; `mcp:admin` kræver frisk anden faktor) og **deep links** (`/go?path=…&focus=…`, også `/admin/go`), der åbner en side og fokuserer et `data-deeplink`-anker. |
| `/admin/…` | admin (+ MFA) | Administration — en selvstændig app (`web/admin.html`). Kræver anden faktor efter tre logins med engangskode. Alt logges. |
| `/m/…` | logget ind | Mobil-app (PWA) med bundmenu, AI, klip, filer og profil. |
| `/extension/…` | i browserudvidelsen | Sidepanelet: aktuel side, klip og AI med siden som kontekst. |
| `/office-addin?app=word\|excel\|powerpoint\|outlook` | Office (offentlig skal) | Router for Word, Excel, PowerPoint og Outlook → værktøjerne under `/extension/tools/…`. |
| `/extension/tools/word\|outlook\|document` | logget ind, i Office | Office-værktøjer: indsæt/gem klip og AI med dokumentet eller mailen som kontekst. |
| `/api/…` | pr. endpoint | JSON-API. Ændringer kræver headeren `X-Gaer: 1` (CSRF). |
| `/mcp`, `/mcp/admin` | MCP-klienter (OAuth) | MCP-servere: appen som den indloggede bruger og admin med kladder. OAuth: `/.well-known/oauth-*`, `/mcp/authorize`, `/mcp/token`, `/mcp/register`. |
| `/health` | udrulning | Sundhedstjek (database + version), bruges af `deploy/deploy.sh`. |

<details><summary>Alle ruter (66)</summary>

| Sti | Svarer | Adgang | Side |
| --- | --- | --- | --- |
| `/` | appen | logget ind | Forside |
| `/w/{slug}` | serveren | alle | Website |
| `/login` | appen | alle | Log ind · kan indlejres |
| `/extension/login` | appen | alle | Log ind i udvidelsen · eget layout · kan indlejres |
| `/m/login` | appen | alle | Log ind på mobil · eget layout |
| `/klip` | appen | logget ind | Klip (feature `klip`) |
| `/klip/:id` | appen | logget ind | Klip (feature `klip`) |
| `/filer` | appen | logget ind | Filer (feature `filer`) |
| `/konto` | appen | logget ind | Konto |
| `/privatliv` | appen | alle | Privatliv |
| `/forbind` | appen | logget ind | Forbind MCP-klient |
| `/godkend` | appen | logget ind | Godkend adgang |
| `/go` | appen | alle | Deep link |
| `/admin` | admin-appen | admin | Statistik |
| `/admin/go` | admin-appen | admin | Deep link |
| `/admin/users` | admin-appen | admin | Brugere og roller |
| `/admin/impersonation` | admin-appen | admin | Efterligning |
| `/admin/features` | admin-appen | admin | Features og ringe |
| `/admin/app-stores` | admin-appen | admin | App Stores |
| `/admin/app-stores/:tab` | admin-appen | admin | App Stores |
| `/admin/analytics` | admin-appen | admin | Analytics |
| `/admin/heatmaps` | admin-appen | admin | Heatmaps |
| `/admin/logs/auth` | admin-appen | admin | Auth-log |
| `/admin/logs/audit` | admin-appen | admin | Audit-log |
| `/admin/web` | admin-appen | admin | Website |
| `/admin/files` | admin-appen | admin | Filer |
| `/admin/knowledge` | admin-appen | admin | Viden |
| `/admin/knowledge/spaces/:id` | admin-appen | admin | Vidensrum |
| `/admin/knowledge/agents/:id` | admin-appen | admin | Agent |
| `/admin/ai` | admin-appen | admin | AI |
| `/admin/integrations` | admin-appen | admin | Udvidelse og Office |
| `/admin/mcp` | admin-appen | admin | MCP-servere |
| `/admin/webhooks` | admin-appen | admin | Webhooks |
| `/admin/jobs` | admin-appen | admin | Jobs og oprydning |
| `/admin/nats` | admin-appen | admin | NATS |
| `/admin/data` | admin-appen | admin | Katalog |
| `/admin/data/explore` | admin-appen | admin | Udforsk |
| `/admin/data/connect` | admin-appen | admin | Forbind API |
| `/admin/data/tools` | admin-appen | admin | MCP-værktøjer |
| `/admin/data/:id` | admin-appen | admin | Datasæt |
| `/admin/gdpr` | admin-appen | admin | GDPR-forespørgsler |
| `/admin/access` | admin-appen | admin | Adgangsanmodninger |
| `/admin/retention` | admin-appen | admin | Sletning af logdata |
| `/admin/consent` | admin-appen | admin | Samtykke og cookies |
| `/admin/settings` | admin-appen | admin | Indstillinger |
| `/m` | appen | logget ind | Hjem · eget layout |
| `/m/klip` | appen | logget ind | Klip · eget layout |
| `/m/chat` | appen | logget ind | AI · eget layout |
| `/m/filer` | appen | logget ind | Filer · eget layout |
| `/m/profil` | appen | logget ind | Profil · eget layout |
| `/extension` | appen | logget ind | Aktuel side · eget layout · kan indlejres |
| `/extension/chat` | appen | logget ind | AI · eget layout · kan indlejres |
| `/extension/klip` | appen | logget ind | Klip · eget layout · kan indlejres |
| `/extension/konto` | appen | logget ind | Konto · eget layout · kan indlejres |
| `/extension/agent` | appen | logget ind | Agenter (feature `knowledge`) · eget layout · kan indlejres |
| `/extension/agent/:slug` | appen | logget ind | Agent (feature `knowledge`) · eget layout · kan indlejres |
| `/office-addin` | appen | alle | Office-router · eget layout · kan indlejres |
| `/extension/tools` | appen | logget ind | Office-værktøjer · eget layout · kan indlejres |
| `/extension/tools/word` | appen | logget ind | Word · eget layout · kan indlejres |
| `/extension/tools/outlook` | appen | logget ind | Outlook · eget layout · kan indlejres |
| `/extension/tools/document` | appen | logget ind | Dokument · eget layout · kan indlejres |
| `/api/{path}` | serveren | alle | API · kan indlejres |
| `/mcp` | serveren | OAuth | MCP (appen) |
| `/mcp/admin` | serveren | OAuth | MCP (admin) |
| `/.well-known/{name}` | serveren | alle | OAuth-metadata |
| `/health` | serveren | alle | Sundhedstjek |

</details>
<!-- routes:end -->

## GDPR

| Ret | Hvordan |
| --- | --- |
| **Samtykke** | Cookie-banner i appen og på `/w`. Uden samtykke kun anonyme sidevisninger; med samtykke et tilfældigt id til besøg, forløb og heatmaps. Kan ændres på `/privatliv`. Tekster og overblik i `/admin/consent`. |
| **Indsigt** | Brugeren henter selv en zip på `/privatliv` (data.json + egne filer). Admin kan gøre det samme fra en anmodning i `/admin/gdpr`. Eksporten logges. |
| **Rettelse** | Navn på `/konto`. Telefonnummer på `/konto` med kode til det nye nummer, eller af admin i `/admin/users` (“Ret nummer”). |
| **Sletning** | Brugeren sletter selv sin konto på `/privatliv`, eller admin sletter fra en anmodning. Brugeren, filer, klip, passkeys, sessioner, AI-forbrug, samtykker, statistik og log-data om brugeren slettes. Tilbage bliver kun en notits uden persondata (`gdpr.erasure`, `log.purged`). Den sidste admin kan ikke slettes. |
| **Anmodninger** | Indsigt, rettelse og sletning kan sendes fra `/privatliv`, også uden login. `/admin/gdpr` viser frist (30 dage) og status. |
| **Opbevaring** | Logs slettes automatisk efter perioderne i `/admin/retention`. |
| **Backups** | Managed PostgreSQL tager automatiske backups. Slettede data forsvinder fra dem, når backuppen udløber (se OVHcloud-planens opbevaringstid). Gendannes en backup, skal sletninger efter backup-tidspunktet gentages, før databasen tages i brug: `python -m app.reapply_erasures --from-url "$GAMMEL_DATABASE_URL" --since <backup-tidspunkt>` (se `/gaer-recover`). |

Alle tabeller med persondata skal med i `server/app/gdpr.py` — både i eksporten og i sletningen.

## Kom i gang lokalt

Kræver Python 3.12+, Node 22 og Docker.

```bash
cp .env.example .env
python -m venv .venv && . .venv/bin/activate && pip install -r server/requirements-dev.txt
npm ci --prefix web
docker compose up -d                                   # Postgres + MinIO + NATS
(cd server && python -m app.migrate)
(cd server && uvicorn app.main:app --reload --port 8000 --env-file ../.env) &
npm run dev --prefix web                               # http://localhost:5173
```

Med `SMS_PROVIDER=console` står engangskoden i serverens log og på login-siden. Den første bruger bliver admin.
Websitet `/w/` serveres af Python-serveren: http://localhost:8000/w/.

### Test

```bash
TEST_DATABASE_ADMIN_URL=postgresql://gaer:gaer@localhost:5432/gaer pytest server
npm run build --prefix web
```

Testene opretter en midlertidig database med samme brugere som i produktion (`_app`, `_purge`), så
rettighederne på log-tabellerne bliver testet. Passkeys testes med en software-authenticator.

## Arkitektur

```
        Cloudflare Tunnel (TLS, ingen åbne porte)
                     │  cloudflared
   Server · docker compose (deploy/compose.yml)
        ┌────────────▼────────────┐     ┌──────────────────────────────┐
        │  Container  app         │────►│ Container  postgres          │
        │  Python (FastAPI)       │     │  gaer_<kunde>  ← egen db     │
        │  /api /w /mcp /health   │     └──────────────────────────────┘
        │  serverer web/dist      │     ┌──────────────────────────────┐
        └─────────────────────────┘────►│ Object Storage (S3)          │
                                        └──────────────────────────────┘
```

Databasebrugere, oprettet af `deploy/postgres-init.sh`, første gang Postgres-volumet er tomt (eller med
`deploy/provision_database.py` på en managed PostgreSQL-klynge):

| Bruger | Miljøvariabel | Rettigheder |
| --- | --- | --- |
| `gaer_<navn>_owner` | `MIGRATION_DATABASE_URL` | ejer skemaet, kører migrationer |
| `gaer_<navn>_app` | `DATABASE_URL` | appen; kun SELECT/INSERT på log-tabeller |
| `gaer_<navn>_purge` | `PURGE_DATABASE_URL` | sletning af logdata fra /admin |
| `gaer_<navn>_mcp_ro` | `MCP_DB_QUERY_URL` | kun SELECT, for `db_query` i /mcp/admin; aldrig hemmeligheder (valgfri) |

Ingen af dem kan forbinde til andre instansers databaser.

## Udrulning

Push til `main` → `.github/workflows/deploy.yml`:

1. Test (Python mod Postgres, build af frontend)
2. Byg ét image, tagget med commit-sha, og send det direkte til UpCloud-serveren over SSH (`docker save | docker load`)
3. Kopiér `deploy/compose.yml`, `deploy/postgres-init.sh`, `deploy/nats.conf` og `deploy/deploy.sh` til `~/gaer` på UpCloud-serveren
4. `deploy.sh release`: start Postgres, migrér databasen, start den nye version med `docker compose`.
   Svarer `/health` ikke inden for 60 sekunder, startes den forrige version igen.
   `deploy.sh rollback` starter den forrige version.

Stakken (`deploy/compose.yml`) er Postgres (egen database med de tre brugere), appen, NATS og `cloudflared`,
som sender trafik ind via en Cloudflare Tunnel — serveren har ingen åbne web-porte.

**NATS** (`nats:2.15.2-alpine`, konfiguration i `deploy/nats.conf`) kører med JetStream, og streams, key/value- og
object stores ligger på volumet `nats` (`/data`), så de overlever genstart og nye versioner. Klienter i stakken
bruger `NATS_URL` (sat af `compose.yml`, med `NATS_TOKEN` fra `~/gaer/.env`; tomt token = ingen godkendelse, men
NATS kan kun nås inde fra stakken). Overvågningen ses i `/admin/nats` (rollen Drift): server, forbindelser,
JetStream (streams og consumers), abonnementer, cluster/gateways/leaf nodes, konti og rå data — læst fra NATS'
overvågningsport (`NATS_MONITOR_URL`, port 8222) gennem appen. Sæt `NATS_TOKEN` (`openssl rand -hex 24`) inden
næste udrulning på en eksisterende server.

GitHub-environment **production** skal have secrets `HOST` (serverens IP), `SSH_KEY` (privat deploy-nøgle
til brugeren `deploy`) og `SSH_KNOWN_HOSTS` (`ssh-keyscan <host>`), samt variablen `DOMAIN` (smoke test).
På serveren laves `~/gaer/.env` ud fra `deploy/.env.example` og `~/gaer/app.env` ud fra
`deploy/app.env.example`.

`postgres-init.sh` kører kun på et tomt volume. Har en eksisterende server ikke `<db>_mcp_ro` (fra før MCP), så
sæt `DB_MCP_RO_PASSWORD` i `~/gaer/.env` og opret rollen én gang; den næste udrulning giver den rettighederne:

```bash
cd ~/gaer && set -a && . ./.env && set +a
docker compose -f compose.yml exec -T postgres psql -U postgres \
  -c "create role ${DB_NAME}_mcp_ro login password '$DB_MCP_RO_PASSWORD'" \
  -c "grant connect on database $DB_NAME to ${DB_NAME}_mcp_ro"
```

## Browserudvidelse og Office

- **Udvidelse**: hent den i `/admin/integrations` (zip med instansens adresse), indlæs upakket i
  `chrome://extensions`, eller udgiv mappen `extension/` i Chrome Web Store / Edge Add-ons.
- **Office**: hent manifesterne i `/admin/integrations` og upload dem i Microsoft 365 Admin Center
  (Integrerede apps). Alle peger på `/office-addin`.

Begge viser appen i en iframe. Brugeren logger ind dér; sessionen er en partitioneret cookie, så den kun
gælder inde i udvidelsen/Office.

## Konfiguration

Alle indstillinger er miljøvariabler — se `.env.example` (lokalt), `deploy/app.env.example` og `deploy/.env.example`
(produktion: appens indstillinger og stakkens hemmeligheder) og
`server/app/config.py`. Navn, logo, SMS-afsender, GDPR-mail, cookie-tekster og opbevaringsperioder sættes i /admin.
