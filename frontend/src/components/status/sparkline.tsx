/**
 * A tiny dependency-free sparkline (inline SVG polyline) for the status infra tile.
 * Pure + static (reduced-motion-safe), so it needs no charting lib; the series is
 * summarised in an aria-label and the line itself is decorative. Values are clamped
 * into [0, max] and the line spans the full viewBox (stretched by the container).
 */
export function Sparkline({
  values,
  max = 100,
  className,
  label,
}: {
  values: number[];
  max?: number;
  className?: string;
  label: string;
}) {
  const W = 100;
  const H = 24;
  if (values.length < 2) {
    return (
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={className}
        role="img"
        aria-label={`${label}: not enough data yet`}
      />
    );
  }
  const ceiling = Math.max(max, ...values) || 1;
  const step = W / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * step;
      const clamped = Math.max(0, Math.min(v, ceiling));
      const y = H - (clamped / ceiling) * H;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const latest = values[values.length - 1];
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className={className}
      role="img"
      aria-label={`${label}: latest ${latest.toFixed(0)}, ${values.length} samples`}
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
