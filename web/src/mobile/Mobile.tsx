// /m — the mobile app (PWA). Same login and data as the web app; a phone layout with a
// bottom tab bar, installable on the home screen (public/m/manifest.webmanifest, public/m/sw.js).

import { useEffect } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { useAiStatus } from "../ai";
import { FeatureGate } from "../features";
import { ClipsList, useClips, type Clip } from "../clips";
import { FileBrowser } from "../pages/FileBrowser";
import { Alert, fmtBytes, fmtDate, useLoad } from "../ui";

function usePwa() {
  useEffect(() => {
    let link = document.querySelector<HTMLLinkElement>('link[rel="manifest"]');
    if (!link) {
      link = document.createElement("link");
      link.rel = "manifest";
      link.href = "/m/manifest.webmanifest";
      document.head.appendChild(link);
    }
    const theme = document.createElement("meta");
    theme.name = "theme-color";
    theme.content = "#ffffff";
    document.head.appendChild(theme);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/m/sw.js", { scope: "/m/" }).catch(() => undefined);
    return () => theme.remove();
  }, []);
}

export function MobileLayout() {
  usePwa();
  const { config } = useAuth();
  const { status: ai } = useAiStatus();
  // Dark mode (styles.css, .theme-auto) so far only on Mapper, whose components use the theme tokens.
  const themed = useLocation().pathname.startsWith("/m/mapper");
  return (
    <div className={`embed-shell mobile${themed ? " theme-auto" : ""}`}>
      <header className="embed-header">
        {config?.logo_url ? <img src={config.logo_url} alt="" /> : <span className="brand-dot" style={{ width: 22, height: 22 }} />}
        <strong>{config?.app_name ?? "Tørgær"}</strong>
      </header>
      <main className="embed-main"><Outlet /></main>
      <nav className="tabbar">
        <NavLink to="/m" end>Hjem</NavLink>
        {/* A full page load: /m/mapper has its own CSP (frame-src https: for the preview, server/app/main.py). */}
        <FeatureGate feature="mapper"><NavLink to="/m/mapper" reloadDocument>Mapper</NavLink></FeatureGate>
        {ai?.enabled && <NavLink to="/m/chat">AI</NavLink>}
        <NavLink to="/m/klip">Klip</NavLink>
        <NavLink to="/m/filer">Filer</NavLink>
        <NavLink to="/m/profil">Profil</NavLink>
      </nav>
    </div>
  );
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 10 ? "Godmorgen" : h < 17 ? "Goddag" : "Godaften";
}

type FileRow = { id: string; filename: string; size_bytes: number; created_at: string };

export function MobileHome() {
  const { user } = useAuth();
  const clips = useLoad(() => api.get<Clip[]>("/api/clips?limit=3"));
  const files = useLoad(() => api.get<FileRow[]>("/api/files"));
  return (
    <div className="stack">
      <h2>{greeting()}{user?.display_name ? `, ${user.display_name}` : ""}</h2>
      {user && user.passkeys === 0 && (
        <Alert kind={user.mfa.admin_grace_left !== null ? "warn" : "info"}>
          Log ind med fingeraftryk eller ansigt: opret en passkey på telefonen. <Link to="/m/profil">Opret</Link>
        </Alert>
      )}
      <div className="grid" style={{ "--min": "130px", "--gap": "10px" } as React.CSSProperties}>
        <Link to="/m/klip" className="card"><span className="eyebrow">Nyt</span><strong>Skriv et klip</strong></Link>
        <Link to="/m/filer" className="card"><span className="eyebrow">Kamera</span><strong>Tag et billede</strong></Link>
        <Link to="/m/beskeder" className="card"><span className="eyebrow">Fra administrationen</span><strong>Beskeder</strong></Link>
        <Link to="/m/samtaler" className="card"><span className="eyebrow">Med de andre</span><strong>Samtaler</strong></Link>
        <FeatureGate feature="mapper"><Link to="/m/mapper" reloadDocument className="card"><span className="eyebrow">Dine genveje</span><strong>Mapper</strong></Link></FeatureGate>
      </div>
      <div className="card">
        <span className="eyebrow">Seneste klip</span>
        {clips.data?.length === 0 && <p className="muted small">Ingen klip endnu.</p>}
        {clips.data?.map((c) => <div key={c.id} className="small"><strong>{c.title}</strong> <span className="muted tiny">{fmtDate(c.created_at)}</span></div>)}
      </div>
      <div className="card">
        <span className="eyebrow">Seneste filer</span>
        {files.data?.length === 0 && <p className="muted small">Ingen filer endnu.</p>}
        {files.data?.slice(0, 3).map((f) => <div key={f.id} className="small"><strong>{f.filename}</strong> <span className="muted tiny">{fmtBytes(f.size_bytes)}</span></div>)}
      </div>
    </div>
  );
}

export function MobileClips() {
  const clips = useClips();
  return <ClipsList clips={clips} compact source="mobile" actions={(c) => (
    <button className="btn btn-outline btn-sm" onClick={() => navigator.share ? navigator.share({ title: c.title, text: c.body }).catch(() => undefined) : navigator.clipboard.writeText(c.body)}>Del</button>
  )} />;
}

export function MobileFiles() {
  return <FileBrowser compact camera />;
}
