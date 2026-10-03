/**
 * Application entry point.
 *
 * Mounts the React tree into the `#root` element of `index.html` and
 * enables StrictMode so accidental side effects in development surface
 * immediately. Production builds are served by nginx (see
 * `frontend/Dockerfile`); this file is the same in dev (`vite`) and
 * prod (the static bundle emitted by `vite build`).
 */
import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./App";
import { AuthProvider } from "./features/auth/AuthContext";
import { ErrorBoundary, ToastProvider } from "@ws/ui";
import { logger } from "./utils/logger";
import "./index.css";

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Root element #root not found in index.html");

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <ErrorBoundary
      onError={(error, errorInfo) =>
        logger.error("React component boundary caught error", {
          error,
          errorInfo,
        })
      }
    >
      <ToastProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ToastProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
