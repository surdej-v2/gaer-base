// /developer — Gær Developer's own universe (specs/developer/NAVIGATION.md): one sidebar with global and local
// navigation (NAV-1 … NAV-3), a fixed top and bottom with only the menu scrolling (NAV-9), a resize handle between
// 64 and 320 px (NAV-5), ⌘B / Ctrl+B and double-click to switch narrow ↔ wide (NAV-6), automatic narrow mode under
// 1024 px (NAV-7), width and state saved per user (NAV-8), a dot on items that need action (NAV-10), a command
// palette on ⌘K / Ctrl+K (NAV-11) and the accessibility rules of NAV-12. The pattern of the /admin side menu
// (admin-app/SideMenu.tsx: fixed top and bottom, sticky headings, ⌘K) is reused here; the two apps share no code.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft, ChevronsLeft, ChevronsRight, Menu, Search, X } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../auth";
import { HELP, NAV, activeItem, breadcrumb, score, staticCommands, type Command, type NavItem } from "./nav";
import "./developer.css";

export const SECTION = "/developer";
const MIN = 64;
const MAX = 320;
const TEXT_MIN = 180;
const DEFAULT_WIDTH = 248;

export function useDeveloperAccess(): "loading" | "ok" | "login" | "denied" {
  const { user, scope, loading } = useAuth();
  if (loading) return "loading";
  if (!user || scope !== "full") return "login";
  return user.role === "admin" && user.admin_sections.includes(SECTION) ? "ok" : "denied";
}

export function NoDeveloperAccess() {
  return (
    <div className="card" style={{ maxWidth: 560, margin: "40px auto" }}>
      <h2 style={{ margin: 0, fontSize: 22 }}>Kræver rollen Udvikler</h2>
      <p className="muted" style={{ margin: 0 }}>
        Gær Developer er for interne brugere med rollen <strong>Udvikler</strong> eller <strong>Udvikler-admin</strong>
        (eller admin-rollerne Data eller Fuld adgang). Bed en Udvikler-admin om rollen.
      </p>
      <a href="/" className="btn btn-outline btn-sm" style={{ alignSelf: "flex-start" }}>← Til Gær base</a>
    </div>
  );
}

type DevMe = { userId: number; roles: string[]; devAdmin: boolean; ai: boolean; counts: { approvals: number; reviews: number; unread: number } };

/** Shared state for pages: the caller's roles and counts, and a way to set the last crumb. */
export const devState: { me: DevMe | null; setCrumb: (s: string | null) => void; refresh: () => void } = {
  me: null, setCrumb: () => {}, refresh: () => {},
};

/** Pages call this with a name (an endpoint, a pipeline …) for the breadcrumb's last part. */
export function useCrumb(label: string | null | undefined) {
  useEffect(() => {
    devState.setCrumb(label ?? null);
    return () => devState.setCrumb(null);
  }, [label]);
}

type Saved = { width: number; collapsed: boolean };

function storageKey(userId: number | undefined) {
  return `gaer.developer.sidebar.${userId ?? "anon"}`;
}

function load(userId: number | undefined): Saved {
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey(userId)) ?? "null");
    if (raw && typeof raw.width === "number") return { width: Math.min(MAX, Math.max(TEXT_MIN, raw.width)), collapsed: !!raw.collapsed };
  } catch {
    /* default */
  }
  return { width: DEFAULT_WIDTH, collapsed: false };
}

function useWindowWidth() {
  const [w, setW] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return w;
}

