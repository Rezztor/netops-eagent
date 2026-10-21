import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { initTheme } from "@/lib/theme";
import "./styles/global.css";

// Applied before first paint so the page never flashes the wrong theme.
initTheme();

const container = document.getElementById("root");
if (!container) {
  throw new Error("Missing #root element");
}

// basename matches vite's `base` and the backend mount point: client-side
// routes are /app/users, not /users.
createRoot(container).render(
  <StrictMode>
    <BrowserRouter basename="/app">
      <App />
    </BrowserRouter>
  </StrictMode>,
);
