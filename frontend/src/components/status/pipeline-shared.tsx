import type { ReactNode } from "react";

import type { CheckRow, ChecksStatus, PipelineStage } from "@/lib/api";
import { formatDateTimeUtc } from "@/lib/format";

/**
 * Shared pipeline primitives consumed by BOTH the vertical timeline
 * (`pipeline-flow.tsx`, the mobile fallback) and the night-passage centerpiece
 * (`night-passage.tsx`, ≥md). Status is conveyed by a colour AND a glyph AND a word
 * (the Vault "never colour alone" rule); the dependency is always stated in text.
 */

export type StatusKey = "green" | "amber" | "red" | "grey";

export const STATUS_META: Record<
  StatusKey,
  { label: string; glyph: string; text: string; dot: string; edge: string }
> = {
  green: { label: "OK", glyph: "✓", text: "text-gain", dot: "bg-gain", edge: "border-gain" },
  amber: {
    label: "Attention",
    glyph: "!",
    text: "text-flat",
    dot: "bg-flat",
    edge: "border-flat",
  },
  red: { label: "Failed", glyph: "✕", text: "text-loss", dot: "bg-loss", edge: "border-loss" },
  grey: {
    label: "No data",
    glyph: "·",
    text: "text-muted-foreground",
    dot: "bg-muted-foreground",
    edge: "border-border",
  },
};

export const LOCALE = "en-US";

// Healthchecks status vocabulary → the flow's status light + a check-specific word.
export const CHECK_STATUS: Record<string, StatusKey> = {
  up: "green",
  grace: "amber",
  down: "red",
  paused: "grey",
  new: "grey",
};
export const CHECK_LABEL: Record<string, string> = {
  up: "Up",
  grace: "Late",
  down: "Down",
  paused: "Paused",
  new: "No pings yet",
};

export function metaFor(status: string): (typeof STATUS_META)[StatusKey] {
  return STATUS_META[status as StatusKey] ?? STATUS_META.grey;
}

// The dependency edge in WORDS, coloured by the UPSTREAM's live status so a broken
// upstream reads as broken at its dependents (paired with the coloured edge, never
// colour-only). The green wording stays "gated on <dep>" (the prior copy).
export function gateCaption(
  dependsOn: string,
  edgeStatus: StatusKey,
): { text: string; className: string } {
  switch (edgeStatus) {
    case "amber":
      return { text: `${dependsOn} incomplete — gate unmet`, className: "text-flat" };
    case "red":
      return { text: `${dependsOn} failed — gate unmet`, className: "text-loss" };
    case "grey":
      return { text: `awaiting ${dependsOn}`, className: "text-muted-foreground" };
    default: // green
      return { text: `gated on ${dependsOn}`, className: "text-muted-foreground" };
  }
}

export type Severity = "green" | "attention" | "loss" | "neutral";

// The sentinel Eye iris colour by rollup severity (gold = all-good, matching the Eye's
// lamp-lit aesthetic; amber/red for degraded; dim gold for neutral/awaiting).
export const SEVERITY_IRIS: Record<Severity, string> = {
  green: "#e6c063",
  attention: "#fbbf24",
  loss: "#f87171",
  neutral: "#9d814d",
};

