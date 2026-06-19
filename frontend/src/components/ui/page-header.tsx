import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The shared authed page masthead — the single biggest lift from "reskinned
 * dashboard" to the landing's bar. A mono kicker (thematic uppercase label, the
 * same `font-terminal … tracking-[0.3em] text-gold-900` eyebrow the landing's
 * section headers use), a Fraunces display title, an optional subtitle, and the
 * `.gold-rule` hairline that closes every section on the landing. An optional
 * right-aligned `actions` slot carries page-level controls (a live indicator, a
 * window selector, a primary CTA) the way the landing's Curve floats its window
 * pills beside the heading.
 */
export function PageHeader({
  kicker,
  title,
  subtitle,
  actions,
  titleClassName,
  className,
}: {
  /** Thematic mono label, e.g. "LEDGER", "THE WATCH", "RECONCILIATION". */
  kicker: string;
  title: ReactNode;
  subtitle?: ReactNode;
  /** Right-aligned controls (status dot, filter, CTA). */
  actions?: ReactNode;
  titleClassName?: string;
  className?: string;
}) {
  return (
    <header className={cn("mb-8", className)}>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="font-terminal text-xs uppercase tracking-[0.3em] text-gold-900">
            {kicker}
          </p>
          <h1
            className={cn(
              "mt-3 font-display text-3xl font-semibold leading-tight tracking-tight text-bone sm:text-4xl",
              titleClassName
            )}
          >
            {title}
          </h1>
          {subtitle ? (
            <p className="mt-2 max-w-2xl font-body text-sm leading-relaxed text-bone-muted">
              {subtitle}
            </p>
          ) : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </div>
      <hr className="gold-rule mt-6" />
    </header>
  );
}
