// App Stores: what each store asks for, after happy-groups member-brand-portal shared/src/store/listing.ts
// without white-label. Texts, limits, links, ids, privacy answers, images, checklist and guides.

export type StoreId = "apple" | "google";
export type Locale = "da" | "en";
export type Cfg = { name: string; bundleId: string; primary: string; primarySoft: string; iconBg: string; splashBg: string };
export type Accounts = {
  apple?: { team_id?: string; issuer_id?: string; key_id?: string };
  google?: { developer_id?: string; service_account?: string };
  expo?: { owner?: string; project_id?: string };
};
export type StoreData = {
  cfg?: Partial<Cfg>;
  texts?: Partial<Record<Locale, Record<string, string>>>;
  links?: Record<string, string>;
  manual?: Partial<Record<StoreId, Record<string, boolean>>>;
  accounts?: Accounts;
};
export type Defaults = { host: string; cfg: Cfg; links: Record<string, string>; ids: { scheme: string; sku: string; rp_id: string } };
export type AppStoreResponse = { data: StoreData; defaults: Defaults; logo_url: string | null };

export type Field = [key: string, label: string, limit: number, rows: number, where: string];
export const STORE_FIELDS: Record<StoreId, Field[]> = {
  apple: [["appName", "Navn", 30, 1, "App-information"], ["subtitle", "Undertitel", 30, 1, "App-information"], ["promo", "Kampagnetekst", 170, 3, "Version"],
    ["description", "Beskrivelse", 4000, 10, "Version"], ["keywords", "Nøgleord (kommasepareret)", 100, 2, "Version"], ["whatsNew", "Nyheder i denne version", 4000, 2, "Version"]],
  google: [["appName", "App-navn", 30, 1, "Hovedbutiksopslag"], ["shortDesc", "Kort beskrivelse", 80, 2, "Hovedbutiksopslag"],
    ["description", "Fuld beskrivelse", 4000, 10, "Hovedbutiksopslag"], ["releaseNotes", "Udgivelsesnoter", 500, 2, "Udgivelse"]],
};
export const CAPTION_LIMIT = 32;
export const CAPTIONS: [string, string][] = [["cap1", "1 · Oversigt"], ["cap2", "2 · Kamera"], ["cap3", "3 · Filer"], ["cap4", "4 · Log ind"]];

export const tagline = (loc: Locale) => (loc === "da" ? "Klip, filer og noter fra alle dine enheder." : "Clips, files and notes from all your devices.");

export function defaultTexts(loc: Locale, name: string): Record<string, string> {
  const tag = tagline(loc);
  return loc === "da"
    ? { appName: name, subtitle: "Klip og filer på farten", promo: `${tag} Log ind med dit telefonnummer og se dine klip med det samme.`,
        description: `${name} samler dine klip og filer i én app.\n\nGem tekst og links som klip, tag billeder med kameraet og upload dem direkte, og find det hele igen fra computer, browser og Office.\n\nSådan virker det\n• Log ind med dit telefonnummer og en SMS-kode\n• Brug Face ID eller fingeraftryk næste gang med en passkey\n• Dine data ligger hos ${name} i EU og deles ikke med tredjepart\n\nDu kan se, hente og slette dine data under Konto.`,
        keywords: "klip,noter,filer,upload,kamera,passkey", whatsNew: `Første version af ${name}.`, shortDesc: tag, releaseNotes: `Første version af ${name}.`,
        cap1: "Dine klip altid ved hånden", cap2: "Gem direkte fra kameraet", cap3: "Filer der deles sikkert", cap4: "Log ind uden kodeord" }
    : { appName: name, subtitle: "Clips and files on the go", promo: `${tag} Sign in with your phone number and see your clips straight away.`,
        description: `${name} brings your clips and files together in one app.\n\nSave text and links as clips, take photos and upload them directly, and find everything again from your computer, browser and Office.\n\nHow it works\n• Sign in with your phone number and a text-message code\n• Use Face ID or your fingerprint next time with a passkey\n• Your data stays with ${name} in the EU and is not shared with third parties\n\nYou can view, download and delete your data under Account.`,
        keywords: "clips,notes,files,upload,camera,passkey", whatsNew: `First version of ${name}.`, shortDesc: tag, releaseNotes: `First version of ${name}.`,
        cap1: "Your clips always at hand", cap2: "Save straight from the camera", cap3: "Files shared securely", cap4: "Sign in without a password" };
}

