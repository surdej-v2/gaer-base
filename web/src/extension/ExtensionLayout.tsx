// /extension — the app as shown in the browser extension's side panel: no top bar,
// a compact header and a bottom tab bar.

import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import { useAiStatus } from "../ai";
import { FeatureGate } from "../features";

export default function ExtensionLayout() {
  const { config } = useAuth();
  const { status: ai } = useAiStatus();
  // Dark mode (styles.css, .theme-auto) so far only on Mapper, whose components use the theme tokens.
  const themed = useLocation().pathname.startsWith("/extension/mapper");
  return (
    <div className={`embed-shell${themed ? " theme-auto" : ""}`}>
      <header className="embed-header">
        {config?.logo_url ? <img src={config.logo_url} alt="" /> : <span className="brand-dot" style={{ width: 20, height: 20 }} />}
        <strong>{config?.app_name ?? "Tørgær"}</strong>
      </header>
      <main className="embed-main">
        <Outlet />
      </main>
      <nav className="tabbar">
        <NavLink to="/extension" end>Side</NavLink>
        {/* A full page load: /extension/mapper has its own CSP (frame-src https: for the preview, server/app/main.py). */}
        <FeatureGate feature="mapper"><NavLink to="/extension/mapper" reloadDocument>Mapper</NavLink></FeatureGate>
        {ai?.enabled && <NavLink to="/extension/chat">AI</NavLink>}
        <NavLink to="/extension/klip">Klip</NavLink>
        <NavLink to="/extension/meta">Tags</NavLink>
        <NavLink to="/extension/konto">Konto</NavLink>
      </nav>
    </div>
  );
}
