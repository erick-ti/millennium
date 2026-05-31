"use client";

import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";

import { installAuthInterceptor } from "@/lib/auth-interceptor";
import { handleQueryAuthError, shouldRetryQuery } from "@/lib/auth-redirect";

// Stamp the HTTP status onto thrown query errors (the cache handler below reads
// it). Idempotent; safe to call at module load on both server and browser.
installAuthInterceptor();

// Exported for the integration test, which exercises this exact wiring (retry
// predicate + QueryCache onError) against a real QueryClient.
export function makeQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) =>
        handleQueryAuthError(error, query.meta?.auth === "me"),
    }),
    defaultOptions: {
      queries: {
        // Pricing refreshes daily; valuations daily. Minute-scale staleTime
        // is plenty for UI freshness without thrashing the proxy.
        staleTime: 60 * 1000,
        refetchOnWindowFocus: false,
        // Fail fast on a 401/403 so the global handler redirects to /login
        // without waiting through doomed retries + backoff; keep retries for
        // transient errors (see shouldRetryQuery).
        retry: shouldRetryQuery,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

function getQueryClient() {
  // SSR-safe pattern per the TanStack Query App Router guide: a fresh client
  // on every server render (so request state can't bleed across requests),
  // and a single client reused across remounts in the browser.
  if (typeof window === "undefined") {
    return makeQueryClient();
  }
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const queryClient = getQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      {children}
      {process.env.NODE_ENV !== "production" && (
        <ReactQueryDevtools initialIsOpen={false} />
      )}
    </QueryClientProvider>
  );
}