export function textsFor(loc: Locale, cfg: Cfg, data: StoreData): Record<string, string> {
  return { ...defaultTexts(loc, cfg.name), ...(data.texts?.[loc] ?? {}) };
}

export function fieldProblems(t: Record<string, string>, store: StoreId): string[] {
  return STORE_FIELDS[store].filter(([k, , lim]) => !String(t[k] || "").trim() || String(t[k]).length > lim).map((f) => f[1]);
}

// [key, label, stores, suggestion, hint]
const LINKS: [string, string, "both" | StoreId, boolean, string?][] = [
  ["support", "Support-URL", "both", true], ["marketing", "Marketing-URL", "apple", true], ["privacy", "Privatlivspolitik", "both", false],
  ["deletion", "Slet konto (krævet af Google Play)", "google", true], ["email", "Kontakt-e-mail", "both", false],
  ["reviewLogin", "Demo-login til review", "both", true, "Telefonnummer til Apples og Googles testere"],
  ["ascAppId", "ASC app-ID", "apple", true, "Fra App Store Connect"], ["playAppId", "Play app-ID", "google", true, "Fra Play Console-URL"],
  ["easProjectId", "EAS-projekt-ID", "both", true, "eas init i mobile/"], ["copyright", "Copyright", "apple", false],
];
export const ID_LINKS = ["ascAppId", "playAppId", "easProjectId"];

export type Link = { key: string; label: string; value: string; stores: "both" | StoreId; hint: string; pending: boolean };

export function storeLinks(data: StoreData, defaults: Defaults): Link[] {
  return LINKS.map(([key, label, stores, sugg, hint]) => {
    const edited = data.links?.[key];
    // The EAS project id can also come from the Expo guide in the Happy AI side panel.
    const def = key === "easProjectId" ? data.accounts?.expo?.project_id ?? "" : defaults.links[key] ?? "";
    const value = edited ?? def;
    return { key, label, value, stores, hint: hint ?? "", pending: edited != null ? !edited : sugg || !def };
  });
}

export const MANUAL: Record<StoreId, [string, string][]> = {
  apple: [["privacy", "App Privacy udfyldt"], ["age", "Aldersvurdering udfyldt"], ["price", "Pris og tilgængelighed (gratis · DK)"], ["testflight", "TestFlight-build testet"]],
  google: [["datasafety", "Data safety udfyldt"], ["rating", "Indholdsvurdering (IARC)"], ["audience", "Målgruppe og indhold"], ["assetlinks", "SHA-256 i assetlinks.json"],
    ["internal", "Intern test-track med testere"]],
};

export type Tab = "overview" | "assets" | "texts" | "links" | "privacy" | "guide";
export const TABS: [Tab, string, string][] = [["overview", "Overblik", ""], ["assets", "Grafik", "grafik"], ["texts", "Tekster", "tekster"],
  ["links", "Links og ID'er", "links"], ["privacy", "Privatliv og vurdering", "privatliv"], ["guide", "Vejledning", "vejledning"]];

export type Check = { label: string; ok: boolean; warn?: boolean; manual?: string; detail: string; tab: Tab };

