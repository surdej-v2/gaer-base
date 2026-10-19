// The Happy AI popover under the top bar (screen 11): status, current tab, selection with user lookup,
// actions, instance in the side panel and the bridge log.
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppWindow, PanelRightOpen, Settings, UserSearch, VenetianMask, Zap, type LucideIcon } from "lucide-react";
import { api } from "../../api";
import { happyAi, HAPPY_AI_STORE_URL, type HappyAiInstance, type HappyAiPageInfo } from "../../bridge/extension";
import logo from "../assets/happy-mates-logo.svg";
import { useAdmin } from "../context";
import { MONO, phoneLabel, sectionLabel, SANS, TONE } from "../theme";
import { tracer, useTracer } from "../tracer/tracerStore";
import { useToast } from "../ui";
import type { AdminUser, UsersResponse } from "../users";
import { bridge, happyAiUi, REQUIRED_VERSION, useHappyAi } from "./happyAiStore";

type Action = { label: string; hint: string; icon: LucideIcon; run: () => void; disabled?: boolean };
const norm = (t: string) => t.replace(/[\s.-]/g, "").toLowerCase();

export default function ExtensionPopover() {
  const { status, version, popoverOpen, log } = useHappyAi();
  const { impersonating } = useAdmin();
  const { hidden: tracerHidden } = useTracer();
  const navigate = useNavigate();
  const say = useToast();
  const [page, setPage] = useState<HappyAiPageInfo | null>(null);
  const [selection, setSelection] = useState("");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [instances, setInstances] = useState<{ endpoint: string; endpoints: HappyAiInstance[] } | null>(null);
  const present = status === "connected" || status === "outdated";

  const readSelection = useCallback(() => {
    bridge("GET_SELECTION", happyAi.selection, (t) => t || "tom").then(setSelection, () => setSelection(""));
  }, []);

  useEffect(() => {
    if (!popoverOpen || !present) return;
    bridge("GET_PAGE_INFO", happyAi.pageInfo, (p) => p.hostname).then(setPage, () => setPage(null));
    readSelection();
    happyAi.instances().then(setInstances, () => setInstances(null)); // only the side panel answers this
    api.get<UsersResponse>("/api/admin/users?limit=500").then((r) => setUsers(r.items), () => {});
  }, [popoverOpen, present, readSelection]);

  if (!popoverOpen) return null;

  const sel = selection.trim();
  const match = sel ? users.find((u) => !u.impersonation_only && (norm(u.phone) === norm(sel) || norm(u.phone) === norm("+45" + sel)
    || (u.display_name && u.display_name.toLowerCase() === sel.toLowerCase()))) : undefined;
  const pillTone = status === "connected" ? TONE.green : status === "outdated" ? TONE.orange : TONE.gray;
  const pill = status === "connected" ? `Forbundet · v${version}` : status === "outdated" ? `Forældet · v${version ?? "?"}` : "Ikke installeret";
  const canImpPanel = status === "connected" && !!impersonating;

  const openPanel = (done: string) => bridge("OPEN_SIDE_PANEL", happyAi.openSidePanel).then(() => say(done), (e: Error) => say(e.message));
  const actions: Action[] = [
    { label: "Åbn sidepanelet", hint: "Appen på / i Chrome- eller Edge-sidepanelet", icon: PanelRightOpen, run: () => openPanel("Sidepanelet er åbnet") },
    { label: impersonating ? `Vis sidepanelet som ${impersonating.display_name}` : "Vis sidepanelet som testkonto",
      hint: canImpPanel ? "Sidepanelet følger din efterligning" : status === "outdated" ? `Kræver version ${REQUIRED_VERSION}` : "Start en efterligning først",
      icon: VenetianMask, disabled: !canImpPanel, run: () => openPanel("Sidepanelet er åbnet") },
    { label: tracerHidden ? "Vis API-tracer" : "Åbn API-tracer", hint: "Live log over kald til /api", icon: Zap,
      run: () => { tracer.show(); happyAiUi.closePopover(); } },
    { label: "Udvidelsens indstillinger", hint: "Endpoint, sprog og debug", icon: Settings,
      run: () => bridge("OPEN_OPTIONS", happyAi.openOptions).then(() => say("Indstillinger åbnet i en ny fane"), (e: Error) => say(e.message)) },
  ];

  const pickInstance = (i: HappyAiInstance) => bridge("SET_INSTANCE", () => happyAi.setInstance(i.url), (r) => r.endpoint.replace("https://", ""))
    .then(() => { setInstances((s) => s && { ...s, endpoint: i.url }); say(`Sidepanelet peger nu på ${i.label}`); }, (e: Error) => say(e.message));

  return (
    <>
      <div onClick={happyAiUi.closePopover} style={{ position: "fixed", inset: 0, zIndex: 44 }} />
      <div aria-label="Happy AI-udvidelse" style={{ position: "fixed", zIndex: 45, top: 54, right: 24, width: 380, maxWidth: "calc(100vw - 32px)",
        maxHeight: "calc(100vh - 72px)", overflow: "auto", boxSizing: "border-box", background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 16,
        boxShadow: "var(--hm-shadow-2xl)", display: "flex", flexDirection: "column", fontSize: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", borderBottom: "1px solid #e2e8f0" }}>
          <img src={logo} alt="" style={{ width: 24, height: 24 }} />
          <span className="tg-rainbow-text" style={{ fontSize: 13, fontWeight: 800, letterSpacing: "0.04em" }}>HAPPY AI</span>
          <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 9999, background: pillTone[0], color: pillTone[1] }}>{pill}</span>
        </div>

        {!present && (
          <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, fontSize: 13 }}>
            <p style={{ margin: 0, color: "#475569", lineHeight: 1.55, textWrap: "pretty" }}>
              Udvidelsen er ikke fundet i denne browser. Med den kan /admin læse den aktuelle fane, slå brugere op fra en markering og åbne appen i sidepanelet.
            </p>
            <a href={HAPPY_AI_STORE_URL} target="_blank" rel="noopener" style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 6, height: 34,
              padding: "0 14px", borderRadius: 8, background: "#2563eb", color: "#f8fafc", fontWeight: 500, textDecoration: "none" }}>Åbn Chrome Web Store</a>
          </div>
        )}

        {present && (
          <div style={{ display: "flex", flexDirection: "column" }}>
            {status === "outdated" && (
              <div style={{ margin: "12px 16px 0", padding: "10px 12px", borderRadius: 10, background: "rgba(249,115,22,0.08)", border: "1px solid rgba(249,115,22,0.3)",
                fontSize: 12, color: "#9a3412", textWrap: "pretty" }}>
                Version {version ?? "?"} er installeret. Opdatér til {REQUIRED_VERSION} for at bruge efterligning i sidepanelet.
              </div>
            )}
            <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8, borderBottom: "1px solid #f1f5f9" }}>
              <span style={sectionLabel}>Aktuel fane</span>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ width: 28, height: 28, flex: "none", borderRadius: 8, background: "#f1f5f9", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <AppWindow size={14} color="#475569" strokeWidth={2} />
                </span>
                <div style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
                  <span style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{page?.title || "—"}</span>
                  <span style={{ font: `12px ${MONO}`, color: "#64748b" }}>{page ? page.hostname + page.pathname : "—"}</span>
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "10px 12px", borderRadius: 10, background: "#f8fafc", border: "1px solid #e2e8f0" }}>
                <span style={{ fontSize: 12, color: "#64748b" }}>Markeret tekst</span>
                <span style={{ font: `13px ${MONO}` }}>{sel ? `„${sel.length > 80 ? sel.slice(0, 80) + "…" : sel}“` : "—"}</span>
                {match && (
                  <button type="button" onClick={() => { happyAiUi.closePopover(); navigate(`/users?user=${match.id}`); }}
                    style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 6, height: 30, padding: "0 10px", borderRadius: 8,
                      border: "1px solid #e2e8f0", background: "#ffffff", cursor: "pointer", font: `500 12px ${SANS}`, color: "#020817" }}>
                    <UserSearch size={13} strokeWidth={2} />Åbn {match.display_name || phoneLabel(match.phone)}
                  </button>
                )}
                <button type="button" onClick={readSelection} style={{ alignSelf: "flex-start", border: 0, background: "transparent", padding: 0, cursor: "pointer",
                  color: "#2563eb", font: `500 12px ${SANS}` }}>Læs markering igen</button>
              </div>
            </div>
            <div style={{ padding: "10px 8px", display: "flex", flexDirection: "column", gap: 2, borderBottom: "1px solid #f1f5f9" }}>
              {actions.map((a) => (
                <button key={a.hint} type="button" onClick={a.run} disabled={a.disabled} className="tg-hover"
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: 8, border: 0, borderRadius: 8, background: "transparent", cursor: "pointer",
                    textAlign: "left", font: `500 13px ${SANS}`, color: "#020817", ...(a.disabled ? { opacity: 0.45, pointerEvents: "none" } : {}) }}>
                  <a.icon size={16} strokeWidth={2} style={{ flex: "none" }} />
                  <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: 1 }}>
                    <span>{a.label}</span>
                    <span style={{ fontSize: 12, fontWeight: 400, color: "#64748b" }}>{a.hint}</span>
                  </span>
                </button>
              ))}
            </div>
            {instances && instances.endpoints.length > 0 && (
              <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8, borderBottom: "1px solid #f1f5f9" }}>
                <span style={sectionLabel}>Instans i sidepanelet</span>
                {instances.endpoints.map((i) => {
                  const on = i.url === instances.endpoint;
                  return (
                    <button key={i.url} type="button" onClick={() => !on && pickInstance(i)} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px",
                      borderRadius: 8, border: `1px solid ${on ? "#2563eb" : "#e2e8f0"}`, background: on ? "rgba(37,99,235,0.05)" : "#ffffff", cursor: "pointer", textAlign: "left" }}>
                      <span style={{ width: 14, height: 14, flex: "none", borderRadius: 9999, border: `2px solid ${on ? "#2563eb" : "#cbd5e1"}`, boxSizing: "border-box",
                        display: "flex", alignItems: "center", justifyContent: "center" }}>
                        <span style={{ width: 6, height: 6, borderRadius: 9999, background: on ? "#2563eb" : "transparent" }} />
                      </span>
                      <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
                        <span style={{ font: `600 13px ${SANS}`, color: "#020817" }}>{i.label}</span>
                        <span style={{ font: `11px ${MONO}`, color: "#64748b" }}>{i.url}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
            <div style={{ padding: "12px 16px 14px", display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ ...sectionLabel, paddingBottom: 4 }}>Bro · happyai-bridge</span>
              {log.map((l, i) => (
                <div key={i} style={{ display: "flex", gap: 8, font: `11px ${MONO}`, color: "#475569" }}>
                  <span style={{ color: l.ok ? "#16a34a" : "#dc2626" }}>{l.ok ? "✓" : "✗"}</span>
                  <span>{l.verb}</span>
                  <span style={{ marginLeft: "auto", color: "#94a3b8", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 200 }}>{l.detail}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
