// The /admin base package, as described on the Gær product page.
export const SECTIONS = [
  { path: "/admin/users", title: "Brugere og roller", text: "Se brugere, tildel og fjern admin-rollen, spær en bruger og nulstil passkeys." },
  { path: "/admin/logs/auth", title: "Auth-log", text: "Søg og filtrer i login-hændelser efter bruger, telefonnummer, enhed, IP og tidsrum." },
  { path: "/admin/logs/audit", title: "Audit-log", text: "Hvem ændrede hvad og hvornår, med værdier før og efter. Kan eksporteres som CSV." },
  { path: "/admin/analytics", title: "Analytics", text: "Besøg, sider, kilder, enheder og forløb. Vi opsamler og præsenterer det selv." },
  { path: "/admin/heatmaps", title: "Heatmaps", text: "Klik- og scroll-heatmaps pr. side, opsamlet af Python-serveren og gemt i containerens egen database." },
  { path: "/admin/gdpr", title: "GDPR-forespørgsler", text: "Modtag og behandl anmodninger om indsigt, rettelse og sletning. Indsigt udleveres som en samlet eksport." },
  { path: "/admin/retention", title: "Sletning af logdata", text: "Opbevaringsperiode pr. log, automatisk sletning af gamle hændelser og manuel sletning for én bruger." },
  { path: "/admin/consent", title: "Samtykke og cookies", text: "Tekster i cookie-banneret og overblik over, hvem der har givet samtykke til hvad." },
  { path: "/admin/files", title: "Filer", text: "Overblik over filer i Object Storage pr. bruger og forbrug i alt." },
  { path: "/admin/web", title: "Website", text: "Det offentlige website under /w: sider i Markdown på dansk og engelsk, med menu og forhåndsvisning." },
  { path: "/admin/integrations", title: "Udvidelse og Office", text: "Browserudvidelsen (/extension) og Office-tilføjelser til Word, Excel, PowerPoint og Outlook." },
  { path: "/admin/ai", title: "AI", text: "Valgfri AI-chat via OVHcloud AI Endpoints: model, månedlig kvote pr. bruger og forbrug." },
  { path: "/admin/settings", title: "Indstillinger", text: "Navn, logo, SMS-afsender, e-mail til GDPR-forespørgsler og miljøets version." },
];
