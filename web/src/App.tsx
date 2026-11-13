import { useEffect, type ReactNode } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import manifest from "./routes.json";
import { FeatureGate } from "./features";
import { GoPage, useDeepLinkFocus } from "./deeplink";
import ConsentBanner from "./ConsentBanner";
import { StepUpDialog } from "./security";
import { trackPageview } from "./tracker";
import { noteVisit } from "./adoption";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Account from "./pages/Account";
import AiUsage from "./pages/AiUsage";
import Files from "./pages/Files";
import Privacy from "./pages/Privacy";
import McpConsent from "./pages/McpConsent";
import { DelegatedArea, UserHome, UserSettings, UserSharing } from "./pages/UserArea";
import UserMail, { UserMailById } from "./pages/Mail";
import Approve from "./pages/Approve";
import { StudioLayout } from "./developer/DeveloperLayout";
import DeveloperShell from "./developer/DeveloperShell";
import DevOverview from "./developer/Overview";
import { ApprovalDetail, ApprovalsHistory, ApprovalsMine } from "./developer/Approvals";
import { CatalogDataset, CatalogLineage, CatalogList } from "./developer/Catalog";
import { ArtifactPage } from "./developer/devui";
import DevHelp, { Soon } from "./developer/Help";
import StudioOverview from "./developer/StudioOverview";
import ApiStudio from "./developer/ApiStudio";
import McpStudio from "./developer/McpStudio";
import Prompts from "./developer/Prompts";
import Trees from "./developer/Trees";
import Inbox from "./pages/Inbox";
import Conversations from "./pages/Conversations";
import ExtensionLayout from "./extension/ExtensionLayout";
import ExtensionPage from "./extension/ExtensionPage";
import ExtensionAccount from "./extension/ExtensionAccount";
import ExtensionChat from "./extension/ExtensionChat";
import ExtensionMeta from "./extension/ExtensionMeta";
import { Chat } from "./ai";
import ClipsPage, { ClipsList, useClips } from "./clips";
import OfficeShell from "./office/OfficeShell";
import { DocumentTool, OutlookTool, ToolLayout, WordTool } from "./office/Tools";
import { MobileClips, MobileFiles, MobileHome, MobileLayout } from "./mobile/Mobile";
import ReviewLauncher from "./review/ReviewLauncher";
import { AgentChat, AgentList } from "./knowledge/Agents";
import { AppShell } from "./shell";

function ImpersonationBanner() {
  const { user, impersonator } = useAuth();
  if (!impersonator) return null;
  return (
    <div style={{ padding: "8px 16px", background: "#fffbeb", borderBottom: "1px solid rgba(245,158,11,0.4)", color: "#78350f", fontSize: 13, textAlign: "center" }}>
      Du ser appen som testkontoen <strong>{user?.display_name}</strong>. Stop efterligningen i <a href="/admin/impersonation">/admin</a>.
    </div>
  );
}

/** The top bar for visitors who are not (fully) logged in: /login, /privatliv, /go. Signed-in users get AppShell. */
function TopBar() {
  const { user, scope, config, logout } = useAuth();
  const full = user && scope === "full";
  return (
    <>
    <ImpersonationBanner />
    <header className="topbar">
      <div className="topbar-inner">
        <Link to="/" className="brand">
          {config?.logo_url ? <img src={config.logo_url} alt="" /> : <span className="brand-dot" />}
          {config?.app_name ?? "Tørgær"}
        </Link>
        <nav className="nav">
          {full && <FeatureGate feature="klip"><NavLink to="/klip">Klip</NavLink></FeatureGate>}
          {full && <FeatureGate feature="filer"><NavLink to="/filer">Filer</NavLink></FeatureGate>}
          {full && user.role === "admin" && user.admin_sections.includes("/developer") && <NavLink to="/developer">Udvikler</NavLink>}
          {full && <NavLink to="/user">Mit område</NavLink>}
          {full && user.role === "admin" && <a href="/admin">Admin</a>}
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
    </>
  );
}

function RequireUser({ children, login = "/login" }: { children: ReactNode; login?: string }) {
  const { user, scope, loading } = useAuth();
  const location = useLocation();
  if (loading) return null;
  if (!user || scope !== "full") return <Navigate to={login} replace state={{ from: location.pathname + location.search + location.hash }} />;
  return <>{children}</>;
}

