import type { CheckRow, ChecksStatus, PipelineStage } from "@/lib/api";
import { formatDateTimeUtc } from "@/lib/format";

/**
 * The centerpiece: the nightly lifecycle rendered as a LIVE flow, not a grid of tiles.
 * The internal stages (metadata → pricing → valuation → alerts) come from
 * /api/status/overview/, lit by their real run-history status; the trailing backup + CD
 * stages come from /api/status/checks/ (the Healthchecks dead-men), and render grey with
 * a reason when that tier is loading / unconfigured / unavailable. The dependency edges
 * (valuation + alerts gate on the same-day pricing run) are annotated so you can see the
 * chain — and where it would break.
 *
 * Pure presentational + all-text (no SVG): status is conveyed by a color AND a glyph AND
 * a word (the Vault "never color alone" rule), so assistive tech reads the full state
 * from the DOM. Rendered in the Engine Room sober-terminal register.
 */

type StatusKey = "green" | "amber" | "red" | "grey";

const STATUS_META: Record<
  StatusKey,
  { label: string; glyph: string; text: string; dot: string }
> = {
  green: { label: "OK", glyph: "✓", text: "text-gain", dot: "bg-gain" },
  amber: { label: "Attention", glyph: "!", text: "text-flat", dot: "bg-flat" },
  red: { label: "Failed", glyph: "✕", text: "text-loss", dot: "bg-loss" },
  grey: {
    label: "No data",
    glyph: "·",
    text: "text-muted-foreground",
    dot: "bg-muted-foreground",
  },
};

const LOCALE = "en-US";

// Healthchecks status vocabulary → the flow's status light + a check-specific word.
const CHECK_STATUS: Record<string, StatusKey> = {
  up: "green",
  grace: "amber",
  down: "red",
  paused: "grey",
  new: "grey",
};
const CHECK_LABEL: Record<string, string> = {
  up: "Up",
  grace: "Late",
  down: "Down",
  paused: "Paused",
  new: "No pings yet",
};

function metaFor(status: string): (typeof STATUS_META)[StatusKey] {
  return STATUS_META[status as StatusKey] ?? STATUS_META.grey;
}

