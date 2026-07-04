"use client";

import { createContext, useContext } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { type User, authMeRetrieveOptions, authMeRetrieveQueryKey } from "@/lib/api";

interface AuthState {
  /** The signed-in user, or null when anonymous / still loading. */
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** True when the session is the read-only demo account (write affordances hidden). */
  isDemo: boolean;
  /** Owner session — authenticated AND not the demo; may perform writes. */
  canWrite: boolean;
  /** Django superuser — gates the owner-only /ops console link (display only; the
   *  server enforces the real boundary via IsSuperUser). */
  isSuperuser: boolean;
  /** Re-probe `/api/auth/me` (after login/logout changes the session). */
  refetch: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

/**
 * Runs the `/api/auth/me` session probe and exposes auth state to the nav and
 * the login page. Does NOT gate the tree: pages render and a read 403 triggers
 * the global redirect (see `providers.tsx`). The probe query is tagged
 * `meta: { auth: "me" }` so its own 403 (the expected anonymous signal) is
 * exempt from that redirect — otherwise it would loop.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    ...authMeRetrieveOptions(),
    meta: { auth: "me" },
    retry: false, // a 403 is a definitive "anonymous", not a transient failure
    staleTime: 5 * 60 * 1000,
  });

  const user = query.data ?? null;
  const isAuthenticated = !query.isError && user !== null;
  // Read the server's capability flag rather than string-matching a hard-coded demo
  // username — the backend (UserSerializer.is_demo) is the single source of truth.
  const isDemo = isAuthenticated && (user?.is_demo ?? false);
  const value: AuthState = {
    user,
    isLoading: query.isLoading,
    isAuthenticated,
    isDemo,
    canWrite: isAuthenticated && !isDemo,
    isSuperuser: isAuthenticated && (user?.is_superuser ?? false),
    refetch: () => {
      void queryClient.invalidateQueries({ queryKey: authMeRetrieveQueryKey() });
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === null) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
