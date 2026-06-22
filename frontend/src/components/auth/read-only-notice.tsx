import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * Shown in place of a write form / action when the read-only demo session is active.
 * The server's ``DemoReadOnly`` permission already blocks the write (403); this keeps
 * the demo user from ever submitting one and hitting that error, and points a real owner
 * at /login. Presentational only (no hooks), so it composes inside client components.
 */
export function ReadOnlyNotice({
  className,
  children = "You’re exploring the read-only demo.",
}: {
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      role="note"
      className={cn(
        "rounded-md border border-gold-900/30 bg-vault-900/40 px-4 py-3 font-terminal text-xs leading-relaxed text-bone-muted",
        className,
      )}
    >
      {children}{" "}
      <Link
        href="/login"
        className="text-gold-500 underline-offset-2 transition-colors hover:text-gold-300 hover:underline"
      >
        Sign in
      </Link>{" "}
      to make changes.
    </div>
  );
}