// The shared timeline rail: a status dot over a connecting line (omitted on the last
// node). The ring matches the panel background so the dot reads as a bead on the rail.
function Rail({ last, dotClass }: { last: boolean; dotClass?: string }) {
  return (
    <div className="relative flex flex-col items-center" aria-hidden>
      <span
        className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ring-2 ring-vault-900 ${dotClass ?? "bg-muted-foreground"}`}
      />
      {!last ? <span className="mt-1 w-px grow bg-border" /> : null}
    </div>
  );
}

function StageNode({ stage, last }: { stage: PipelineStage; last: boolean }) {
  const m = metaFor(stage.status);
  return (
    <li className="flex gap-4 pb-6 last:pb-0">
      <Rail last={last} dotClass={m.dot} />
      <div className="-mt-0.5 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4">
          <h3 className="text-sm font-medium text-foreground">{stage.label}</h3>
          <span className="font-terminal text-xs text-muted-foreground">
            {stage.scheduled_utc} UTC
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className={`inline-flex items-center gap-1 font-medium ${m.text}`}>
            <span aria-hidden>{m.glyph}</span>
            {m.label}
          </span>
          {stage.metric_value != null ? (
            <span className="font-terminal tabular-nums text-bone-muted">
              {stage.metric_value.toLocaleString(LOCALE)} {stage.metric_label}
            </span>
          ) : null}
          {stage.last_run_at ? (
            <span className="font-terminal text-muted-foreground">
              · {formatDateTimeUtc(stage.last_run_at)}
            </span>
          ) : null}
        </div>
        {stage.depends_on ? (
          <p className="mt-1 font-terminal text-xs text-muted-foreground">
            ↳ gated on {stage.depends_on}
          </p>
        ) : null}
      </div>
    </li>
  );
}

// A trailing flow node backed by a Healthchecks dead-man. Renders the real status when
// the check is present, else a grey "not wired" row carrying the reason (loading /
// unconfigured / unavailable / slug-not-found).
function CheckNode({
  label,
  cadence,
  check,
  note,
  last,
}: {
  label: string;
  cadence: string;
  check: CheckRow | null | undefined;
  note: string;
  last: boolean;
}) {
  if (!check) {
    return (
      <li className="flex gap-4 pb-6 last:pb-0">
        <Rail last={last} />
        <div className="-mt-0.5 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4">
            <h3 className="text-sm font-medium text-muted-foreground">{label}</h3>
            <span className="font-terminal text-xs text-muted-foreground">{cadence}</span>
          </div>
          <p className="mt-1 font-terminal text-xs text-muted-foreground">{note}</p>
        </div>
      </li>
    );
  }
  const m = metaFor(CHECK_STATUS[check.status] ?? "grey");
  return (
    <li className="flex gap-4 pb-6 last:pb-0">
      <Rail last={last} dotClass={m.dot} />
      <div className="-mt-0.5 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4">
          <h3 className="text-sm font-medium text-foreground">{label}</h3>
          <span className="font-terminal text-xs text-muted-foreground">{cadence}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className={`inline-flex items-center gap-1 font-medium ${m.text}`}>
            <span aria-hidden>{m.glyph}</span>
            {CHECK_LABEL[check.status] ?? check.status}
          </span>
          {check.last_ping_at ? (
            <span className="font-terminal text-muted-foreground">
              · last ping {formatDateTimeUtc(check.last_ping_at)}
            </span>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function checkNote(checks: ChecksStatus | undefined, checksError?: boolean): string {
  if (!checks) {
    // No data: a hard /checks/ query failure (checksError) vs. the initial load.
    return checksError ? "Healthchecks — couldn't load" : "Healthchecks — loading…";
  }
  if (!checks.configured) return "Healthchecks — not configured";
  if (!checks.available) return "Healthchecks — unavailable";
  return "Healthchecks — check not found"; // configured + available but the slug didn't match
}

// The headline reflects BOTH the internal pipeline AND the external dead-man checks,
// so it can't green-wash a degraded external tier (Codex adversarial review). Policy,
// by precedence: failures (a red sync stage, or a DOWN backup/CD check) → loss; then
// attention (a stale/skipped sync, an OVERDUE `grace` check, or an external tier we
// can't confirm — provider unavailable / query failed) → flat; then never-run syncs;
// else green. DELIBERATELY NEUTRAL (never move the headline off green): a `paused`
// check (operator-intentional), a `new`/never-pinged check, an unconfigured tier, and
// a null check (ambiguous: an unset vs. mistyped slug looks identical here — the node
// itself shows "not configured"/"check not found").
function summarize(
  stages: PipelineStage[],
  checks?: ChecksStatus,
  checksError?: boolean,
): { text: string; className: string } {
  const failedStages = stages.filter((s) => s.status === "red").length;
  if (failedStages > 0) {
    return {
      text: `${failedStages} stage${failedStages > 1 ? "s" : ""} failed`,
      className: "text-loss",
    };
  }
  const downChecks = [checks?.backup, checks?.cd].filter(
    (c) => c?.status === "down",
  ).length;
  if (downChecks > 0) {
    return {
      text: `${downChecks} check${downChecks > 1 ? "s" : ""} down`,
      className: "text-loss",
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
    };
  }
  const overdueChecks = [checks?.backup, checks?.cd].filter(
    (c) => c?.status === "grace",
  ).length;
  if (overdueChecks > 0) {
    return {
      text: `${overdueChecks} check${overdueChecks > 1 ? "s" : ""} overdue`,
      className: "text-flat",
    };
  }
  // A CONFIGURED tier we can't read (provider down, or the /checks/ query failed) — we
  // can't confirm backups/CD, so the headline can't claim "all green". An UNconfigured
  // tier stays neutral (don't nag someone who hasn't wired Healthchecks).
  if ((checks?.configured && !checks.available) || checksError) {
    return { text: "Checks unavailable", className: "text-flat" };
  }

  // A grey stage NEVER ran (strictly worse than stale) — it must NOT fall through to
  // "All green", or a fresh box (all grey) / a not-yet-run valuation would green-wash
  // the banner directly above the "No data" rows. Green is gated on every stage green.
  const grey = stages.filter((s) => s.status === "grey").length;
  if (grey === stages.length) {
    return { text: "Awaiting first run", className: "text-muted-foreground" };
  }
  if (grey > 0) {
    return {
      text: `${grey} stage${grey > 1 ? "s" : ""} not yet run`,
      className: "text-flat",
    };
  }
  return { text: "All green today", className: "text-gain" };
}

export function PipelineFlow({
  stages,
  checks,
  checksError,
}: {
  stages: PipelineStage[];
  checks?: ChecksStatus;
  checksError?: boolean;
}) {
  const summary = summarize(stages, checks, checksError);
  const note = checkNote(checks, checksError);
  const external = [
    { key: "backup", label: "Database backup", cadence: "06:00 UTC", check: checks?.backup },
    { key: "cd", label: "Continuous deploy", cadence: "~2 min", check: checks?.cd },
  ];
  const total = stages.length + external.length;
  return (
    <section className="vitrine rounded-lg p-5 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="font-terminal text-xs uppercase tracking-[0.18em] text-gold-700">
          Nightly pipeline
        </h2>
        <span className={`text-xs font-medium ${summary.className}`}>{summary.text}</span>
      </div>
      <ol className="mt-5">
        {stages.map((stage, i) => (
          <StageNode key={stage.key} stage={stage} last={i === total - 1} />
        ))}
        {external.map((node, i) => (
          <CheckNode
            key={node.key}
            label={node.label}
            cadence={node.cadence}
            check={node.check}
            note={note}
            last={stages.length + i === total - 1}
          />
        ))}
      </ol>
    </section>
  );
}
