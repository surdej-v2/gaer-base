// Entry of the standalone admin app on /admin (web/admin.html). Shares only the login cookie, api.ts,
// auth.tsx, bridge/, deeplink.tsx and routes.json with the app on /; shell, components and styles are its own (enforced in vite.config.ts).
import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { setApiApp } from "../api";
import { AuthProvider, useAuth } from "../auth";
import AdminShell from "./AdminShell";
import { AdminProvider } from "./context";
import { AdminMfaGate, AdminStepUp } from "./mfa";
import { ToastProvider } from "./ui";
import { installTracer } from "./tracer/tracerStore";
import Stats from "./pages/Stats";
import Users from "./pages/Users";
import Impersonation from "./pages/Impersonation";
import Features from "./pages/Features";
import AppStores from "./pages/AppStores";
import AuthLog from "./pages/AuthLog";
import AuditLog from "./pages/AuditLog";
import Analytics from "./pages/Analytics";
import Heatmaps from "./pages/Heatmaps";
import Gdpr from "./pages/Gdpr";
import Retention from "./pages/Retention";
import Consent from "./pages/Consent";
import AdminFiles from "./pages/AdminFiles";
import Web from "./pages/Web";
import Integrations from "./pages/Integrations";
import AiAdmin from "./pages/AiAdmin";
import Consumption from "./pages/Consumption";
import Settings from "./pages/Settings";
import Mcp from "./pages/Mcp";
import Webhooks from "./pages/Webhooks";
import Jobs from "./pages/Jobs";
import KnowledgeOverview from "./pages/knowledge/Overview";
import KnowledgeSpace from "./pages/knowledge/Space";
import KnowledgeAgent from "./pages/knowledge/Agent";
import Meta from "./pages/Meta";
import Nats from "./pages/Nats";
import Resources from "./pages/Resources";
import MailAdmin from "./pages/mail/MailAdmin";
import MailUser from "./pages/mail/MailUser";
import Secrets from "./pages/secrets/Secrets";
import SecretDetail from "./pages/secrets/SecretDetail";
import Groups from "./pages/Groups";
import MessagesOverview from "./pages/messages/Overview";
import SendMessage from "./pages/messages/Send";
import Replies from "./pages/messages/Replies";
import Moderation from "./pages/messages/Moderation";
import Delivery from "./pages/messages/Delivery";
import MessageSettingsPage from "./pages/messages/Settings";
import DataCatalog from "./pages/data/Catalog";
import DataDataset from "./pages/data/Dataset";
import DataExplore from "./pages/data/Explore";
import DataConnect from "./pages/data/Connect";
import { AdoptionLayout } from "./pages/adoption/shared";
import AdoptionOverview from "./pages/adoption/Overview";
import AdoptionJourney, { AdoptionJourneys } from "./pages/adoption/Journey";
import AdoptionRoutes from "./pages/adoption/Routes";
import AdoptionConsole from "./pages/adoption/Console";
import AdoptionAccess from "./pages/adoption/Access";
import DataTools from "./pages/data/Tools";
import Access from "./pages/Access";
import LoginProviders from "./pages/loginProviders/Overview";
import LoginProviderSetup from "./pages/loginProviders/Setup";
import { AdminGo } from "./go";
import "./base.css";
import "./admin.css";

setApiApp("admin");
installTracer();

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, scope, loading } = useAuth();
  if (loading) return null;
  if (!user || scope !== "full") {
    window.location.replace(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    return null;
  }
  if (user.role !== "admin") {
    return (
      <div style={{ maxWidth: 480, margin: "15vh auto", padding: 24, fontFamily: "var(--hm-font-sans)", display: "flex", flexDirection: "column", gap: 12 }}>
        <h1 style={{ fontSize: 26 }}>Kræver admin-rollen</h1>
        <p style={{ color: "#64748b" }}>Du er logget ind, men din bruger har ikke adgang til /admin.</p>
        <a href="/">← Til appen på /</a>
      </div>
    );
  }
  // After the grace logins /admin needs a second factor in this session (the server enforces it as well).
  if (!user.mfa.admin_ok) return <AdminMfaGate />;
  return <>{children}</>;
}

