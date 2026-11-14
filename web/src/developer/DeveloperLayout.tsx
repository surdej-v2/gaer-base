// The classic Studio pages (/developer/studio, …/api, …/mcp) share a row of tabs. The shell around every /developer
// page is DeveloperShell.tsx.
import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

/** The Studio pages share a second row of tabs: overview, API Studio, MCP Studio. */
export function StudioLayout() {
  return (
    <>
      <div className="segmented" role="tablist" aria-label="Studio" style={{ maxWidth: 480 }}>
        <StudioTab to="/developer/studio" end>Overblik</StudioTab>
        <StudioTab to="/developer/studio/api">API Studio</StudioTab>
        <StudioTab to="/developer/studio/mcp">MCP Studio</StudioTab>
      </div>
      <Outlet />
    </>
  );
}

function StudioTab({ to, end, children }: { to: string; end?: boolean; children: ReactNode }) {
  const { pathname } = useLocation();
  const active = end ? pathname === to : pathname.startsWith(to);
  return (
    <NavLink to={to} end={end} role="tab" aria-selected={active}
             className={active ? "active" : undefined}
             style={{ textAlign: "center", padding: 6, borderRadius: 8, fontSize: 13, fontWeight: 600, textDecoration: "none",
                      color: active ? "var(--hm-foreground)" : "var(--hm-muted-foreground)",
                      background: active ? "#fff" : "transparent", boxShadow: active ? "0 1px 2px rgb(0 0 0 / .08)" : undefined }}>
      {children}
    </NavLink>
  );
}
