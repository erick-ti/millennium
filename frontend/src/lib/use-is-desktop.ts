import { useSyncExternalStore } from "react";

// The breakpoint that gates the /status Timeline⇄Passage toggle + the night-passage
// centerpiece (Tailwind `md`). useSyncExternalStore is the sanctioned client-value
// pattern — no hydration ERROR on the viewport that doesn't match the server snapshot
// (a non-matching narrow client does one silent store-driven re-render).
const QUERY = "(min-width: 768px)";

function subscribe(callback: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => {};
  }
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", callback);
  return () => mql.removeEventListener("change", callback);
}

function getSnapshot(): boolean {
  // No matchMedia (jsdom under test) → mobile/timeline fallback, so the timeline tests
  // exercise the accessible vertical view without any matchMedia mock.
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia(QUERY).matches;
}

// Desktop-first SSR so the recruiter (desktop) viewport renders the toggle on first
// paint; a narrow client re-renders once to hide it. The default VIEW is the timeline on
// both, so this only affects the toggle's presence — the opt-in Passage view itself
// (persisted in localStorage) does intentionally swap in post-hydration.
function getServerSnapshot(): boolean {
  return true;
}

export function useIsDesktop(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