export default function DeveloperShell() {
  const { user } = useAuth();
  const access = useDeveloperAccess();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const windowWidth = useWindowWidth();
  const mobile = windowWidth < 768;
  const [saved, setSaved] = useState<Saved>(() => load(user?.id));
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [crumb, setCrumb] = useState<string | null>(null);
  const [me, setMe] = useState<DevMe | null>(null);

  useEffect(() => setSaved(load(user?.id)), [user?.id]);
  const persist = useCallback((s: Saved) => {
    setSaved(s);
    try {
      localStorage.setItem(storageKey(user?.id), JSON.stringify(s));
    } catch {
      /* private mode */
    }
  }, [user?.id]);

  const refresh = useCallback(() => {
    if (access !== "ok") return;
    api.get<DevMe>("/api/admin/dev/me").then(setMe).catch(() => {});
  }, [access]);
  useEffect(refresh, [refresh, pathname]);
  devState.me = me;
  devState.setCrumb = setCrumb;
  devState.refresh = refresh;

  // NAV-7: under 1024 px the sidebar goes narrow by itself and comes back when there is room — unless the user
  // collapsed it.
  const autoNarrow = windowWidth < 1024;
  const width = dragWidth ?? saved.width;
  const narrow = !mobile && (saved.collapsed || autoNarrow || width < TEXT_MIN);
  const shown = mobile ? DEFAULT_WIDTH : narrow ? MIN : width;

  const toggle = useCallback(() => {
    if (mobile) {
      setMobileOpen((o) => !o);
      return;
    }
    persist({ ...saved, collapsed: !narrow ? true : false, width: saved.width < TEXT_MIN ? DEFAULT_WIDTH : saved.width });
  }, [mobile, narrow, persist, saved]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      const k = e.key.toLowerCase();
      if (k === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      } else if (k === "b") {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  useEffect(() => setMobileOpen(false), [pathname]);

  // NAV-5: the handle. Below 180 px the texts go and the bar jumps to 64 px, so text is never cut off.
  const startDrag = (e: React.PointerEvent) => {
    if (mobile) return;
    e.preventDefault();
    const startX = e.clientX;
    const startW = shown;
    const move = (ev: PointerEvent) => setDragWidth(Math.min(MAX, Math.max(MIN, startW + ev.clientX - startX)));
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      const w = Math.min(MAX, Math.max(MIN, startW + ev.clientX - startX));
      setDragWidth(null);
      if (w < TEXT_MIN) persist({ width: saved.width, collapsed: true });
      else persist({ width: w, collapsed: false });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onHandleKey = (e: ReactKeyboardEvent) => {
    const step = e.shiftKey ? 48 : 16;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const next = Math.min(MAX, Math.max(MIN, shown + (e.key === "ArrowRight" ? step : -step)));
      if (next < TEXT_MIN) persist({ width: saved.width, collapsed: true });
      else persist({ width: next, collapsed: false });
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  const crumbs = breadcrumb(pathname, crumb);
  const approvals = (me?.counts.approvals ?? 0) + (me?.counts.reviews ?? 0);

  return (
    <div className="dev-root" style={{ "--dev-side": `${shown}px` } as CSSProperties}>
      {mobile && mobileOpen && <div className="dev-scrim" onClick={() => setMobileOpen(false)} aria-hidden />}
      <aside className={`dev-side${narrow ? " narrow" : ""}${mobile ? " mobile" : ""}${mobile && mobileOpen ? " open" : ""}`}
             style={{ width: shown }} aria-label="Gær Developer">
        <div className="dev-side-top">
          <div className="dev-brand">
            <span className="dev-mark" aria-hidden>G</span>
            {!narrow && <span className="dev-brand-text">Gær Developer</span>}
            <button type="button" className="dev-icon-btn" onClick={toggle}
                    aria-label={mobile ? "Luk menuen" : narrow ? "Udvid sidebaren (⌘B)" : "Klap sidebaren sammen (⌘B)"}
                    title={mobile ? "Luk menuen" : narrow ? "Udvid (⌘B)" : "Klap sammen (⌘B)"}>
              {mobile ? <X size={16} /> : narrow ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
            </button>
          </div>
          <button type="button" className={`dev-search${narrow ? " icon" : ""}`} onClick={() => setPaletteOpen(true)}
                  aria-label="Søg (⌘K)" title={narrow ? "Søg (⌘K)" : undefined}>
            <Search size={15} aria-hidden />
            {!narrow && <><span>Søg</span><kbd>⌘K</kbd></>}
          </button>
        </div>

        <SideNav pathname={pathname} narrow={narrow} approvals={approvals} />

        <div className="dev-side-bottom">
          <SideLink item={HELP} pathname={pathname} narrow={narrow} />
          <a href="/" className="dev-link" title={narrow ? "Til Gær base" : undefined}>
            <ArrowLeft size={16} aria-hidden />
            {narrow ? <span className="sr-only">Til Gær base</span> : <span>Til Gær base</span>}
          </a>
        </div>
        {!mobile && (
          <div className="dev-handle" role="separator" aria-orientation="vertical" aria-label="Sidebarens bredde"
               aria-valuemin={MIN} aria-valuemax={MAX} aria-valuenow={shown} tabIndex={0}
               onPointerDown={startDrag} onDoubleClick={toggle} onKeyDown={onHandleKey}
               title="Træk for at ændre bredden · dobbeltklik for smal/bred" />
        )}
      </aside>

      <div className="dev-main">
        <header className="dev-top">
          {mobile && (
            <button type="button" className="dev-icon-btn" onClick={() => setMobileOpen(true)} aria-label="Åbn menuen">
              <Menu size={18} />
            </button>
          )}
          <nav aria-label="Brødkrumme" className="dev-crumbs">
            {crumbs.map((c, i) => (
              <span key={i}>
                {i > 0 && <span className="sep" aria-hidden>/</span>}
                {c.path && i < crumbs.length - 1 ? <Link to={c.path}>{c.label}</Link>
                  : <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c.label}</span>}
              </span>
            ))}
          </nav>
          <button type="button" className="dev-top-search" onClick={() => setPaletteOpen(true)} aria-label="Søg (⌘K)">
            <Search size={14} aria-hidden /><kbd>⌘K</kbd>
          </button>
        </header>
        <main className="dev-content">
          {access === "loading" ? null : access === "ok" ? <Outlet /> : <NoDeveloperAccess />}
        </main>
      </div>
      {paletteOpen && <Palette onClose={() => setPaletteOpen(false)} onGo={(p) => { setPaletteOpen(false); if (p === "/") window.location.href = "/"; else navigate(p); }} />}
    </div>
  );
}

function SideNav({ pathname, narrow, approvals }: { pathname: string; narrow: boolean; approvals: number }) {
  const ref = useRef<HTMLElement>(null);
  const [shadow, setShadow] = useState({ top: false, bottom: false });
  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setShadow({ top: el.scrollTop > 2, bottom: el.scrollTop + el.clientHeight < el.scrollHeight - 2 });
  }, []);
  useLayoutEffect(() => {
    measure();
    // NAV-9: the active item is scrolled into view.
    ref.current?.querySelector<HTMLElement>("[aria-current='page']")?.scrollIntoView({ block: "nearest" });
  }, [pathname, narrow, measure]);
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);
  return (
    <nav ref={ref} className={`dev-nav${shadow.top ? " shadow-top" : ""}${shadow.bottom ? " shadow-bottom" : ""}`}
         aria-label="Developer" onScroll={measure}>
      {NAV.map((g) => (
        <div key={g.label} className="dev-group">
          {narrow ? <div className="dev-group-line" aria-hidden /> : <div className="dev-heading">{g.label}</div>}
          {g.items.map((item) => (
            <SideLink key={item.key} item={item} pathname={pathname} narrow={narrow} dot={item.badge === "approvals" && approvals > 0}
                      count={item.badge === "approvals" ? approvals : 0} />
          ))}
        </div>
      ))}
    </nav>
  );
}

function SideLink({ item, pathname, narrow, dot, count = 0 }: { item: NavItem; pathname: string; narrow: boolean; dot?: boolean; count?: number }) {
  const active = activeItem(pathname)?.key === item.key;
  const [flyout, setFlyout] = useState(false);
  const Icon = item.icon;
  const expanded = active && !narrow && item.local.length > 0;
  const label = dot ? `${item.label} (${count} kræver handling)` : item.label;
  return (
    <div className="dev-item" onMouseEnter={() => narrow && setFlyout(true)} onMouseLeave={() => setFlyout(false)}
         onFocus={() => narrow && setFlyout(true)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setFlyout(false); }}>
      <NavLink to={item.path} end={item.path === "/developer"} className={`dev-link${active ? " active" : ""}`}
               aria-current={active && pathname === item.path ? "page" : undefined}
               aria-expanded={item.local.length > 0 && !narrow ? expanded : undefined} aria-label={narrow ? label : undefined}>
        <Icon size={16} aria-hidden />
        {!narrow && <span className="dev-link-text">{item.label}</span>}
        {dot && <span className="dev-dot" aria-hidden title={`${count} kræver handling`} />}
        {dot && !narrow && <span className="sr-only">{count} kræver handling</span>}
      </NavLink>
      {expanded && (
        <div className="dev-local" role="group" aria-label={item.label}>
          {item.local.map((l) => {
            const on = l.end ? pathname === l.path : pathname === l.path || pathname.startsWith(l.path + "/");
            return (
              <Link key={l.path} to={l.path} className={`dev-local-link${on ? " active" : ""}`} aria-current={on ? "page" : undefined}>
                {l.label}
              </Link>
            );
          })}
        </div>
      )}
      {narrow && flyout && (
        <div className="dev-flyout" role="group" aria-label={item.label}>
          <div className="dev-flyout-title">{item.label}</div>
          {item.local.map((l) => (
            <Link key={l.path} to={l.path} className="dev-local-link" onClick={() => setFlyout(false)}>{l.label}</Link>
          ))}
        </div>
      )}
    </div>
  );
}