export function checklist(store: StoreId, cfg: Cfg, data: StoreData, defaults: Defaults): Check[] {
  const links = storeLinks(data, defaults);
  const L = (k: string) => links.find((l) => l.key === k)?.value ?? "";
  const here = links.filter((l) => l.stores === "both" || l.stores === store);
  const pDa = fieldProblems(textsFor("da", cfg, data), store), pEn = fieldProblems(textsFor("en", cfg, data), store);
  const pend = here.filter((l) => l.pending && !ID_LINKS.includes(l.key)).map((l) => l.label);
  const a = data.accounts ?? {};
  const appleOk = !!(a.apple?.team_id && a.apple?.issuer_id && a.apple?.key_id);
  const googleOk = !!(a.google?.developer_id && a.google?.service_account);
  const eas = L("easProjectId");
  return [
    store === "apple"
      ? { label: "Udviklerkonto og API-nøgle", ok: appleOk, tab: "links",
          detail: appleOk ? `Team ID ${a.apple!.team_id} · Key ID ${a.apple!.key_id}` : "Kør Apple-guiden i Happy AI-sidepanelet" }
      : { label: "Udviklerkonto og servicekonto", ok: googleOk, tab: "links",
          detail: googleOk ? `Developer ID ${a.google!.developer_id} · ${a.google!.service_account}` : "Kør Google Play-guiden i Happy AI-sidepanelet" },
    store === "apple"
      ? { label: "App oprettet i App Store Connect", ok: !!L("ascAppId"), detail: L("ascAppId") ? `ASC app-ID ${L("ascAppId")}` : "Udfyld ASC app-ID under Links og ID'er", tab: "links" }
      : { label: "App oprettet i Play Console", ok: !!L("playAppId"), detail: L("playAppId") || "Udfyld Play app-ID fra Play Console-URL'en", tab: "links" },
    { label: "EAS-projekt", ok: !!eas, detail: eas ? (a.expo?.owner ? `@${a.expo.owner} · ${eas}` : eas) : "Kør eas init i mobile/", tab: "links" },
    { label: "Tekster · dansk", ok: !pDa.length, detail: pDa.length ? "Tomt eller for langt: " + pDa.join(", ") : "Alle felter inden for grænsen", tab: "texts" },
    { label: "Tekster · engelsk", ok: !pEn.length, detail: pEn.length ? "Tomt eller for langt: " + pEn.join(", ") : "Alle felter inden for grænsen", tab: "texts" },
    { label: "Ikon fra logo", ok: true, detail: "Fra Indstillinger → logo, på ikonbaggrunden", tab: "assets" },
    { label: "Skærmbilleder", ok: true, tab: "assets",
      detail: store === "apple" ? '4 × iPhone 6,9" · 4 × iPhone 6,5" · 4 × iPad 13" pr. sprog' : 'Featuregrafik · 4 × telefon · 4 × 7" · 4 × 10" pr. sprog' },
    { label: "Links bekræftet", ok: !pend.length, warn: pend.length > 0, detail: pend.length ? "Forslag — bekræft: " + pend.join(", ") : "OK", tab: "links" },
    ...MANUAL[store].map(([k, label]): Check => ({ label, manual: k, ok: !!data.manual?.[store]?.[k], detail: "Manuelt trin — markér når det er gjort", tab: "privacy" })),
  ];
}

export function identifiers(cfg: Cfg, defaults: Defaults, data: StoreData): [string, string][] {
  const h = defaults.host, a = data.accounts ?? {};
  return [
    ["Bundle ID / pakkenavn", cfg.bundleId], ["URL-scheme", `${defaults.ids.scheme}://`], ["Domæne", h], ["Passkey RP ID", defaults.ids.rp_id],
    ["Universelle links", `applinks:${h} · webcredentials:${h}`], ["EAS build-profil", "production"], ["SKU", defaults.ids.sku],
    ["Kategori (Apple)", "Produktivitet / Hjælpeprogrammer"], ["Kategori (Google)", "Produktivitet"],
    ["Apple Team ID", a.apple?.team_id || "—"], ["Apple Issuer ID", a.apple?.issuer_id || "—"], ["Apple Key ID", a.apple?.key_id || "—"],
    ["Google Developer ID", a.google?.developer_id || "—"], ["Servicekonto", a.google?.service_account || "—"],
    ["Expo-organisation", a.expo?.owner || "—"], ["EAS Project ID", a.expo?.project_id || "—"],
  ];
}

