import type { ReactNode } from "react";

/**
 * A small Vault display-case tile (the `.vitrine` lit edge — gold hairline + inset
 * top light, no drop shadow) with a terminal eyebrow heading. The supporting
 * "app state" blocks around the pipeline-flow centerpiece.
 */
export function StatusTile({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="vitrine rounded-lg p-4">
      <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
        {title}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** One label/value row inside a `<dl>` — terminal numerals, no reflow. */
export function MetricRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-terminal text-sm tabular-nums text-foreground">{children}</dd>
    </div>
  );
}
