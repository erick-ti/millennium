"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";

/**
 * The vault-themed error boundary for the route segment (replaces Next's default
 * error page). `reset()` re-renders the failed segment; the link is the escape
 * hatch if the error persists. A root-layout crash falls through to Next's
 * built-in global error — out of scope here (it can't use the theme's fonts).
 */
export default function Error({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-6xl flex-col items-center justify-center px-6 py-24 text-center">
      <p className="font-terminal text-xs uppercase tracking-[0.3em] text-loss">
        Error
      </p>
      <h1 className="mt-4 font-display text-3xl font-semibold leading-tight tracking-tight text-bone sm:text-4xl">
        Something went wrong.
      </h1>
      <p className="mt-3 max-w-md font-body text-sm leading-relaxed text-bone-muted">
        An unexpected error interrupted this page. Try again, or head back to
        your collection.
      </p>
      <hr className="gold-rule mt-8 w-40" />
      <div className="mt-8 flex items-center gap-3">
        <Button onClick={() => reset()}>Try again</Button>
        <Link
          href="/collection"
          className="font-terminal text-xs uppercase tracking-[0.12em] text-gold-700 transition-colors hover:text-gold-500"
        >
          Back to collection
        </Link>
      </div>
    </div>
  );
}
