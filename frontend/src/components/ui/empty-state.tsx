import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * A characterful, page-level empty state — a lit `.vitrine` display case with a
 * Fraunces title and quiet body copy, replacing the bare "No results." line.
 * Rendered INSTEAD of a table/grid when a surface has no data at all (so it is
 * never nested inside the table's own `.vitrine` frame — no double border). For
 * an empty row *within* a populated-shape table, `DataTable`'s own muted message
 * is used instead.
 */
export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
}: {
  title: string;
  description?: ReactNode;
  /** Optional inline glyph/SVG (no `next/image` — keep it CSS/SVG). */
  icon?: ReactNode;
  /** Optional CTA (e.g. an "Import a CSV" link/button). */
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "vitrine flex flex-col items-center rounded-lg px-6 py-14 text-center",
        className
      )}
    >
      {icon ? (
        <div className="mb-4 text-gold-700/70" aria-hidden>
          {icon}
        </div>
      ) : null}
      <p className="font-display text-lg font-semibold text-bone">{title}</p>
      {description ? (
        <p className="mt-2 max-w-sm font-body text-sm leading-relaxed text-bone-muted">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-6">{action}</div> : null}
    </div>
  );
}