function PageTracker() {
  const location = useLocation();
  const { user, scope } = useAuth();
  const signedIn = !!user && scope === "full";
  useEffect(() => {
    trackPageview(location.pathname);
  }, [location.pathname]);
  useEffect(() => {
    if (signedIn) noteVisit(location.pathname);
  }, [location.pathname, signedIn]);
  return null;
}

// Embedded surfaces (browser extension, Office) and the mobile app have their own chrome: "chrome": "own" in
// routes.json, the one description of every route (npm run app-map checks it against the <Route>s below).
const OWN_CHROME = [...new Set(manifest.routes.filter((r) => "chrome" in r && r.chrome === "own").map((r) => r.path))];

function ExtensionClips() {
  const clips = useClips();
  return <ClipsList clips={clips} compact source="extension" actions={(c) => (
    <button className="btn btn-outline btn-sm" onClick={() => navigator.clipboard.writeText(c.body)}>Kopiér</button>
  )} />;
}

// /develop moved to /developer (the prompt catalogue is now /developer/prompts).
const MOVED: Record<string, string> = { "/develop": "/developer/prompts", "/develop/trae": "/developer/trae" };

export default function App() {
  const { pathname } = useLocation();
  useDeepLinkFocus();
  const { user, scope } = useAuth();
  if (MOVED[pathname]) return <Navigate to={MOVED[pathname]} replace />;
  const ownChrome = OWN_CHROME.some((p) => pathname === p || pathname.startsWith(p + "/"));
  // Signed-in pages get the shell (sidepanel, assistant, phone toolbar); everyone else the plain top bar.
  const shelled = !ownChrome && !!user && scope === "full" && pathname !== "/login";
  const routes = (
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="/privatliv" element={<Privacy />} />
        <Route path="/konto" element={<RequireUser><Account /></RequireUser>} />
        <Route path="/user" element={<RequireUser><UserHome /></RequireUser>} />
        <Route path="/user/indstillinger" element={<RequireUser><UserSettings /></RequireUser>} />
        <Route path="/user/deling" element={<RequireUser><UserSharing /></RequireUser>} />
        <Route path="/user/mail" element={<RequireUser><FeatureGate feature="mail"><UserMail /></FeatureGate></RequireUser>} />
        <Route path="/user/:id" element={<RequireUser><DelegatedArea /></RequireUser>} />
        <Route path="/user/:id/mail" element={<RequireUser><FeatureGate feature="mail"><UserMailById /></FeatureGate></RequireUser>} />
        <Route path="/konto/forbrug" element={<RequireUser><AiUsage /></RequireUser>} />
        <Route path="/beskeder" element={<RequireUser><FeatureGate feature="beskeder"><Inbox /></FeatureGate></RequireUser>} />
        <Route path="/beskeder/:id" element={<RequireUser><FeatureGate feature="beskeder"><Inbox /></FeatureGate></RequireUser>} />
        <Route path="/samtaler" element={<RequireUser><FeatureGate feature="samtaler"><Conversations /></FeatureGate></RequireUser>} />
        <Route path="/samtaler/:id" element={<RequireUser><FeatureGate feature="samtaler"><Conversations /></FeatureGate></RequireUser>} />
        <Route path="/forbind" element={<RequireUser><McpConsent /></RequireUser>} />
        <Route path="/godkend" element={<RequireUser><Approve /></RequireUser>} />
        <Route path="/filer" element={<RequireUser><FeatureGate feature="filer"><Files /></FeatureGate></RequireUser>} />
        <Route path="/klip" element={<RequireUser><FeatureGate feature="klip"><ClipsPage /></FeatureGate></RequireUser>} />
        <Route path="/klip/:id" element={<RequireUser><FeatureGate feature="klip"><ClipsPage /></FeatureGate></RequireUser>} />
        <Route path="/go" element={<GoPage />} />
        <Route path="/developer" element={<RequireUser><DeveloperShell /></RequireUser>}>
          <Route index element={<DevOverview />} />
          <Route path="datastudie" element={<Soon title="Datastudie" text="Beskriv data med AI og udgiv som MCP-endpoint." />} />
          <Route path="pipeline" element={<Soon title="Pipeline" text="Byg snapshots af data på forhånd." />} />
          <Route path="api" element={<Soon title="API-studie" text="REST-API'er over de samme operationer, med OpenAPI og nøgler." />} />
          <Route path="forbindelser" element={<Soon title="Forbindelser" text="Databaser via FDW eller direkte, live-kilder og cache." />} />
          <Route path="eksport" element={<Soon title="Eksport og import" text="Flyt artefakter mellem instanser." />} />
          <Route path="katalog" element={<CatalogList />} />
          <Route path="katalog/lineage" element={<CatalogLineage />} />
          <Route path="katalog/:id" element={<CatalogDataset />} />
          <Route path="godkendelser" element={<ApprovalsMine />} />
          <Route path="godkendelser/historik" element={<ApprovalsHistory />} />
          <Route path="godkendelser/:id" element={<ApprovalDetail />} />
          <Route path="artefakter/:id" element={<ArtifactPage />} />
          <Route path="hjaelp" element={<DevHelp />} />
          <Route path="studio" element={<StudioLayout />}>
            <Route index element={<StudioOverview />} />
            <Route path="api" element={<ApiStudio />} />
            <Route path="mcp" element={<McpStudio />} />
          </Route>
          <Route path="prompts" element={<Prompts />} />
          <Route path="trae" element={<Trees />} />
        </Route>

        <Route path="/extension/login" element={<Login home="/extension" embedded />} />
        <Route path="/extension/tools" element={<RequireUser login="/extension/login"><ToolLayout /></RequireUser>}>
          <Route path="word" element={<WordTool />} />
          <Route path="outlook" element={<OutlookTool />} />
          <Route path="document" element={<DocumentTool />} />
        </Route>
        <Route path="/extension/agent" element={<RequireUser login="/extension/login"><FeatureGate feature="knowledge"><AgentList /></FeatureGate></RequireUser>} />
        <Route path="/extension/agent/:slug" element={<RequireUser login="/extension/login"><FeatureGate feature="knowledge"><AgentChat /></FeatureGate></RequireUser>} />
        <Route path="/extension" element={<RequireUser login="/extension/login"><ExtensionLayout /></RequireUser>}>
          <Route index element={<ExtensionPage />} />
          <Route path="chat" element={<ExtensionChat />} />
          <Route path="klip" element={<ExtensionClips />} />
          <Route path="meta" element={<ExtensionMeta />} />
          <Route path="konto" element={<ExtensionAccount />} />
        </Route>
        <Route path="/office-addin" element={<OfficeShell />} />

        <Route path="/m/login" element={<Login home="/m" embedded />} />
        <Route path="/m" element={<RequireUser login="/m/login"><MobileLayout /></RequireUser>}>
          <Route index element={<MobileHome />} />
          <Route path="klip" element={<MobileClips />} />
          <Route path="chat" element={<Chat surface="mobile" contextHint="Spørg om hvad som helst." />} />
          <Route path="filer" element={<MobileFiles />} />
          <Route path="beskeder" element={<FeatureGate feature="beskeder"><Inbox base="/m/beskeder" compact /></FeatureGate>} />
          <Route path="beskeder/:id" element={<FeatureGate feature="beskeder"><Inbox base="/m/beskeder" /></FeatureGate>} />
          <Route path="samtaler" element={<FeatureGate feature="samtaler"><Conversations base="/m/samtaler" compact /></FeatureGate>} />
          <Route path="samtaler/:id" element={<FeatureGate feature="samtaler"><Conversations base="/m/samtaler" compact /></FeatureGate>} />
          <Route path="profil" element={<Account />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
  );
  return (
    <>
      {shelled ? <ImpersonationBanner /> : !ownChrome && <TopBar />}
      <PageTracker />
      {shelled ? <AppShell>{routes}</AppShell> : routes}
      <StepUpDialog />
      {/* The review panel (⌃⌥⌘W or ?review=1): logged-in users only, loaded when it opens. Not in embedded surfaces. */}
      {!ownChrome && <ReviewLauncher />}
      {!pathname.startsWith("/extension") && !pathname.startsWith("/office-addin") && <ConsentBanner />}
    </>
  );
}
