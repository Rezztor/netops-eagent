import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// The backend to proxy to in development. The app is served same-origin in
// production (see the proxy note below), so the dev server has to look
// same-origin too or the session cookie will not be sent.
const BACKEND = process.env.EAGENT_BACKEND ?? "http://127.0.0.1:8080";

export default defineConfig({
  // Served from /app by the backend (helpers/ui_server.py), because Flask
  // already owns "/" for the inherited interface. Set in dev as well as in the
  // build so the two behave identically rather than only resembling each other.
  base: "/app/",
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    // Bind explicitly. Vite defaults to "localhost", which on a dual-stack
    // machine listens on ::1 only -- so anything reaching for 127.0.0.1 gets
    // connection refused, which is a confusing way to start a project.
    host: "127.0.0.1",
    port: 5273,
    // Why a proxy rather than calling the backend directly on its own origin:
    // authentication is a Flask session cookie with SameSite=Lax plus a CSRF
    // token, which only works when the page and the API share an origin. The
    // backend serves the built frontend at /app so production has one origin;
    // this proxy mirrors that in development so the two behave identically.
    proxy: {
      "/api": { target: BACKEND, changeOrigin: false },
      "/login": { target: BACKEND, changeOrigin: false },
      "/logout": { target: BACKEND, changeOrigin: false },
      "/socket.io": { target: BACKEND, changeOrigin: false, ws: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