/** The pages from the old /admin, moved in unchanged. */
const legacy = (page: ReactNode) => <div className="tg-legacy">{page}</div>;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename="/admin">
      <AuthProvider>
        <RequireAdmin>
          <ToastProvider>
            <AdminStepUp />
            <AdminProvider>
              <Routes>
                <Route path="go" element={<AdminGo />} />
                <Route element={<AdminShell />}>
                  <Route index element={<Stats />} />
                  <Route path="users" element={<Users />} />
                  <Route path="impersonation" element={<Impersonation />} />
                  <Route path="login-providers" element={<LoginProviders />} />
                  <Route path="login-providers/:key" element={<LoginProviderSetup />} />
                  <Route path="features" element={<Features />} />
                  <Route path="app-stores" element={<AppStores />} />
                  <Route path="app-stores/:tab" element={<AppStores />} />
                  <Route path="analytics" element={legacy(<Analytics />)} />
                  <Route path="heatmaps" element={legacy(<Heatmaps />)} />
                  <Route path="adoption" element={<AdoptionLayout />}>
                    <Route index element={<AdoptionOverview />} />
                    <Route path="journeys" element={<AdoptionJourneys />} />
                    <Route path="journeys/:id" element={<AdoptionJourney />} />
                    <Route path="journeys/:id/setup" element={<AdoptionJourney setup />} />
                    <Route path="routes" element={<AdoptionRoutes />} />
                    <Route path="console" element={<AdoptionConsole />} />
                    <Route path="access" element={<AdoptionAccess />} />
                  </Route>
                  <Route path="logs/auth" element={legacy(<AuthLog />)} />
                  <Route path="logs/audit" element={legacy(<AuditLog />)} />
                  <Route path="web" element={legacy(<Web />)} />
                  <Route path="files" element={<AdminFiles />} />
                  <Route path="knowledge" element={<KnowledgeOverview />} />
                  <Route path="knowledge/spaces/:id" element={<KnowledgeSpace />} />
                  <Route path="knowledge/agents/:id" element={<KnowledgeAgent />} />
                  <Route path="meta" element={<Meta />} />
                  <Route path="ai" element={legacy(<AiAdmin />)} />
                  <Route path="consumption" element={<Consumption />} />
                  <Route path="integrations" element={legacy(<Integrations />)} />
                  <Route path="mcp" element={<Mcp />} />
                  <Route path="webhooks" element={legacy(<Webhooks />)} />
                  <Route path="jobs" element={legacy(<Jobs />)} />
                  <Route path="nats" element={<Nats />} />
                  <Route path="resources" element={<Resources />} />
                  <Route path="mail" element={<MailAdmin />} />
                  <Route path="mail/users/:id" element={<MailUser />} />
                  <Route path="secrets" element={legacy(<Secrets />)} />
                  <Route path="secrets/:id" element={legacy(<SecretDetail />)} />
                  <Route path="groups" element={legacy(<Groups />)} />
                  <Route path="messages" element={<MessagesOverview />} />
                  <Route path="messages/send" element={<SendMessage />} />
                  <Route path="messages/replies" element={<Replies />} />
                  <Route path="messages/moderation" element={<Moderation />} />
                  <Route path="messages/delivery" element={<Delivery />} />
                  <Route path="messages/settings" element={<MessageSettingsPage />} />
                  <Route path="data" element={<DataCatalog />} />
                  <Route path="data/explore" element={<DataExplore />} />
                  <Route path="data/connect" element={<DataConnect />} />
                  <Route path="data/tools" element={legacy(<DataTools />)} />
                  <Route path="data/:id" element={<DataDataset />} />
                  <Route path="gdpr" element={legacy(<Gdpr />)} />
                  <Route path="access" element={legacy(<Access />)} />
                  <Route path="retention" element={legacy(<Retention />)} />
                  <Route path="consent" element={legacy(<Consent />)} />
                  <Route path="settings" element={legacy(<Settings />)} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Route>
              </Routes>
            </AdminProvider>
          </ToastProvider>
        </RequireAdmin>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
