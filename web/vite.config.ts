import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Two apps in one build: the app on / (index.html) and the standalone admin app on /admin (admin.html).
// In production the Python server picks the HTML file by path; in development this plugin does.
function adminEntry(): Plugin {
  return {
    name: "gaer-admin-entry",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const r = req as unknown as { url?: string };
        const path = (r.url ?? "").split("?")[0];
        if ((path === "/admin" || path.startsWith("/admin/")) && !path.includes(".")) r.url = "/admin.html";
        next();
      });
    },
  };
}

// In development Vite serves the frontend on :5173 and proxies the API to the Python server on :8000.
// In production the Python server serves the built files from web/dist itself.
export default defineConfig({
  plugins: [react(), adminEntry()],
  build: {
    rollupOptions: {
      input: { main: "index.html", admin: "admin.html" },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
