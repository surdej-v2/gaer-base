// /admin/app-stores (screens 06–08), after happy-groups StoreStudio.tsx without white-label:
// no brand switcher, no "Nyt brand", no icon suggestions, no brand file. One app per instance.
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, errorText } from "../../../api";
import { happyAi } from "../../../bridge/extension";
import logo from "../../assets/happy-mates-logo.svg";
import { bridge, happyAiUi, useHappyAi } from "../../extension/happyAiStore";
import { card, MONO, SANS } from "../../theme";
import { copyText, ErrorBox, PageTitle, useToast } from "../../ui";
import {
  assetBg, assetGroups, CAPTION_LIMIT, CAPTIONS, checklist, guides, identifiers, onDark, PRIVACY, STORE_FIELDS, storeLinks, TABS, tagline, textsFor,
  type AppStoreResponse, type Cfg, type Locale, type StoreData, type StoreId, type Tab,
} from "./model";
import { renderPng, saveBlob } from "./render";

type Patch = {
  cfg?: Partial<Cfg>;
  texts?: Partial<Record<Locale, Record<string, string | null>>>;
  links?: Record<string, string | null>;
  manual?: Partial<Record<StoreId, Record<string, boolean>>>;
};

/** Same merge as the server (routes/app_store.py): null removes an edited value. */
function applyPatch(d: StoreData, p: Patch): StoreData {
  const out: StoreData = { ...d };
  if (p.cfg) out.cfg = { ...d.cfg, ...p.cfg };
  if (p.texts) {
    out.texts = { ...d.texts };
    for (const [loc, fields] of Object.entries(p.texts) as [Locale, Record<string, string | null>][]) {
      const cur = { ...(out.texts[loc] ?? {}) };
      for (const [k, v] of Object.entries(fields)) if (v === null) delete cur[k]; else cur[k] = v;
      out.texts[loc] = cur;
    }
  }
  if (p.links) {
    const cur = { ...d.links };
    for (const [k, v] of Object.entries(p.links)) if (v === null) delete cur[k]; else cur[k] = v;
    out.links = cur;
  }
  if (p.manual) {
    out.manual = { ...d.manual };
    for (const [s, steps] of Object.entries(p.manual) as [StoreId, Record<string, boolean>][]) out.manual[s] = { ...out.manual[s], ...steps };
  }
  return out;
}

function mergePatch(a: Patch, b: Patch): Patch {
  const texts = { ...a.texts };
  for (const [loc, f] of Object.entries(b.texts ?? {}) as [Locale, Record<string, string | null>][]) texts[loc] = { ...texts[loc], ...f };
  const manual = { ...a.manual };
  for (const [s, f] of Object.entries(b.manual ?? {}) as [StoreId, Record<string, boolean>][]) manual[s] = { ...manual[s], ...f };
  return { cfg: { ...a.cfg, ...b.cfg }, texts, links: { ...a.links, ...b.links }, manual };
}

function useAppStore() {
  const say = useToast();
  const [res, setRes] = useState<AppStoreResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<Patch>({});
  const timer = useRef(0);

  const load = useCallback(() => api.get<AppStoreResponse>("/api/admin/app-store").then(setRes, (e) => setError(errorText(e))), []);
  useEffect(() => void load(), [load]);

  const flush = useCallback(async () => {
    const body = pending.current;
    pending.current = {};
    try {
      await api.put("/api/admin/app-store", body);
    } catch (e) {
      say(errorText(e));
      void load();
    }
  }, [load, say]);

  useEffect(() => () => {
    window.clearTimeout(timer.current);
    if (Object.keys(pending.current).length) void flush();
  }, [flush]);

  const update = useCallback((p: Patch, now = false) => {
    setRes((r) => r && { ...r, data: applyPatch(r.data, p) });
    // Half-typed values the server would reject (422) stay local until they are valid.
    const cfg = Object.fromEntries(Object.entries(p.cfg ?? {}).filter(([k, v]) => CFG_VALID[k as keyof Cfg]?.(String(v)) ?? false));
    if (p.cfg && !Object.keys(cfg).length && !p.texts && !p.links && !p.manual) return;
    pending.current = mergePatch(pending.current, { ...p, cfg });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, now ? 0 : 600);
  }, [flush]);

  return { res, error, update };
}