type Found = Command & { kind?: string };

function Palette({ onClose, onGo }: { onClose: () => void; onGo: (path: string) => void }) {
  const [q, setQ] = useState("");
  const [remote, setRemote] = useState<Found[]>([]);
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const commands = useMemo(staticCommands, []);
  useEffect(() => input.current?.focus(), []);
  useEffect(() => {
    if (!q.trim()) {
      setRemote([]);
      return;
    }
    const t = setTimeout(() => {
      api.get<{ items: { label: string; hint: string; path: string }[] }>(`/api/admin/dev/search?q=${encodeURIComponent(q.trim())}`)
        .then((r) => setRemote(r.items)).catch(() => setRemote([]));
    }, 150);
    return () => clearTimeout(t);
  }, [q]);
  const local = q.trim()
    ? commands.map((c) => [c, score(c, q)] as const).filter(([, s]) => s >= 0).sort((a, b) => b[1] - a[1]).map(([c]) => c)
    : commands.slice(0, 12);
  const items: Found[] = [...local.slice(0, 12), ...remote];
  useEffect(() => setIndex(0), [q]);
  const onKey = (e: ReactKeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setIndex((i) => Math.min(items.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter" && items[index]) {
      e.preventDefault();
      onGo(items[index].path);
    }
  };
  return (
    <div className="dev-palette-backdrop" onMouseDown={onClose}>
      <div className="dev-palette" role="dialog" aria-modal="true" aria-label="Kommandopalet" onMouseDown={(e) => e.stopPropagation()}>
        <div className="dev-palette-input">
          <Search size={16} aria-hidden />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} placeholder="Find studier, endpoints, pipelines, datasæt og handlinger"
                 aria-label="Søg" role="combobox" aria-expanded="true" aria-controls="dev-palette-list"
                 aria-activedescendant={items[index] ? `dev-cmd-${index}` : undefined} />
          <kbd>Esc</kbd>
        </div>
        <ul id="dev-palette-list" role="listbox" className="dev-palette-list">
          {items.map((c, i) => (
            <li key={`${c.path}-${i}`} id={`dev-cmd-${i}`} role="option" aria-selected={i === index}
                className={i === index ? "active" : undefined} onMouseEnter={() => setIndex(i)} onClick={() => onGo(c.path)}>
              <span>{c.label}</span><span className="tiny muted">{c.hint}</span>
            </li>
          ))}
          {items.length === 0 && <li className="muted" role="status">Intet matcher “{q}”.</li>}
        </ul>
      </div>
    </div>
  );
}

/** Page header inside the shell. */
export function DevPage({ title, intro, actions, children }: { title: ReactNode; intro?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="dev-page">
      <div className="spread" style={{ alignItems: "flex-end", gap: 12, flexWrap: "wrap" }}>
        <div className="stack" style={{ "--gap": "4px" } as CSSProperties}>
          <h1 className="dev-h1">{title}</h1>
          {intro && <p className="muted small" style={{ margin: 0 }}>{intro}</p>}
        </div>
        {actions && <div className="row" style={{ gap: 8 }}>{actions}</div>}
      </div>
      {children}
    </div>
  );
}