export const PRIVACY: Record<StoreId, [string, string][]> = {
  apple: [["Kryptering", "Nej — ITSAppUsesNonExemptEncryption = false (sat i app.config.ts)"], ["Aldersvurdering", "4+ forventet. Ingen beskeder mellem brugere i Gær-base"],
    ["Telefonnummer", "Indsamles · knyttet til brugeren · app-funktionalitet · ikke sporing"], ["Navn", "Valgfrit visningsnavn · knyttet til brugeren · app-funktionalitet"],
    ["Bruger-ID", "Indsamles · knyttet til brugeren · app-funktionalitet"], ["Klip, fotos og filer", "Brugerindhold · knyttet til brugeren · app-funktionalitet"],
    ["Enheds-ID (push-token)", "Indsamles · knyttet til brugeren · app-funktionalitet"],
    ["Brugsdata", "Kun med samtykke · ikke knyttet til brugeren uden samtykke · analyse i egen database"],
    ["Tilladelser", "Kamera og fotos (upload) · Face ID (passkey)"], ["Slet konto", "Startes i appen under Konto (Apple 5.1.1(v))"]],
  google: [["Data safety: indsamlet", "Telefonnummer, navn, bruger-ID, klip, fotos, filer, enheds-ID"], ["Data safety: delt", "Nej — intet sendes til tredjepart"],
    ["Kryptering under overførsel", "Ja"], ["Sletning", "Brugeren kan slette sin konto under Konto og via Slet konto-linket"],
    ["Indholdsvurdering", "IARC-spørgeskema: ingen kommunikation mellem brugere · forventet PEGI 3 / Everyone"], ["Målgruppe", "18+ — undgår Families-politikken"],
    ["Tilladelser", "CAMERA · READ_MEDIA_IMAGES · RECORD_AUDIO blokeret"], ["App-adgang", "Angiv demo-login, da appen kræver telefonnummer"]],
};

export function guides(cfg: Cfg): [string, string, [string, string, string?][]][] {
  return [
    ["apple", "App Store (iPhone + iPad)", [["Forbered appen", "Gennemgå Overblik. Alt rødt skal løses først."],
      ["Opret appen", `App Store Connect → Apps → + → Ny app. Bundle ID = ${cfg.bundleId}. Skriv ASC app-ID ind under Links og ID'er og i mobile/eas.json → submit.production.ios.ascAppId.`],
      ["Byg og upload", "GitHub → Actions → mobile → profil production, platform ios, submit slået til. Eller lokalt:", "cd mobile && eas build -p ios --profile production --auto-submit"],
      ["Grafik", "Hent PNG'er under Grafik og læg dem i App Store Connect, eller brug eas metadata."],
      ["App Privacy og aldersvurdering", "Udfyld efter fanen Privatliv og vurdering. Husk demo-login under App Review-information."],
      ["TestFlight", "Tilføj interne testere og test login, passkey, kamera-upload og universelle links på en rigtig enhed."],
      ["Indsend", "Vælg build → Tilføj til review → Indsend. Første review tager typisk 1–3 dage."]]],
    ["google", "Google Play (telefon + tablet)", [["Opret appen", "Play Console → Opret app. Standardsprog dansk, gratis. Kopiér app-ID fra URL'en ind under Links og ID'er."],
      ["App-signering", "Upload første AAB i hånden. Aktivér Play App Signing og kopiér SHA-256 til Indstillinger → app_store.android_sha256 (bruges i assetlinks.json og passkeys).", "cd mobile && eas build -p android --profile production"],
      ["Grafik", "Ikon 512, featuregrafik 1024 × 500, telefon og tablets fra fanen Grafik."],
      ["Hovedbutiksopslag", "Kopiér teksterne fra fanen Tekster. Engelsk tilføjes under Administrer oversættelser."],
      ["Appindhold", "Privatlivspolitik, App-adgang (demo-login), Annoncer (nej), Indholdsvurdering, Målgruppe, Data safety, Slet konto-URL."],
      ["Intern test", "eas submit lægger build på intern test som kladde.", "cd mobile && eas submit -p android --profile production"],
      ["Produktion", "Når intern test er godkendt: forfrem til Produktion → Danmark → send til gennemgang."]]],
    ["expo", "Expo og credentials", [["EAS-projekt", "Opret projektet og skriv ID'et ind under Links og ID'er.", "cd mobile && eas init"],
      ["Miljø", "Sæt GAER_ORIGIN, GAER_APP_NAME, GAER_BUNDLE_ID og GAER_SCHEME som EAS-miljøvariabler. De kommer fra denne side og Indstillinger."],
      ["Credentials", "Lad EAS håndtere certifikater, keystore og APNs. App Store Connect API-nøgle og Play-servicekonto uploades i eas credentials — aldrig i repoet.", "cd mobile && eas credentials"],
      ["Universelle links og passkeys", "Serveren leverer apple-app-site-association og assetlinks.json ud fra Indstillinger → app_store. Tjek at begge svarer med Content-Type application/json."],
      ["OTA", "runtimeVersion følger version. Hæv version, når native kode eller config ændres, og byg igen."]]],
  ];
}

