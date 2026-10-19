// Shell of the /admin app: dark side menu, sticky top bar and the impersonation banner.
import { useEffect, type CSSProperties } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ArrowLeft, ExternalLink, VenetianMask, X } from "lucide-react";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { useAdmin } from "./context";
import { NAV, type NavBadge } from "./nav";
import ToergaerMark from "./ToergaerMark";
import { initials, MONO, SANS } from "./theme";
import { useToast } from "./ui";
import TracerOverlay from "./tracer/TracerOverlay";
import TracerPanel from "./tracer/TracerPanel";
import ExtensionButton from "./extension/ExtensionButton";
import ExtensionPopover from "./extension/ExtensionPopover";
import InstallGuide from "./extension/InstallGuide";
import { handshake, useHappyAi } from "./extension/happyAiStore";

export default function AdminShell() {
  const { user, config } = useAuth();
  const { impersonating, refreshImpersonation, missingFeatures, range, setRange, health } = useAdmin();
  const { pathname } = useLocation();
  const say = useToast();
  const instance = config?.app_name ?? window.location.hostname;
  const me = user?.display_name || user?.phone || "";
  const ext = useHappyAi();

  useEffect(() => handshake(true), []);

  const badge = (b?: NavBadge): { text: string; amber: boolean } | null => {
    if (b === "impersonation" && impersonating) return { text: "Aktiv", amber: true };
    if (b === "features" && missingFeatures) return { text: String(missingFeatures), amber: true };
    return null;
  };

  const stop = async () => {
    try {
      await api.post("/api/impersonation/stop");
      await refreshImpersonation();
      say(`Efterligning stoppet. Du er tilbage som ${me}.`);
    } catch (e) {
      say(errorText(e));
    }
  };

  return (
    <div className="tg-admin tg-shell" style={{ minHeight: "100vh", display: "flex", fontSize: 14, fontFamily: SANS, color: "#020817", background: "#f1f5f9" }}>
      <aside className="tg-side" style={{ position: "sticky", top: 0, height: "100vh", width: 232, flex: "none", boxSizing: "border-box", display: "flex",
        flexDirection: "column", background: "#0f172a", color: "#ffffff", padding: "20px 10px 16px", overflowY: "auto" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "0 10px 22px" }}>
          <ToergaerMark size={28} byline={false} dark />
          <span style={{ fontSize: 11, color: "rgba(255,255,255,0.6)", fontFamily: MONO }}>/admin · {instance}</span>
        </div>
        <nav style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {NAV.map((g) => (
            <div key={g.label} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <div style={{ padding: "0 10px 4px", fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "rgba(255,255,255,0.45)" }}>
                {g.label}
              </div>
              {g.items.map((n) => {
                const b = badge(n.badge);
                return (
                  <NavLink key={n.path} to={n.path} end={n.path === "/"} style={{ textDecoration: "none" }}>
                    {({ isActive }) => (
                      <span className={isActive ? undefined : "tg-nav"} style={{ position: "relative", display: "flex", alignItems: "center", gap: 10, width: "100%",
                        boxSizing: "border-box", padding: "8px 10px", borderRadius: 8, font: `${isActive ? 600 : 400} 13px ${SANS}`,
                        background: isActive ? "rgba(255,255,255,0.1)" : "transparent", color: isActive ? "#ffffff" : "rgba(255,255,255,0.72)" }}>
                        <span style={{ position: "absolute", left: 0, top: "50%", width: 2, height: 16, marginTop: -8, borderRadius: 2,
                          background: isActive ? "#fcbf49" : "transparent" }} />
                        <n.icon size={16} strokeWidth={2} style={{ flex: "none" }} />
                        <span style={{ flex: 1, minWidth: 0 }}>{n.label}</span>
                        {b && (
                          <span style={{ minWidth: 18, padding: "0 6px", boxSizing: "border-box", borderRadius: 9999, background: b.amber ? "#f59e0b" : "#dc2626",
                            color: b.amber ? "#0f172a" : "#ffffff", fontSize: 10, fontWeight: 700, textAlign: "center", lineHeight: "18px" }}>{b.text}</span>
                        )}
                      </span>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        <div style={{ marginTop: "auto", padding: "12px 10px 0", borderTop: "1px solid rgba(255,255,255,0.1)", display: "flex", flexDirection: "column",
          gap: 10, fontSize: 12, color: "rgba(255,255,255,0.7)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: 9999, background: health && !health.ok ? "#dc2626" : "#10b981",
              animation: health?.ok ? "tgpulse 2s infinite" : "none" }} />
            {health && !health.ok ? "Server svarer ikke" : `Server online · v${health?.version ?? "…"}`}
          </div>
          <a href="/" style={{ display: "flex", alignItems: "center", gap: 6, color: "rgba(255,255,255,0.7)", textDecoration: "none" }}>
            <ArrowLeft size={12} strokeWidth={2} />Til appen på /
          </a>
        </div>
      </aside>

      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        {impersonating && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", padding: "10px 24px", background: "#fffbeb",
            borderBottom: "1px solid rgba(245,158,11,0.4)", fontSize: 13, color: "#78350f" }}>
            <VenetianMask size={18} color="#f59e0b" strokeWidth={2} style={{ flex: "none" }} />
            <span style={{ flex: 1, minWidth: 240, textWrap: "pretty" }}>
              Appen på <strong style={{ fontFamily: MONO }}>/</strong> kører nu som <strong>{impersonating.display_name}</strong> (
              {impersonating.role === "admin" ? "admin" : "bruger"}). /admin bruger stadig din egen rolle, og alt logges med dig som aktør.
            </span>
            <a href="/" target="_blank" rel="noopener" style={{ display: "inline-flex", alignItems: "center", height: 30, padding: "0 12px", borderRadius: 8,
              border: "1px solid rgba(245,158,11,0.5)", background: "#ffffff", color: "#78350f", fontWeight: 500, textDecoration: "none" }}>
              Åbn / som {impersonating.display_name}
            </a>
            <button type="button" onClick={stop} style={{ display: "inline-flex", alignItems: "center", gap: 6, height: 30, padding: "0 12px", borderRadius: 8,
              border: 0, cursor: "pointer", background: "#78350f", color: "#ffffff", font: `500 13px ${SANS}` }}>
              <X size={14} strokeWidth={2} />Stop efterligning
            </button>
          </div>
        )}

        <header style={{ position: "sticky", top: 0, zIndex: 20, display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", padding: "10px 24px",
          background: "rgba(255,255,255,0.92)", backdropFilter: "blur(8px)", borderBottom: "1px solid #e2e8f0", fontSize: 12, color: "#64748b" }}>
          <span style={{ font: `500 13px ${MONO}`, color: "#64748b" }}>
            {window.location.host}<span style={{ color: "#020817", fontWeight: 600 }}>/admin{pathname === "/" ? "" : pathname}</span>
          </span>
          {pathname === "/" && (
            <div style={{ display: "flex", gap: 2, padding: 2, borderRadius: 8, background: "#f1f5f9" }}>
              {[7, 30, 90].map((r) => <RangeBtn key={r} on={range === r} onClick={() => setRange(r)}>{`${r} dage`}</RangeBtn>)}
            </div>
          )}
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 10px", borderRadius: 9999, border: "1px solid #e2e8f0", background: "#ffffff" }}>
            <span style={{ width: 6, height: 6, borderRadius: 9999, background: health && !health.ok ? "#dc2626" : "#10b981" }} />
            {health ? (health.ok ? `API ${health.ms} ms · Postgres ok` : "API svarer ikke") : "API …"}
          </span>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
            <ExtensionButton />
            <a href="/" target="_blank" rel="noopener" style={{ display: "inline-flex", alignItems: "center", gap: 6, height: 32, padding: "0 12px", borderRadius: 8,
              border: "1px solid #e2e8f0", background: "#ffffff", color: "#020817", font: `500 13px ${SANS}`, textDecoration: "none" }}>
              Åbn appen<ExternalLink size={13} strokeWidth={2} />
            </a>
            <span title={`${me} · admin`} style={{ width: 30, height: 30, borderRadius: 9999, background: "var(--hm-brand-gradient)", color: "#ffffff",
              display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700 }}>
              {initials(user?.display_name || null)}
            </span>
          </div>
        </header>

        <main style={{ flex: 1, padding: "28px 24px 64px", boxSizing: "border-box", width: "100%", maxWidth: 1240, display: "flex", flexDirection: "column", gap: 20 }}>
          <Outlet />
        </main>
      </div>

      <ExtensionPopover />
      <InstallGuide />
      <TracerOverlay />
      <TracerPanel context={[
        ["Side", `/admin${pathname === "/" ? "" : pathname}`],
        ["Bruger", `${me} · #${user?.id} · admin`],
        ["Efterligner", impersonating ? `${impersonating.display_name} · #${impersonating.id}` : "—"],
        ["Udvidelse", ext.status === "missing" ? "ingen handshake" : ext.status === "unknown" ? "…" : `happyai-bridge · v${ext.version ?? "?"}`],
        ["API", `${window.location.origin}/api`],
      ]} />
    </div>
  );
}

function RangeBtn({ on, onClick, children }: { on: boolean; onClick: () => void; children: string }) {
  const style: CSSProperties = { border: 0, cursor: "pointer", padding: "4px 10px", borderRadius: 6, font: `500 12px ${SANS}`,
    background: on ? "#ffffff" : "transparent", color: on ? "#020817" : "#64748b", boxShadow: on ? "0 1px 2px rgba(0,0,0,0.08)" : "none" };
  return <button type="button" onClick={onClick} style={style}>{children}</button>;
}
