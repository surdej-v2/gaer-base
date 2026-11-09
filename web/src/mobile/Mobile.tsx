// /m — the mobile app (PWA). Same login and data as the web app; a phone layout with a
// bottom tab bar, installable on the home screen (public/m/manifest.webmanifest, public/m/sw.js).

import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { api, errorText } from "../api";
import { useAuth } from "../auth";
import { useAiStatus } from "../ai";
import { ClipsList, useClips, type Clip } from "../clips";
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
  return (
    <div className="embed-shell mobile">
      <header className="embed-header">
        {config?.logo_url ? <img src={config.logo_url} alt="" /> : <span className="brand-dot" style={{ width: 22, height: 22 }} />}
        <strong>{config?.app_name ?? "Tørgær"}</strong>
      </header>
      <main className="embed-main"><Outlet /></main>
      <nav className="tabbar">
        <NavLink to="/m" end>Hjem</NavLink>
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
  const files = useLoad(() => api.get<FileRow[]>("/api/files"));
  const camera = useRef<HTMLInputElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true);
    setError(null);
    try {
      for (const f of Array.from(list)) {
        const form = new FormData();
        form.append("file", f);
        await api.post("/api/files", form);
      }
      await files.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <input ref={camera} type="file" accept="image/*" capture="environment" hidden onChange={(e) => upload(e.target.files)} />
      <input ref={picker} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
      <div className="row">
        <button className="btn btn-primary" disabled={busy} onClick={() => camera.current?.click()}>Tag billede</button>
        <button className="btn btn-outline" disabled={busy} onClick={() => picker.current?.click()}>Vælg fil</button>
      </div>
      <Alert>{error}</Alert>
      {files.data?.map((f) => (
        <div key={f.id} className="card" style={{ padding: "12px 14px" }}>
          <div className="spread">
            <div style={{ minWidth: 0 }}><strong className="small" style={{ wordBreak: "break-all" }}>{f.filename}</strong><div className="muted tiny">{fmtBytes(f.size_bytes)} · {fmtDate(f.created_at)}</div></div>
            <button className="btn btn-outline btn-sm" onClick={async () => { window.location.href = (await api.get<{ url: string }>(`/api/files/${f.id}/link`)).url; }}>Hent</button>
          </div>
        </div>
      ))}
    </div>
  );
}