// ── Images ──────────────────────────────────────────────────────────────────

export type AssetKind = "icon" | "adaptive" | "mono" | "splash" | "feature" | "shot";
export type Asset = { name: string; file: string; w: number; h: number; kind: AssetKind; note?: string; device?: "phone" | "tablet"; capKey?: string; alt?: boolean };
export type AssetGroup = { title: string; req: string; items: Asset[] };

const shots = (prefix: string, w: number, h: number, device: "phone" | "tablet"): Asset[] =>
  ["oversigt", "kamera", "filer", "login"].map((n, i) => ({ name: `${i + 1} · ${n}`, file: `${prefix}-${i + 1}-${n}`, w, h, kind: "shot", device, capKey: `cap${i + 1}`, alt: i % 2 === 1 }));

export function assetGroups(store: StoreId): AssetGroup[] {
  const groups: Record<StoreId, AssetGroup[]> = {
    apple: [{ title: "App-ikon", req: "PNG uden alfa, ingen afrundede hjørner", items: [{ name: "App Store-ikon", file: "ios-icon-1024", w: 1024, h: 1024, kind: "icon" }] },
      { title: 'iPhone 6,9"', req: "Påkrævet · 1–10 stk.", items: shots("ios-69", 1320, 2868, "phone") },
      { title: 'iPhone 6,5"', req: 'Valgfri — skaleres ellers fra 6,9"', items: shots("ios-65", 1284, 2778, "phone") },
      { title: 'iPad 13"', req: "Påkrævet når supportsTablet = true", items: shots("ipad-13", 2064, 2752, "tablet") }],
    google: [{ title: "Ikon og featuregrafik", req: "32-bit PNG · featuregrafik påkrævet",
      items: [{ name: "Play-ikon", file: "play-icon-512", w: 512, h: 512, kind: "icon" }, { name: "Featuregrafik", file: "play-feature-1024x500", w: 1024, h: 500, kind: "feature" }] },
      { title: "Telefon", req: "Påkrævet · 2–8 stk.", items: shots("play-phone", 1080, 1920, "phone") },
      { title: '7" tablet', req: "Til tablet-opslag", items: shots("play-7", 1200, 1920, "tablet") },
      { title: '10" tablet', req: "Til tablet-opslag", items: shots("play-10", 1600, 2560, "tablet") }],
  };
  return [...groups[store], { title: "App-build", req: "mobile/assets/ · læses af app.config.ts", items: [
    { name: "icon.png", file: "icon", w: 1024, h: 1024, kind: "icon", note: "Expo icon" },
    { name: "adaptive-icon.png", file: "adaptive-icon", w: 1024, h: 1024, kind: "adaptive", note: "Forgrund · 66 % sikker zone" },
    { name: "monochrome-icon.png", file: "monochrome-icon", w: 1024, h: 1024, kind: "mono", note: "Android 13 temaikon" },
    { name: "splash.png", file: "splash", w: 1024, h: 1024, kind: "splash", note: "Vises på splash-baggrunden" }] }];
}

export function assetBg(a: Asset, cfg: Cfg): string {
  if (a.kind === "shot") return a.alt ? cfg.primarySoft : cfg.primary;
  return a.kind === "mono" ? "#1f2937" : a.kind === "splash" ? cfg.splashBg : a.kind === "feature" ? cfg.primary : cfg.iconBg;
}

export function onDark(hex: string): boolean {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return (r * 299 + g * 587 + b * 114) / 1000 < 150;
}
