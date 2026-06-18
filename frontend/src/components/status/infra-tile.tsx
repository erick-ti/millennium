import type { InfraStatus } from "@/lib/api";
import { MetricRow, StatusTile } from "@/components/status/status-tile";
import { Sparkline } from "@/components/status/sparkline";
import { formatDateTimeUtc } from "@/lib/format";

/** Percent of a capacity, integer, clamped to 100; "—" when the total is unknown/zero. */
function pct(used: number, total: number): string {
  if (total <= 0) return "—";
  return `${Math.min(100, Math.round((used / total) * 100))}%`;
}

/** kbit/s, rolled up to Mbit/s past 1000 so a busy moment stays readable. */
function formatRate(kbps: number): string {
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbps`;
  return `${kbps.toFixed(0)} kbps`;
}

/**
 * The "box it all runs on" tile — CPU/mem/disk/load + a CPU sparkline, from the
 * host-collector samples (NOT a host call; the backend container can't read host
 * /proc, so a host timer writes the samples to Postgres). Three states:
 *   - no sample at all (loading, dev, or before the first timer tick) → "Awaiting…"
 *   - a sample past the freshness window → the last-known values + a "stale" note
 *   - a fresh sample → the live values
 */
export function InfraTile({
  infra,
  error,
}: {
  infra: InfraStatus | undefined;
  error?: boolean;
}) {
  // A first-load failure of /api/status/infra/ (500, auth regression, missing table,
  // proxy error) — kept DISTINCT from the legitimate no-sampler state so a broken
  // backend isn't green-washed as "Awaiting" (the dashboard exists to SURFACE failure).
  // Mirrors the checks tier's error≠no-data handling. On a refetch error the data
  // survives (keepPreviousData) so the last-known values stay shown.
  if (error && !infra) {
    return (
      <StatusTile title="Host box">
        <p className="text-sm text-loss">Host metrics unavailable.</p>
      </StatusTile>
    );
  }

  // No sample to show yet (still loading, or no collector has ever run).
  if (!infra || (!infra.available && !infra.stale)) {
    return (
      <StatusTile title="Host box">
        <p className="text-sm text-muted-foreground">Awaiting host metrics.</p>
      </StatusTile>
    );
  }

  // available OR stale → there are last-known values to render.
  return (
    <StatusTile title="Host box">
      <dl className="space-y-0.5">
        <MetricRow label="CPU">
          {infra.cpu_percent == null ? "—" : `${infra.cpu_percent.toFixed(0)}%`}
        </MetricRow>
        {infra.cpu_series.length > 1 ? (
          <Sparkline
            values={infra.cpu_series}
            max={100}
            label="CPU over the last hour"
            className="my-1 h-6 w-full text-gold-700"
          />
        ) : null}
        <MetricRow label="Load (1m)">
          {infra.load_1m == null ? "—" : infra.load_1m.toFixed(2)}
        </MetricRow>
        <MetricRow label="Memory">
          {infra.mem_used_mb == null || infra.mem_total_mb == null
            ? "—"
            : `${(infra.mem_used_mb / 1024).toFixed(1)} / ${(
                infra.mem_total_mb / 1024
              ).toFixed(1)} GB (${pct(infra.mem_used_mb, infra.mem_total_mb)})`}
        </MetricRow>
        <MetricRow label="Disk">
          {infra.disk_used_gb == null || infra.disk_total_gb == null
            ? "—"
            : `${infra.disk_used_gb.toFixed(0)} / ${infra.disk_total_gb.toFixed(
                0,
              )} GB (${pct(infra.disk_used_gb, infra.disk_total_gb)})`}
        </MetricRow>
        <MetricRow label="Net (rx/tx)">
          {infra.net_rx_kbps == null || infra.net_tx_kbps == null
            ? "—"
            : `${formatRate(infra.net_rx_kbps)} / ${formatRate(infra.net_tx_kbps)}`}
        </MetricRow>
      </dl>
      {infra.stale ? (
        <p className="pt-1 text-xs text-flat">
          Stale — last sample{" "}
          {infra.sampled_at ? formatDateTimeUtc(infra.sampled_at) : "unknown"}.
        </p>
      ) : null}
    </StatusTile>
  );
}