const CFG_VALID: Record<keyof Cfg, (v: string) => boolean> = {
  name: (v) => v.trim().length > 0 && v.length <= 30,
  bundleId: (v) => /^[a-zA-Z][a-zA-Z0-9]*(\.[a-zA-Z0-9_-]+)+$/.test(v),
  primary: (v) => /^#[0-9a-fA-F]{6}$/.test(v),
  primarySoft: (v) => /^#[0-9a-fA-F]{6}$/.test(v),
  iconBg: (v) => /^#[0-9a-fA-F]{6}$/.test(v),
  splashBg: (v) => /^#[0-9a-fA-F]{6}$/.test(v),
};

const tabBySlug = (slug?: string): Tab => TABS.find((t) => t[2] === (slug ?? ""))?.[0] ?? "overview";
const smallBtn = { height: 28, padding: "0 10px", borderRadius: 6, border: "1px solid #e2e8f0", background: "#ffffff", cursor: "pointer", font: `500 12px ${SANS}`, color: "#020817" } as const;
const ghostBtn = { ...smallBtn, border: 0, background: "transparent" } as const;
const linkBtn = { border: 0, background: "transparent", padding: 0, cursor: "pointer", color: "#2563eb", font: `600 12px ${SANS}` } as const;
const head = { padding: "14px 16px", borderBottom: "1px solid #e2e8f0", display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" } as const;

export default function AppStores() {
  const { tab: slug } = useParams();
  const tab = tabBySlug(slug);
  const navigate = useNavigate();
  const say = useToast();
  const ext = useHappyAi();
  const { res, error, update } = useAppStore();
  const [store, setStore] = useState<StoreId>("apple");
  const [loc, setLoc] = useState<Locale>("da");
  const [guide, setGuide] = useState("apple");
  const goTab = (t: Tab) => navigate(`/app-stores${TABS.find((x) => x[0] === t)![2] ? "/" + TABS.find((x) => x[0] === t)![2] : ""}`);
  const copy = (v: string) => { copyText(v); say("Kopieret"); };

  const openGuide = () => {
    if (ext.status === "connected" || ext.status === "outdated") {
      bridge("OPEN_SIDE_PANEL", happyAi.openSidePanel).then(() => say("Sidepanelet er åbnet · Værktøjer → App Stores"), () => happyAiUi.openGuide());
    } else happyAiUi.openGuide();
  };

  const title = (
    <PageTitle title="App Stores">Grafik, tekster og links til App Store og Google Play for instansens mobil-app. Navn, logo og farver kommer fra Indstillinger.</PageTitle>
  );
  if (error) return <>{title}<ErrorBox>{error}</ErrorBox></>;
  if (!res) return title;

  const { data, defaults } = res;
  const cfg: Cfg = { ...defaults.cfg, ...data.cfg } as Cfg;
  const logoUrl = res.logo_url ?? logo;
  const T = textsFor(loc, cfg, data);
  const checks = checklist(store, cfg, data, defaults);
  const okN = checks.filter((c) => c.ok).length;
  const storeName = store === "apple" ? "App Store" : "Google Play";
  const links = storeLinks(data, defaults).filter((l) => l.stores === "both" || l.stores === store);
  const asc = storeLinks(data, defaults).find((l) => l.key === "ascAppId")?.value;
  const g = guides(cfg).find((x) => x[0] === guide) ?? guides(cfg)[0];

  const segment = (on: boolean) => ({ border: 0, cursor: "pointer", height: 32, padding: "0 14px", borderRadius: 7, font: `600 13px ${SANS}`,
    background: on ? "#ffffff" : "transparent", color: on ? "#020817" : "#64748b", boxShadow: on ? "0 1px 2px rgba(0,0,0,0.08)" : "none" });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {title}
      <a href="#" onClick={(e) => { e.preventDefault(); openGuide(); }} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", borderRadius: 12,
        background: "#ffffff", border: "1px solid rgba(37,99,235,0.2)", textDecoration: "none", color: "#020817" }}>
        <img src={logo} alt="" style={{ width: 24, height: 24 }} />
        <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontWeight: 600 }}>Har I ikke udviklerkonti endnu?</span>
          <span style={{ fontSize: 13, color: "#64748b" }}>Happy AI-sidepanelet guider jer gennem Apple Developer og Google Play Console og tjekker nøglerne til upload.</span>
        </span>
        <span style={{ fontSize: 13, fontWeight: 500, color: "#2563eb", whiteSpace: "nowrap" }}>Åbn guiden →</span>
      </a>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 4, padding: 4, borderRadius: 10, background: "#e2e8f0" }}>
          {(["apple", "google"] as StoreId[]).map((k) => <button key={k} type="button" onClick={() => setStore(k)} style={segment(store === k)}>{k === "apple" ? "App Store" : "Google Play"}</button>)}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <a href={store === "apple" ? (asc ? `https://appstoreconnect.apple.com/apps/${asc}/distribution` : "https://appstoreconnect.apple.com/apps") : "https://play.google.com/console"}
            target="_blank" rel="noopener" style={{ fontSize: 13 }}>{store === "apple" ? "App Store Connect" : "Play Console"} ↗</a>
          <div style={{ display: "flex", gap: 2, padding: 3, borderRadius: 9999, border: "1px solid #e2e8f0", background: "#ffffff" }}>
            {(["da", "en"] as Locale[]).map((k) => (
              <button key={k} type="button" onClick={() => setLoc(k)} style={{ border: 0, cursor: "pointer", height: 26, minWidth: 40, padding: "0 10px", borderRadius: 9999,
                font: `700 12px ${SANS}`, background: loc === k ? "#0f172a" : "transparent", color: loc === k ? "#ffffff" : "#64748b" }}>{k.toUpperCase()}</button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 4, borderBottom: "1px solid #e2e8f0", overflowX: "auto" }}>
        {TABS.map(([k, label]) => (
          <button key={k} type="button" onClick={() => goTab(k)} style={{ marginBottom: -1, border: 0, borderBottom: `2px solid ${tab === k ? "#2563eb" : "transparent"}`,
            background: "transparent", cursor: "pointer", padding: "10px 12px", whiteSpace: "nowrap", font: `${tab === k ? 600 : 500} 14px ${SANS}`, color: tab === k ? "#020817" : "#64748b" }}>
            {label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
          <div style={{ ...card, flex: "1 1 280px", maxWidth: 380, padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <div style={{ width: 64, height: 64, flex: "none", borderRadius: 15, border: "1px solid #e2e8f0", background: cfg.iconBg, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <img src={logoUrl} alt="" style={{ width: 40, height: 40 }} />
              </div>
              <div style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
                <span style={{ fontSize: 18, fontWeight: 700 }}>{cfg.name}</span>
                <span style={{ font: `12px ${MONO}`, color: "#64748b" }}>{cfg.bundleId}</span>
              </div>
            </div>
            <p style={{ margin: 0, color: "#475569", textWrap: "pretty" }}>{tagline(loc)}</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", fontSize: 13 }}>
                <span style={{ fontWeight: 600 }}>{storeName} — klar til indsendelse</span>
                <span style={{ marginLeft: "auto", color: "#64748b", fontVariantNumeric: "tabular-nums" }}>{okN} af {checks.length}</span>
              </div>
              <div style={{ height: 8, borderRadius: 9999, background: "#f1f5f9", overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${Math.round((okN / checks.length) * 100)}%`, background: "#22c55e", borderRadius: 9999 }} />
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 12px", fontSize: 13 }}>
              <span style={{ color: "#64748b" }}>Domæne</span><span style={{ font: `12px ${MONO}` }}>{defaults.host}</span>
              <span style={{ color: "#64748b" }}>App</span><span>Login, så /m i en WebView</span>
              <span style={{ color: "#64748b" }}>Enheder</span><span>Telefon og tablet</span>
            </div>
          </div>
          <div style={{ ...card, flex: "2 1 420px", minWidth: 0, overflow: "hidden" }}>
            <div style={{ padding: "14px 18px", borderBottom: "1px solid #e2e8f0", fontWeight: 700 }}>Tjekliste · {storeName}</div>
            {checks.map((c) => (
              <div key={c.label} style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 18px", borderBottom: "1px solid #f1f5f9" }}>
                <span style={{ width: 10, height: 10, flex: "none", borderRadius: 9999, background: c.ok ? "#22c55e" : c.warn ? "#f59e0b" : c.manual ? "#cbd5e1" : "#ef4444" }} />
                <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
                  <span style={{ fontWeight: 600 }}>{c.label}</span>
                  <span style={{ fontSize: 12, color: "#64748b", textWrap: "pretty" }}>{c.detail}</span>
                </div>
                <button type="button" style={{ ...smallBtn, height: 30, border: `1px solid ${c.manual ? "#e2e8f0" : "transparent"}`, whiteSpace: "nowrap" }}
                  onClick={() => (c.manual ? update({ manual: { [store]: { [c.manual]: !c.ok } } }, true) : goTab(c.tab))}>
                  {c.manual ? (c.ok ? "✓ Udført" : "Markér udført") : "Gå til →"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "assets" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ ...card, padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 700 }}>App-indstillinger</span>
              <span style={{ fontSize: 12, color: "#94a3b8" }}>Ændringer slår igennem i alle billeder og i mobile/app.json.</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 12 }}>
              <CfgInput label="Navn (maks. 30)" value={cfg.name} max={30} onChange={(v) => update({ cfg: { name: v } })} />
              <CfgInput label="Bundle ID" value={cfg.bundleId} mono onChange={(v) => update({ cfg: { bundleId: v } })} />
              {([["primary", "Primær"], ["primarySoft", "Primær lys"], ["iconBg", "Ikonbaggrund"], ["splashBg", "Splash"]] as [keyof Cfg, string][]).map(([key, label]) => (
                <label key={key} style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "#475569" }}>{label}
                  <span style={{ height: 36, display: "flex", alignItems: "center", gap: 8, padding: "0 8px", border: "1px solid #e2e8f0", borderRadius: 8, background: "#ffffff", boxSizing: "border-box" }}>
                    <input type="color" value={cfg[key]} onChange={(e) => update({ cfg: { [key]: e.target.value } })} style={{ width: 24, height: 24, border: 0, padding: 0, background: "transparent" }} />
                    <span style={{ font: `12px ${MONO}` }}>{cfg[key]}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
          {assetGroups(store).map((grp) => {
            const download = async (items: typeof grp.items) => {
              try {
                for (const a of items) saveBlob(await renderPng(a, cfg, logoUrl, a.kind === "feature" ? cfg.name : T[a.capKey ?? ""] ?? ""), `${a.file}${a.kind === "shot" ? "-" + loc : ""}.png`);
              } catch (e) {
                say(errorText(e));
              }
            };
            return (
              <div key={grp.title} style={{ ...card, padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                  <span style={{ fontWeight: 700 }}>{grp.title}</span>
                  <span style={{ fontSize: 12, color: "#94a3b8" }}>{grp.req}</span>
                  <button type="button" onClick={() => { say(`${grp.items.length} filer hentes`); void download(grp.items); }} style={{ ...smallBtn, marginLeft: "auto" }}>Hent alle ({grp.items.length})</button>
                </div>
                <div style={{ display: "flex", gap: 16, overflowX: "auto", paddingBottom: 4 }}>
                  {grp.items.map((a) => {
                    const shot = a.kind === "shot";
                    const ph = shot ? 240 : 104, pw = Math.round((a.w / a.h) * ph);
                    const bg = assetBg(a, cfg), capFg = onDark(bg) ? "#ffffff" : "#0f172a";
                    return (
                      <figure key={a.file} style={{ margin: 0, flex: "none", display: "flex", flexDirection: "column", gap: 8 }}>
                        <div style={{ width: pw, height: ph, borderRadius: a.kind === "adaptive" ? "50%" : a.kind === "icon" || a.kind === "mono" ? "22%" : shot ? 8 : 10,
                          border: "1px solid #e2e8f0", background: bg, overflow: "hidden", display: "flex", flexDirection: "column", alignItems: "center",
                          justifyContent: shot ? "flex-start" : "center", gap: 10, padding: shot ? 12 : 0, boxSizing: "border-box" }}>
                          {shot && (
                            <>
                              <span style={{ font: `700 13px/1.25 ${SANS}`, textAlign: "center", textWrap: "balance", color: capFg }}>{T[a.capKey!]}</span>
                              <div style={{ flex: 1, width: `${a.device === "tablet" ? 84 : 76}%`, borderRadius: "10px 10px 0 0", background: "#ffffff", boxShadow: "0 -2px 8px rgba(2,8,23,0.15)",
                                display: "flex", flexDirection: "column", gap: 5, padding: 8, boxSizing: "border-box" }}>
                                <div style={{ height: 8, width: "55%", borderRadius: 4, background: cfg.primary, opacity: 0.85 }} />
                                <div style={{ height: 18, borderRadius: 5, background: "#f1f5f9" }} />
                                <div style={{ height: 18, borderRadius: 5, background: "#f1f5f9" }} />
                                <div style={{ height: 18, width: "70%", borderRadius: 5, background: "#f1f5f9" }} />
                              </div>
                            </>
                          )}
                          {!shot && a.kind !== "feature" && (
                            <img src={logoUrl} alt="" style={{ width: Math.round(ph * 0.58), height: Math.round(ph * 0.58), filter: a.kind === "mono" ? "grayscale(1) brightness(2.4)" : "none" }} />
                          )}
                          {a.kind === "feature" && <span style={{ font: `700 15px ${SANS}`, color: capFg }}>{cfg.name}</span>}
                        </div>
                        <figcaption style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12, maxWidth: Math.max(pw, 120) }}>
                          <span style={{ fontWeight: 600 }}>{a.name}</span>
                          <span style={{ color: "#64748b", fontVariantNumeric: "tabular-nums" }}>{a.w} × {a.h} px</span>
                          {a.note && <span style={{ color: "#94a3b8" }}>{a.note}</span>}
                          <button type="button" onClick={() => void download([a])} style={{ ...linkBtn, alignSelf: "flex-start", marginTop: 2 }}>Hent PNG</button>
                        </figcaption>
                      </figure>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {tab === "texts" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p style={{ margin: 0, fontSize: 13, color: "#64748b", textWrap: "pretty" }}>
            Teksterne udfyldes fra appens navn. Ret direkte i felterne — ændringer gemmes pr. sprog ({loc === "da" ? "dansk" : "engelsk"}).
          </p>
          {STORE_FIELDS[store].map(([key, label, limit, rows, where]) => {
            const v = String(T[key] ?? "");
            const over = v.length > limit || !v.trim();
            return (
              <div key={key} style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 12, padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                  <span style={{ fontWeight: 700 }}>{label}</span>
                  <span style={{ fontSize: 12, color: "#94a3b8" }}>{where}</span>
                  <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: over ? "#dc2626" : v.length > limit * 0.9 ? "#d97706" : "#64748b" }}>{v.length} / {limit}</span>
                </div>
                <textarea rows={rows} value={v} onChange={(e) => update({ texts: { [loc]: { [key]: e.target.value } } })}
                  style={{ resize: "vertical", padding: "9px 12px", border: `1px solid ${over ? "#fca5a5" : "#e2e8f0"}`, borderRadius: 8, font: `14px/1.5 ${SANS}`, color: "#020817" }} />
                <div style={{ display: "flex", gap: 8 }}>
                  <button type="button" onClick={() => copy(v)} style={smallBtn}>Kopiér</button>
                  {data.texts?.[loc]?.[key] != null && <button type="button" className="tg-hover" onClick={() => update({ texts: { [loc]: { [key]: null } } }, true)} style={ghostBtn}>Nulstil</button>}
                </div>
              </div>
            );
          })}
          <div style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 12, padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 700 }}>Overskrifter på skærmbilleder</span>
              <span style={{ fontSize: 12, color: "#94a3b8" }}>Bruges i begge butikker · anbefalet maks. 32 tegn</span>
            </div>
            {CAPTIONS.map(([key, label]) => {
              const v = String(T[key] ?? "");
              return (
                <label key={key} style={{ display: "grid", gridTemplateColumns: "110px minmax(0,1fr) 54px", gap: 10, alignItems: "center", fontSize: 13 }}>
                  <span style={{ color: "#475569" }}>{label}</span>
                  <input value={v} onChange={(e) => update({ texts: { [loc]: { [key]: e.target.value } } })}
                    style={{ height: 34, padding: "0 10px", border: "1px solid #e2e8f0", borderRadius: 8, font: `14px ${SANS}`, minWidth: 0 }} />
                  <span style={{ textAlign: "right", fontSize: 12, fontVariantNumeric: "tabular-nums", color: v.length > CAPTION_LIMIT ? "#dc2626" : "#64748b" }}>{v.length} / {CAPTION_LIMIT}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}

      {tab === "links" && (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
          <div style={{ ...card, flex: "3 1 420px", minWidth: 0, overflow: "hidden" }}>
            <div style={head}><span style={{ fontWeight: 700 }}>Links og kontakt · {storeName}</span><span style={{ fontSize: 12, color: "#94a3b8" }}>Forslag er markeret. Ret og bekræft.</span></div>
            {links.map((l) => (
              <div key={l.key} style={{ display: "grid", gridTemplateColumns: "minmax(140px,200px) minmax(0,1fr) auto", gap: 12, alignItems: "center", padding: "10px 16px", borderBottom: "1px solid #f1f5f9" }}>
                <span style={{ display: "flex", flexDirection: "column", gap: 3, alignItems: "flex-start" }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{l.label}</span>
                  {l.pending && <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 7px", borderRadius: 9999, background: "#fef3c7", color: "#92400e" }}>FORSLAG</span>}
                </span>
                <input value={l.value} placeholder={l.hint} onChange={(e) => update({ links: { [l.key]: e.target.value } })}
                  style={{ height: 34, padding: "0 10px", border: "1px solid #e2e8f0", borderRadius: 8, font: `13px ${MONO}`, minWidth: 0 }} />
                <button type="button" className="tg-hover" onClick={() => copy(l.value)} style={ghostBtn}>Kopiér</button>
              </div>
            ))}
          </div>
          <div style={{ ...card, flex: "2 1 300px", minWidth: 0, overflow: "hidden" }}>
            <div style={{ padding: "14px 16px", borderBottom: "1px solid #e2e8f0", fontWeight: 700 }}>Identifikatorer</div>
            {identifiers(cfg, defaults, data).map(([k, v]) => (
              <div key={k} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 16px", borderBottom: "1px solid #f1f5f9" }}>
                <span style={{ flex: "0 0 130px", fontSize: 12, color: "#64748b" }}>{k}</span>
                <span style={{ flex: 1, minWidth: 0, font: `12px ${MONO}`, overflowWrap: "anywhere" }}>{v}</span>
                {v !== "—" && <button type="button" onClick={() => copy(v)} style={linkBtn}>Kopiér</button>}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "privacy" && (
        <div style={{ ...card, overflow: "hidden" }}>
          <div style={head}>
            <span style={{ fontWeight: 700 }}>{store === "apple" ? "App Privacy, kryptering og aldersvurdering" : "Appindhold: Data safety, vurdering og målgruppe"}</span>
            <span style={{ fontSize: 12, color: "#94a3b8" }}>Svarene følger af, hvad Gær gemmer. Tilføjer du features, skal de opdateres.</span>
          </div>
          {PRIVACY[store].map(([q, a]) => (
            <div key={q} style={{ display: "grid", gridTemplateColumns: "minmax(140px,240px) minmax(0,1fr)", gap: 12, padding: "11px 16px", borderBottom: "1px solid #f1f5f9", fontSize: 13 }}>
              <span style={{ fontWeight: 600 }}>{q}</span><span style={{ color: "#334155", textWrap: "pretty" }}>{a}</span>
            </div>
          ))}
        </div>
      )}

      {tab === "guide" && (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
          <nav style={{ flex: "1 1 220px", maxWidth: 280, display: "flex", flexDirection: "column", gap: 4 }}>
            {guides(cfg).map(([id, t]) => (
              <button key={id} type="button" onClick={() => setGuide(id)} style={{ textAlign: "left", cursor: "pointer", padding: "10px 12px", borderRadius: 8,
                border: `1px solid ${guide === id ? "#e2e8f0" : "transparent"}`, background: guide === id ? "#ffffff" : "transparent",
                font: `${guide === id ? 600 : 500} 14px ${SANS}`, color: "#020817" }}>{t}</button>
            ))}
          </nav>
          <div style={{ ...card, flex: "3 1 420px", minWidth: 0, padding: "20px 24px", display: "flex", flexDirection: "column", gap: 16 }}>
            <h3 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>{g[1]}</h3>
            <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 16 }}>
              {g[2].map(([t, body, code], i) => (
                <li key={t} style={{ display: "grid", gridTemplateColumns: "28px minmax(0,1fr)", gap: 12 }}>
                  <span style={{ width: 28, height: 28, borderRadius: 9999, background: "#eff6ff", color: "#1d4ed8", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, fontWeight: 700 }}>{i + 1}</span>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 4 }}>
                    <span style={{ fontWeight: 600 }}>{t}</span>
                    <span style={{ color: "#475569", lineHeight: 1.55, textWrap: "pretty" }}>{body}</span>
                    {code && <pre style={{ margin: "4px 0 0", padding: "10px 12px", borderRadius: 8, background: "#0f172a", color: "#e2e8f0", font: `12px/1.55 ${MONO}`, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{code}</pre>}
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}

function CfgInput({ label, value, max, mono, onChange }: { label: string; value: string; max?: number; mono?: boolean; onChange: (v: string) => void }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "#475569" }}>{label}
      <input value={value} maxLength={max} onChange={(e) => onChange(e.target.value)}
        style={{ height: 36, padding: "0 10px", border: "1px solid #e2e8f0", borderRadius: 8, font: mono ? `13px ${MONO}` : `14px ${SANS}` }} />
    </label>
  );
}
