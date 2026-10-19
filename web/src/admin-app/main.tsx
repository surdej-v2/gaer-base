// Entry of the standalone admin app on /admin (web/admin.html). Shares login cookie, api.ts and
// auth.tsx with the app on /, but has its own shell and bundle.
import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { setApiApp } from "../api";
import { AuthProvider, useAuth } from "../auth";
import AdminShell from "./AdminShell";
import { AdminProvider } from "./context";
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
import Settings from "./pages/Settings";
import "../styles.css";
import "./admin.css";

setApiApp("admin");
installTracer();

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, scope, loading } = useAuth();
  if (loading) return null;
  if (!user || scope !== "full") {
    window.location.replace("/login");
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
            <AdminProvider>
              <Routes>
                <Route element={<AdminShell />}>
                  <Route index element={<Stats />} />
                  <Route path="users" element={<Users />} />
                  <Route path="impersonation" element={<Impersonation />} />
                  <Route path="features" element={<Features />} />
                  <Route path="app-stores" element={<AppStores />} />
                  <Route path="app-stores/:tab" element={<AppStores />} />
                  <Route path="analytics" element={legacy(<Analytics />)} />
                  <Route path="heatmaps" element={legacy(<Heatmaps />)} />
                  <Route path="logs/auth" element={legacy(<AuthLog />)} />
                  <Route path="logs/audit" element={legacy(<AuditLog />)} />
                  <Route path="web" element={legacy(<Web />)} />
                  <Route path="files" element={legacy(<AdminFiles />)} />
                  <Route path="ai" element={legacy(<AiAdmin />)} />
                  <Route path="integrations" element={legacy(<Integrations />)} />
                  <Route path="gdpr" element={legacy(<Gdpr />)} />
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
