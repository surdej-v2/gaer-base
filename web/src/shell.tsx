// The signed-in shell of the app on /: a sidepanel with captions that can be dragged wider or narrower and folds
// to icons, a header with the page title and the assistant, an assistant panel on the right, and on phones a
// toolbar fixed to the bottom of the screen. Design: the "Gær signed-in dashboard" canvas.
//
// Pages with their own chrome (/m, /extension, Office) never get this shell; see OWN_CHROME in App.tsx.

import { createContext, useContext, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { Chat, useAiStatus, type ChatContext } from "./ai";
import { useFeature } from "./features";
import manifest from "./routes.json";

// ── Icons (inline stroke SVG, 24×24) ─────────────────────────────────────────

const ICONS: Record<string, ReactNode> = {
  home: <><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></>,
  clip: <path d="M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />,
  folder: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  users: <><circle cx="9" cy="8" r="4" /><path d="M2 21v-1a6 6 0 0 1 12 0v1" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /><path d="M22 21v-1a6 6 0 0 0-4-5.65" /></>,
  share: <><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-1a7 7 0 0 1 16 0v1" /></>,
  gauge: <><path d="M12 14l4-4" /><path d="M3.3 19a10 10 0 1 1 17.4 0" /></>,
  shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
  lock: <><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></>,
  globe: <><circle cx="12" cy="12" r="10" /><path d="M2 12h20" /><path d="M12 2a15 15 0 0 1 0 20a15 15 0 0 1 0-20z" /></>,
  panel: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M9 3v18" /></>,
  sparkle: <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />,
  chat: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  x: <path d="M18 6L6 18M6 6l12 12" />,
  eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>,
  download: <><path d="M12 3v12" /><path d="M7 10l5 5 5-5" /><path d="M5 21h14" /></>,
  trash: <><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M6 6l1 15h10l1-15" /></>,
  copy: <><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="M20 6L9 17l-5-5" />,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 8v4M12 16h.01" /></>,
  phone: <><rect x="6" y="2" width="12" height="20" rx="2" /><path d="M11 18h2" /></>,
  logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></>,
  code: <><path d="M16 18l6-6-6-6" /><path d="M8 6l-6 6 6 6" /></>,
  upload: <><path d="M12 19V7" /><path d="M7 12l5-5 5 5" /><path d="M5 3h14" /></>,
};

export function Icon({ name, size = 20 }: { name: keyof typeof ICONS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={{ flex: "none" }}>
      {ICONS[name]}
    </svg>
  );
}

// ── The assistant, reachable from every page ─────────────────────────────────

type AiAsk = { prompt?: string; context?: ChatContext; send?: boolean };
type AiRequest = AiAsk & { n: number };
type ShellApi = { aiEnabled: boolean; openAi: (req?: AiAsk) => void };
const ShellContext = createContext<ShellApi>({ aiEnabled: false, openAi: () => {} });

/** Open the assistant panel, optionally with a drafted question and context (e.g. the selected clips). */
export const useShell = () => useContext(ShellContext);

// ── Page titles from the route manifest ──────────────────────────────────────

const APP_ROUTES = manifest.routes
  .filter((r) => r.app === "app")
  .map((r) => ({ ...r, re: new RegExp(`^${r.path.replace(/:[^/]+/g, "[^/]+")}/?$`) }));

export function pageTitle(pathname: string): string {
  return APP_ROUTES.find((r) => r.re.test(pathname))?.title ?? "";
}

// ── Sidepanel width, remembered per browser ──────────────────────────────────

const NAV_KEY = "gaer.nav";
const NAV_MIN = 208, NAV_MAX = 360, NAV_DEFAULT = 256, NAV_ICONS = 72, NAV_FOLD = 150;

function loadNav(): { width: number; collapsed: boolean } {
  try {
    const v = JSON.parse(localStorage.getItem(NAV_KEY) ?? "{}");
    return { width: Math.min(NAV_MAX, Math.max(NAV_MIN, Number(v.width) || NAV_DEFAULT)), collapsed: !!v.collapsed };
  } catch {
    return { width: NAV_DEFAULT, collapsed: false };
  }
}

function initials(text: string): string {
  const parts = text.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

// ── Navigation ──────────────────────────────────────────────────────────────

type NavItem = { to: string; label: string; icon: string; feature?: string; end?: boolean; mobile?: boolean };
const WORK: NavItem[] = [
  { to: "/", label: "Overblik", icon: "home", end: true, mobile: true },
  { to: "/klip", label: "Klip", icon: "clip", feature: "klip", mobile: true },
  { to: "/filer", label: "Filer", icon: "folder", feature: "filer", mobile: true },
  { to: "/user", label: "Mit område", icon: "users", end: true },
  { to: "/user/deling", label: "Deling", icon: "share" },
  { to: "/develop", label: "Udvikling", icon: "code" },
];
const ACCOUNT: NavItem[] = [
  { to: "/konto", label: "Konto og sikkerhed", icon: "user", end: true },
  { to: "/konto/forbrug", label: "Mit AI-forbrug", icon: "gauge" },
  { to: "/privatliv", label: "Privatliv", icon: "shield" },
];

function SideLink({ item, onClick }: { item: NavItem; onClick?: () => void }) {
  const enabled = useFeature(item.feature ?? "");
  if (item.feature && !enabled) return null;
  return (
    <NavLink to={item.to} end={item.end} className="side-link" title={item.label} onClick={onClick}>
      <Icon name={item.icon} />
      <span className="cap">{item.label}</span>
    </NavLink>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      <span className="side-label">Arbejde</span>
      {WORK.map((i) => <SideLink key={i.to} item={i} onClick={onNavigate} />)}
      <span className="side-label">Konto</span>
      {ACCOUNT.map((i) => <SideLink key={i.to} item={i} onClick={onNavigate} />)}
    </>
  );
}

/** Links that leave the app on / (full page loads): /admin and the public site. */
function OutLinks() {
  const { user } = useAuth();
  return (
    <>
      {user?.role === "admin" && (
        <a href="/admin" className="side-link" title="Administration">
          <Icon name="lock" /><span className="cap">Administration</span>
        </a>
      )}
      <a href="/w/" className="side-link" title="Til den offentlige side (/w)">
        <Icon name="globe" /><span className="cap grow">Offentlig side</span><span className="cap mono tiny muted">/w</span>
      </a>
    </>
  );
}

// ── The assistant panel ─────────────────────────────────────────────────────

function AiPanel({ request, onClose }: { request: AiRequest; onClose: () => void }) {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const title = pageTitle(pathname);
  const name = user?.display_name || "dig";
  return (
    <aside className="ai-panel" aria-label="Assistent">
      <div className="ai-head">
        <span className="ai-badge"><Icon name="sparkle" size={16} /></span>
        <h2>Assistent</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Luk assistenten"><Icon name="x" size={18} /></button>
      </div>
      <details className="ai-knows">
        <summary>Det ved assistenten lige nu</summary>
        <dl>
          <dt>Logget ind</dt><dd>{name} · {user?.role === "admin" ? "administrator" : "bruger"}</dd>
          {title && <><dt>Side</dt><dd>{title} <span className="mono tiny muted">{pathname}</span></dd></>}
          {request.context && <><dt>Kontekst</dt><dd>{request.context.label}</dd></>}
          <dt>Kan bruge</dt><dd>Gem klip · læs og gem filer i ~/ og /shared</dd>
        </dl>
        <p className="tiny muted">Kun dit navn og din rolle sendes, aldrig e-mail eller telefon. Samtalen gemmes ikke.</p>
      </details>
      <div className="ai-body">
        <Chat key={request.n} surface="app" initialDraft={request.prompt} autoSend={request.send}
              getContext={request.context ? async () => request.context ?? null : undefined}
              contextHint="Spørg om dine klip og filer, eller hvor du finder noget i appen." />
      </div>
    </aside>
  );
}

// ── The shell ───────────────────────────────────────────────────────────────

export function AppShell({ children }: { children: ReactNode }) {
  const { user, config, logout } = useAuth();
  const { status } = useAiStatus();
  const aiEnabled = !!status?.enabled;
  const { pathname } = useLocation();
  const [nav, setNav] = useState(loadNav);
  const [dragging, setDragging] = useState(false);
  const [ai, setAi] = useState<AiRequest | null>(null);
  const [more, setMore] = useState(false);
  const counter = useRef(0);

  useEffect(() => {
    try { localStorage.setItem(NAV_KEY, JSON.stringify(nav)); } catch { /* private mode: not remembered */ }
  }, [nav]);
  useEffect(() => setMore(false), [pathname]);

  const shell = useMemo<ShellApi>(() => ({
    aiEnabled,
    openAi: (req) => setAi({ ...req, n: ++counter.current }),
  }), [aiEnabled]);

  const setWidth = (w: number) => setNav((n) => (w < NAV_FOLD ? { ...n, collapsed: true } : { width: Math.min(NAV_MAX, Math.max(NAV_MIN, w)), collapsed: false }));
  const toggle = () => setNav((n) => ({ ...n, collapsed: !n.collapsed }));

  const startResize = (e: PointerEvent<HTMLButtonElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = nav.collapsed ? NAV_ICONS : nav.width;
    let moved = false;
    setDragging(true);
    const move = (ev: globalThis.PointerEvent) => {
      if (Math.abs(ev.clientX - startX) > 3) moved = true;
      if (moved) setWidth(startW + ev.clientX - startX);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setDragging(false);
      if (!moved) toggle(); // a click on the edge folds or unfolds
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const resizeKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const w = nav.collapsed ? NAV_ICONS : nav.width;
    if (e.key === "ArrowLeft") { e.preventDefault(); setWidth(w - 24); }
    else if (e.key === "ArrowRight") { e.preventDefault(); setWidth(nav.collapsed ? NAV_MIN : w + 24); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
  };

  const signOut = async () => {
    await logout();
    window.location.href = "/w/";
  };

  if (!user) return <>{children}</>;
  const who = user.display_name || user.email || user.phone || "";
  const width = nav.collapsed ? NAV_ICONS : nav.width;
  const title = pageTitle(pathname);

  return (
    <ShellContext.Provider value={shell}>
      <div className={`shell ${nav.collapsed ? "collapsed" : ""}`}>
        <nav className="side" aria-label="Hovedmenu" style={{ width }}>
          <div className="side-brand">
            <Link to="/" className="brand" title={config?.app_name ?? "Tørgær"}>
              {config?.logo_url ? <img src={config.logo_url} alt="" /> : <span className="brand-dot" />}
              <span className="cap">{config?.app_name ?? "Tørgær"}</span>
            </Link>
            <button className="icon-btn" onClick={toggle} aria-expanded={!nav.collapsed}
                    aria-label={nav.collapsed ? "Fold sidepanelet ud" : "Fold sidepanelet ind"}
                    title={nav.collapsed ? "Fold sidepanelet ud" : "Fold sidepanelet ind"}>
              <Icon name="panel" size={18} />
            </button>
          </div>
          <NavLinks />
          <div className="side-foot">
            <OutLinks />
            <Link to="/konto" className="side-link side-user" title={who}>
              <span className="avatar blue" aria-hidden>{initials(who)}</span>
              <span className="cap side-user-text">
                <strong>{user.display_name || "Din profil"}</strong>
                <span className="tiny muted">{user.email || user.phone}</span>
              </span>
            </Link>
            <button className="side-link" onClick={signOut} title="Log ud">
              <Icon name="logout" /><span className="cap">Log ud</span>
            </button>
          </div>
        </nav>
        <button className={`resizer ${dragging ? "dragging" : ""}`} onPointerDown={startResize} onKeyDown={resizeKey}
                aria-label="Sidepanelets bredde: træk, brug piletasterne, eller tryk Enter for at folde" title="Træk for at ændre bredde · klik for at folde">
          <span className="bar" />
        </button>

        <div className="shell-main">
          <header className="shell-head">
            <Link to="/" className="brand mobile-only" aria-label="Overblik">
              {config?.logo_url ? <img src={config.logo_url} alt="" /> : <span className="brand-dot" />}
            </Link>
            <span className="shell-title">{title}</span>
            <div className="shell-actions">
              <a href="/w/" className="icon-btn mobile-only" aria-label="Til den offentlige side" title="Offentlig side (/w)"><Icon name="globe" /></a>
              {aiEnabled && (
                <button className={`btn btn-outline btn-sm ai-toggle ${ai ? "on" : ""}`} aria-pressed={!!ai}
                        onClick={() => (ai ? setAi(null) : shell.openAi())}>
                  <Icon name="sparkle" size={16} /><span className="hide-sm">Assistent</span>
                </button>
              )}
            </div>
          </header>
          <div className="shell-body">
            <main className="shell-content">{children}</main>
            {ai && aiEnabled && <AiPanel request={ai} onClose={() => setAi(null)} />}
          </div>

          <nav className="mbar" aria-label="Hovedmenu">
            {WORK.filter((i) => i.mobile).map((i) => <TabLink key={i.to} item={i} />)}
            {aiEnabled && (
              <button className={ai ? "active" : ""} onClick={() => (ai ? setAi(null) : shell.openAi())}>
                <Icon name="chat" size={22} />AI
              </button>
            )}
            <button className={more ? "active" : ""} aria-expanded={more} onClick={() => setMore(!more)}>
              <Icon name="menu" size={22} />Mere
            </button>
          </nav>
        </div>
      </div>

      {more && (
        <div className="sheet-backdrop" onClick={() => setMore(false)}>
          <nav className="sheet" aria-label="Mere" onClick={(e) => e.stopPropagation()}>
            <NavLinks onNavigate={() => setMore(false)} />
            <span className="side-label">Andre steder</span>
            <OutLinks />
            <button className="side-link danger" onClick={signOut}><Icon name="logout" /><span>Log ud</span></button>
          </nav>
        </div>
      )}
    </ShellContext.Provider>
  );
}

function TabLink({ item }: { item: NavItem }) {
  const enabled = useFeature(item.feature ?? "");
  if (item.feature && !enabled) return null;
  return (
    <NavLink to={item.to} end={item.end}>
      <Icon name={item.icon} size={22} />{item.label}
    </NavLink>
  );
}
