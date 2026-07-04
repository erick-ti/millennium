"use client";

import { useEffect } from "react";

import { reportClientError } from "@/lib/report-error";

/**
 * Installs global handlers that beacon uncaught errors + unhandled promise rejections to
 * the backend (apps.audit). Mounted once in the root layout alongside `CsrfBootstrap`.
 * The route-segment `error.tsx` boundary reports separately (a React render error never
 * reaches `window.onerror`).
 */
export function ErrorReporter() {
  useEffect(() => {
    const onError = (event: ErrorEvent) => {
      reportClientError({
        message: event.message || "Uncaught error",
        name: event.error?.name,
        stack: event.error?.stack,
      });
    };

    const onRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason;
      reportClientError({
        message:
          reason instanceof Error
            ? reason.message
            : String(reason ?? "Unhandled promise rejection"),
        name: reason instanceof Error ? reason.name : "UnhandledRejection",
        stack: reason instanceof Error ? reason.stack : undefined,
      });
    };

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  return null;
}