// The headline reflects BOTH the internal pipeline AND the external dead-man checks,
// so it can't green-wash a degraded external tier. Policy,
// by precedence: failures (a red sync stage, or a DOWN backup/CD check) → loss; then
// attention (a stale/skipped sync, an OVERDUE `grace` check, or an external tier we
// can't confirm, provider unavailable / query failed) → flat; then never-run syncs;
// else green. DELIBERATELY NEUTRAL (never move the headline off green): a `paused`
// check (operator-intentional), a `new`/never-pinged check, an unconfigured tier, and
// a null check. `severity` is the same verdict the dawn-glow / sentinel consume, bound
// to this single source so a beacon can never disagree with the rows.
export function summarize(
  stages: PipelineStage[],
  checks?: ChecksStatus,
  checksError?: boolean,
): { text: string; className: string; severity: Severity } {
  const failedStages = stages.filter((s) => s.status === "red").length;
  if (failedStages > 0) {
    return {
      text: `${failedStages} stage${failedStages > 1 ? "s" : ""} failed`,
      className: "text-loss",
      severity: "loss",
    };
  }
  const downChecks = [checks?.backup, checks?.cd].filter((c) => c?.status === "down").length;
  if (downChecks > 0) {
    return {
      text: `${downChecks} check${downChecks > 1 ? "s" : ""} down`,
      className: "text-loss",
      severity: "loss",
    };
  }

  const attentionStages = stages.filter((s) => s.status === "amber").length;
  if (attentionStages > 0) {
    return {
      text:
        attentionStages === 1
          ? "1 stage needs attention"
          : `${attentionStages} stages need attention`,
      className: "text-flat",
      severity: "attention",
    };
  }
  const overdueChecks = [checks?.backup, checks?.cd].filter((c) => c?.status === "grace").length;
  if (overdueChecks > 0) {
    return {
      text: `${overdueChecks} check${overdueChecks > 1 ? "s" : ""} overdue`,
      className: "text-flat",
      severity: "attention",
    };
  }
  if ((checks?.configured && !checks.available) || checksError) {
    return { text: "Checks unavailable", className: "text-flat", severity: "attention" };
  }

  const grey = stages.filter((s) => s.status === "grey").length;
  if (grey === stages.length) {
    return { text: "Awaiting first run", className: "text-muted-foreground", severity: "neutral" };
  }
  if (grey > 0) {
    return {
      text: `${grey} stage${grey > 1 ? "s" : ""} not yet run`,
      className: "text-flat",
      severity: "attention",
    };
  }
  return { text: "All green today", className: "text-gain", severity: "green" };
}

export function checkNote(checks: ChecksStatus | undefined, checksError?: boolean): string {
  if (!checks) {
    // No data: a hard /checks/ query failure (checksError) vs. the initial load.
    return checksError ? "Healthchecks — couldn't load" : "Healthchecks — loading…";
  }
  if (!checks.configured) return "Healthchecks — not configured";
  if (!checks.available) return "Healthchecks — unavailable";
  return "Healthchecks — check not found"; // configured + available but the slug didn't match
}

export function DetailRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <dt className="w-28 shrink-0 uppercase tracking-[0.12em] text-gold-700">{k}</dt>
      <dd className="min-w-0 flex-1 text-bone-muted">{children}</dd>
    </div>
  );
}

export function StageDetail({ stage }: { stage: PipelineStage }) {
  const m = metaFor(stage.status);
  return (
    <dl className="space-y-1.5 font-terminal text-xs">
      <DetailRow k="Scheduled">{stage.scheduled_utc} UTC, daily</DetailRow>
      <DetailRow k="Last run">
        {stage.last_run_at ? formatDateTimeUtc(stage.last_run_at) : "—"}
      </DetailRow>
      <DetailRow k="State">
        {m.label}
        {stage.green_today ? <span className="text-gain"> · ran today</span> : null}
      </DetailRow>
      {stage.metric_value != null ? (
        <DetailRow k={stage.metric_label}>{stage.metric_value.toLocaleString(LOCALE)}</DetailRow>
      ) : null}
      {stage.depends_on ? (
        <DetailRow k="Dependency">
          Refuses to run unless the same-day {stage.depends_on} sync succeeded — the freshness
          gate.
        </DetailRow>
      ) : null}
    </dl>
  );
}

export function CheckDetail({
  check,
  cadence,
  note,
  metric,
}: {
  check: CheckRow | null | undefined;
  cadence: string;
  note: string;
  metric: string | null;
}) {
  return (
    <dl className="space-y-1.5 font-terminal text-xs">
      <DetailRow k="Cadence">{cadence}</DetailRow>
      {check ? (
        <DetailRow k="Last ping">
          {check.last_ping_at ? formatDateTimeUtc(check.last_ping_at) : "—"}
        </DetailRow>
      ) : null}
      {check ? <DetailRow k="Pings">{check.n_pings.toLocaleString(LOCALE)}</DetailRow> : null}
      {metric ? <DetailRow k="Deployed">{metric.replace(/^deployed /, "")}</DetailRow> : null}
      {!check ? <DetailRow k="Status">{note}</DetailRow> : null}
      <DetailRow k="Monitor">Healthchecks.io dead-man — flags a missed scheduled ping.</DetailRow>
    </dl>
  );
}
