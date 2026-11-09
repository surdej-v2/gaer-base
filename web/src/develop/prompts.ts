// The prompt catalogue on /develop: ready-made prompts for Claude that support the development process.
// Add a prompt by adding an entry here. Fill-in fields are written in [SQUARE BRACKETS]; the page highlights them.

export type Prompt = {
  id: string;
  category: string;
  /** Where the prompt is meant to be used. */
  tool: "Claude.ai" | "Claude Code";
  title: string;
  summary: string;
  prompt: string;
};

export const PROMPTS: Prompt[] = [
  {
    id: "artifact-prototype",
    category: "Artifacts",
    tool: "Claude.ai",
    title: "Klikbar prototype af en skærm",
    summary: "Et Design-artifact af en ny eller ændret skærm, før der kodes.",
    prompt: `Lav et Design-artifact med en klikbar prototype af skærmen [NAVN PÅ SKÆRM] i Gær.

Formål: [HVAD BRUGEREN SKAL KUNNE]
Hvem: [BRUGER / ADMIN]

Krav:
- Happy Mates-designsystemet: Inter, primær #2563eb, tekst #020817, dæmpet #64748b, kanter #e2e8f0.
- Al tekst på dansk. Ingen opdigtede tal: brug [PLADSHOLDERE].
- Vis tilstandene tom, indlæser, fejl og fyldt.
- Skal virke på telefonbredde (390 px).

Afslut med en liste over de API-kald, skærmen kræver.`,
  },
  {
    id: "artifact-dashboard",
    category: "Artifacts",
    tool: "Claude.ai",
    title: "Dashboard over nøgletal",
    summary: "Et dashboard-artifact over tal fra /admin eller en eksport.",
    prompt: `Lav et dashboard-artifact over [EMNE, fx logins pr. uge].
Data: [INDSÆT CSV/JSON ELLER BESKRIV FELTERNE]

- 3–4 nøgletal øverst med ændring i forhold til forrige periode.
- Én hovedgraf over tid og en tabel med detaljer nedenunder.
- Dansk tekst og talformat (1.234,5).
- Mangler data, så vis en tom tilstand i stedet for at opfinde tal.`,
  },
  {
    id: "artifact-explainer",
    category: "Artifacts",
    tool: "Claude.ai",
    title: "Forklaring med diagram",
    summary: "En side der forklarer et flow i Gær, til onboarding eller kunder.",
    prompt: `Lav en artifact-side, der forklarer [FLOW, fx login med engangskode, passkey og MFA til /admin] i Gær.

- Start med ét afsnit om, hvorfor flowet findes.
- Tegn flowet som et diagram med trin og pile.
- Gå derefter trin for trin: hvad brugeren ser, og hvad serveren gør.
- Målgruppe: [UDVIKLER / KUNDE / SUPPORT]. Dansk, kort og præcist.`,
  },
  {
    id: "feature-plan",
    category: "Ny feature",
    tool: "Claude Code",
    title: "Planlæg en ny feature",
    summary: "En plan med migrationer, endpoints, audit og test, før koden skrives.",
    prompt: `/gaer-new-feature [KORT BESKRIVELSE AF FEATUREN]

Lav en plan, før du koder, og vis mig den:
- Migrationer, og hvilke tabeller der har persondata (eksport og sletning i gdpr.py).
- Endpoints, og om de kræver current_user, require_admin eller require_admin_stepup.
- Audit-hændelser i samme transaktion.
- Nye ruter i web/src/routes.json og evt. en /admin-sektion i admin_roles.SECTIONS.
- Tests i server/tests.
Stop og spørg, hvis noget kræver, at login-reglerne ændres.`,
  },
  {
    id: "feature-page",
    category: "Ny feature",
    tool: "Claude Code",
    title: "Ny side i appen",
    summary: "En side med route, manifest og dansk UI.",
    prompt: `Tilføj en ny side [STI, fx /rapporter] i Gær-appen: [HVAD SIDEN VISER].

- Beskriv ruten i web/src/routes.json (adgang, dansk titel, gruppe).
- Tilføj <Route> i web/src/App.tsx, og kør npm run app-map --prefix web.
- Genbrug klasserne i web/src/styles.css (page, card, grid, pill, btn).
- Link til siden fra [HVOR].
- Kør npm run build --prefix web til sidst.`,
  },
  {
    id: "backend-endpoint",
    category: "Backend",
    tool: "Claude Code",
    title: "Nyt API-endpoint",
    summary: "Endpoint med adgangskontrol, validering, audit og test.",
    prompt: `Tilføj endpointet [METODE] /api/[STI] i server/app/routes/[FIL].py, som [HVAD DET GØR].

- Følg skill gaer-python-api: Pydantic-model til input, rå SQL med psycopg, fejl med dansk besked.
- Adgang: [current_user / require_admin / require_admin_stepup].
- Alle dataændringer auditeres i samme transaktion (skill gaer-audit).
- Skriv pytest-tests mod rigtig Postgres, også for adgang nægtet.
- Kør pytest server til sidst.`,
  },
  {
    id: "db-migration",
    category: "Database",
    tool: "Claude Code",
    title: "Ny tabel eller migration",
    summary: "Migration som ny fil, med GDPR og oprydning tænkt ind.",
    prompt: `/gaer-db Lav en migration, der [OPRETTER / ÆNDRER] tabellen [TABEL] med felterne [FELTER].

- Ny fil i server/app/migrations. Ret aldrig en migration, der allerede er kørt.
- Har tabellen persondata, så tilføj den til eksport og sletning i server/app/gdpr.py.
- Vokser data over tid, så tilføj en række i retention_policies og SQL i retention.CLEANUP_SQL.
- Kør python -m app.migrate og pytest server.`,
  },
  {
    id: "review-pr",
    category: "Review",
    tool: "Claude Code",
    title: "Review af en pull request",
    summary: "Gennemgang af sikkerhed, audit, migrationer og GDPR.",
    prompt: `/gaer-pr-review [PR-NUMMER ELLER BRANCH]

Fokusér på:
- Kan login, anden faktor eller step-up omgås?
- Bliver alle dataændringer auditeret i samme transaktion?
- Er migrationer nye filer, og er persondata med i GDPR-eksport og sletning?
- Kræver tilstandsændrende kald X-Gaer-headeren (CSRF)?
Giv fund sorteret efter alvor, med fil og linje.`,
  },
  {
    id: "tests",
    category: "Test",
    tool: "Claude Code",
    title: "Skriv manglende tests",
    summary: "Tests for et modul mod rigtig Postgres.",
    prompt: `Skriv pytest-tests for [MODUL ELLER ENDPOINT] i server/tests.

- Kør mod rigtig Postgres (TEST_DATABASE_ADMIN_URL), ingen mocks af databasen.
- Dæk den glade sti, ugyldigt input, adgang nægtet, og at audit-loggen får en række.
- Ændr ikke produktionskoden, medmindre en test afslører en fejl. Fortæl mig det så først.`,
  },
  {
    id: "debug",
    category: "Fejlfinding",
    tool: "Claude Code",
    title: "Find og ret en fejl",
    summary: "Reproducér først, ret derefter kun det nødvendige.",
    prompt: `Der er en fejl: [HVAD SKER DER, OG HVAD FORVENTEDE DU]
Log / fejlbesked:
[INDSÆT]

- Reproducér fejlen med en test, før du retter noget.
- Find årsagen, og forklar den i to-tre sætninger.
- Ret kun det, der skal til, og vis, at testen nu består.
Er noget gået helt i stykker, så brug /gaer-recover.`,
  },
  {
    id: "spec",
    category: "Dokumentation",
    tool: "Claude.ai",
    title: "Skriv en spec",
    summary: "Et kort spec-dokument til specs/ før en større ændring.",
    prompt: `Skriv en spec for [FEATURE] til mappen specs/[navn]/ i Gær.

Afsnit: Formål · Brugere og adgang · Skærme og ruter · Data og migrationer · Audit og GDPR · Webhooks og jobs · Åbne spørgsmål.
Skriv på dansk og kort. Markér antagelser som [ANTAGELSE].`,
  },
  {
    id: "status",
    category: "Overblik",
    tool: "Claude Code",
    title: "Hvad sker der i projektet?",
    summary: "Status på seneste ændringer, åbne opgaver og fejl.",
    prompt: `/gaer-whatsup

Giv mig derefter de tre vigtigste ting at tage fat på nu, med en begrundelse for hver.`,
  },
];
