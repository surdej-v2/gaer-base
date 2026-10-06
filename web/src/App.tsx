import { useEffect, type ReactNode } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import ConsentBanner from "./ConsentBanner";
import { trackPageview } from "./tracker";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Account from "./pages/Account";
import Files from "./pages/Files";
import Privacy from "./pages/Privacy";
import AdminLayout from "./admin/AdminLayout";
import AdminHome from "./admin/AdminHome";
import Users from "./admin/Users";
import AuthLog from "./admin/AuthLog";
import AuditLog from "./admin/AuditLog";
import Analytics from "./admin/Analytics";
import Heatmaps from "./admin/Heatmaps";
import Gdpr from "./admin/Gdpr";
import Retention from "./admin/Retention";
import Consent from "./admin/Consent";
import AdminFiles from "./admin/AdminFiles";
import Settings from "./admin/Settings";
import Web from "./admin/Web";
import Integrations from "./admin/Integrations";
import ExtensionLayout from "./extension/ExtensionLayout";
import ExtensionPage from "./extension/ExtensionPage";
import ExtensionAccount from "./extension/ExtensionAccount";
import ExtensionChat from "./extension/ExtensionChat";
import AiAdmin from "./admin/AiAdmin";
import { Chat } from "./ai";
import ClipsPage, { ClipsList, useClips } from "./clips";
import OfficeShell from "./office/OfficeShell";
import { DocumentTool, OutlookTool, ToolLayout, WordTool } from "./office/Tools";
import { MobileClips, MobileFiles, MobileHome, MobileLayout } from "./mobile/Mobile";

function TopBar() {
  const { user, scope, config, logout } = useAuth();
  const full = user && scope === "full";
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link to="/" className="brand">
          {config?.logo_url ? <img src={config.logo_url} alt="" /> : <span className="brand-dot" />}
          {config?.app_name ?? "Gær"}
        </Link>
        <nav className="nav">
          {full && <NavLink to="/klip">Klip</NavLink>}
          {full && <NavLink to="/filer">Filer</NavLink>}
          {full && <NavLink to="/konto">Konto</NavLink>}
          {full && user.role === "admin" && <NavLink to="/admin">Admin</NavLink>}
          {user ? (
            <button
              className="btn btn-ghost btn-sm"
              onClick={async () => {
                await logout();
                window.location.href = "/";
              }}
            >
              Log ud
            </button>
          ) : (
            <Link to="/login" className="btn btn-dark btn-sm" style={{ color: "#fff", textDecoration: "none" }}>
              Log ind
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}

function RequireUser({ children, admin = false, login = "/login" }: { children: ReactNode; admin?: boolean; login?: string }) {
  const { user, scope, loading } = useAuth();
  const location = useLocation();
  if (loading) return null;
  if (!user || scope !== "full") return <Navigate to={login} replace state={{ from: location.pathname + location.search }} />;
  if (admin && user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

function PageTracker() {
  const location = useLocation();
  useEffect(() => {
    trackPageview(location.pathname);
  }, [location.pathname]);
  return null;
}

// Embedded surfaces (browser extension, Office) and the mobile app have their own chrome.
const OWN_CHROME = ["/extension", "/office-addin", "/m"];

function ExtensionClips() {
  const clips = useClips();
  return <ClipsList clips={clips} compact source="extension" actions={(c) => (
    <button className="btn btn-outline btn-sm" onClick={() => navigator.clipboard.writeText(c.body)}>Kopiér</button>
  )} />;
}

export default function App() {
  const { pathname } = useLocation();
  const ownChrome = OWN_CHROME.some((p) => pathname === p || pathname.startsWith(p + "/"));
  return (
    <>
      {!ownChrome && <TopBar />}
      <PageTracker />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="/privatliv" element={<Privacy />} />
        <Route path="/konto" element={<RequireUser><Account /></RequireUser>} />
        <Route path="/filer" element={<RequireUser><Files /></RequireUser>} />
        <Route path="/klip" element={<RequireUser><ClipsPage /></RequireUser>} />

        <Route path="/extension/login" element={<Login home="/extension" embedded />} />
        <Route path="/extension/tools" element={<RequireUser login="/extension/login"><ToolLayout /></RequireUser>}>
          <Route path="word" element={<WordTool />} />
          <Route path="outlook" element={<OutlookTool />} />
          <Route path="document" element={<DocumentTool />} />
        </Route>
        <Route path="/extension" element={<RequireUser login="/extension/login"><ExtensionLayout /></RequireUser>}>
          <Route index element={<ExtensionPage />} />
          <Route path="chat" element={<ExtensionChat />} />
          <Route path="klip" element={<ExtensionClips />} />
          <Route path="konto" element={<ExtensionAccount />} />
        </Route>
        <Route path="/office-addin" element={<OfficeShell />} />

        <Route path="/m/login" element={<Login home="/m" embedded />} />
        <Route path="/m" element={<RequireUser login="/m/login"><MobileLayout /></RequireUser>}>
          <Route index element={<MobileHome />} />
          <Route path="klip" element={<MobileClips />} />
          <Route path="chat" element={<Chat surface="mobile" contextHint="Spørg om hvad som helst." />} />
          <Route path="filer" element={<MobileFiles />} />
          <Route path="profil" element={<Account />} />
        </Route>

        <Route path="/admin" element={<RequireUser admin><AdminLayout /></RequireUser>}>
          <Route index element={<AdminHome />} />
          <Route path="users" element={<Users />} />
          <Route path="logs/auth" element={<AuthLog />} />
          <Route path="logs/audit" element={<AuditLog />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="heatmaps" element={<Heatmaps />} />
          <Route path="gdpr" element={<Gdpr />} />
          <Route path="retention" element={<Retention />} />
          <Route path="consent" element={<Consent />} />
          <Route path="files" element={<AdminFiles />} />
          <Route path="web" element={<Web />} />
          <Route path="ai" element={<AiAdmin />} />
          <Route path="integrations" element={<Integrations />} />
          <Route path="settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      {!pathname.startsWith("/extension") && !pathname.startsWith("/office-addin") && <ConsentBanner />}
    </>
  );
}
