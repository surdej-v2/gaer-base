// /extension — the app as shown in the browser extension's side panel: no top bar,
// a compact header and a bottom tab bar.

import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { useAiStatus } from "../ai";

export default function ExtensionLayout() {
  const { config } = useAuth();
  const { status: ai } = useAiStatus();
  return (
    <div className="embed-shell">
      <header className="embed-header">
        {config?.logo_url ? <img src={config.logo_url} alt="" /> : <span className="brand-dot" style={{ width: 20, height: 20 }} />}
        <strong>{config?.app_name ?? "Tørgær"}</strong>
      </header>
      <main className="embed-main">
        <Outlet />
      </main>
      <nav className="tabbar">
        <NavLink to="/extension" end>Side</NavLink>
        {ai?.enabled && <NavLink to="/extension/chat">AI</NavLink>}
        <NavLink to="/extension/klip">Klip</NavLink>
        <NavLink to="/extension/konto">Konto</NavLink>
      </nav>
    </div>
  );
}
