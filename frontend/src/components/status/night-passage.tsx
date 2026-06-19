"use client";

import { useEffect, useRef, useState } from "react";

import type { CheckRow, ChecksStatus, PipelineStage } from "@/lib/api";
import { formatDateTimeUtc } from "@/lib/format";

import {
  CHECK_LABEL,
  CHECK_STATUS,
  CheckDetail,
  type Severity,
  StageDetail,
  type StatusKey,
  checkNote,
  gateCaption,
} from "./pipeline-shared";

/**
 * "Ra's Nightly Journey" — the /status centerpiece (≥md; the vertical timeline in
 * `pipeline-flow.tsx` is the <md fallback). The nightly chain is a horizontal NIGHT
 * PASSAGE: gates sit at their real UTC hours on a gold river, valuation + alerts branch
 * up off pricing on a tributary whose colour is pricing's own health (the same-day
 * gate, drawn), and a barque at the live server-time sails toward dawn. The dawn glow is
 * bound to the rollup severity — rebirth is EARNED by the data, not by the barque
 * reaching the end.
 *
 * Honesty + a11y: every visual maps to a real field; the decorative SVG is aria-hidden
 * and the gates are real <button>s in chronological (= reading) order carrying the full
 * state in words (glyph + word + colour, never colour alone); the dependency is stated
 * in text; all motion is motion-safe with static end-states under reduced motion.
 */

// ── geometry (SVG user units) ────────────────────────────────────────────────
const VW = 900;
const VH = 280;
const RIVER_Y = 178;
const TRIB_Y = 92;
const BAND_MIN0 = 120; // 02:00
const BAND_MIN1 = 360; // 06:00
const BAND_X0 = 130;
const BAND_X1 = 680;
const DUSK_X = 52;
const DAWN_X = 848;
const CD_X = 792;

function timeToMin(hhmm: string): number {
  const [h, m] = hhmm.split(":").map((n) => parseInt(n, 10));
  return (h || 0) * 60 + (m || 0);
}
function isoToUtcMin(iso: string): number | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.getUTCHours() * 60 + d.getUTCMinutes();
}
function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}
// minutes within the night band → x on the river.
function xForMin(min: number): number {
  return BAND_X0 + ((clamp(min, BAND_MIN0, BAND_MIN1) - BAND_MIN0) / (BAND_MIN1 - BAND_MIN0)) * (BAND_X1 - BAND_X0);
}
const pctX = (x: number) => `${(x / VW) * 100}%`;
const pctY = (y: number) => `${(y / VH) * 100}%`;

// ── tone (status → night palette + glyph/word) ───────────────────────────────
type Tone = "open" | "weak" | "sealed" | "cold";
const TONE_HEX: Record<Tone, string> = {
  open: "#e6c063", // gold leaf — gate open (ran clean)
  weak: "#fbbf24", // amber — stale / skipped / late
  sealed: "#f87171", // loss-red — failed / down
  cold: "#7c7565", // dim stone — not yet run
};
const TONE_GLYPH: Record<Tone, string> = { open: "✓", weak: "!", sealed: "✕", cold: "·" };
const STAGE_WORD: Record<Tone, string> = {
  open: "Open",
  weak: "Attention",
  sealed: "Sealed",
  cold: "Awaiting",
};
function stageTone(status: string): Tone {
  if (status === "green") return "open";
  if (status === "red") return "sealed";
  if (status === "amber") return "weak";
  return "cold";
}
function checkTone(check: CheckRow | null | undefined): Tone {
  if (!check) return "cold";
  const s = CHECK_STATUS[check.status] ?? "grey";
  if (s === "green") return "open";
  if (s === "red") return "sealed";
  if (s === "amber") return "weak";
  return "cold";
}
const DAWN_OPACITY: Record<Severity, number> = {
  green: 0.85,
  attention: 0.28,
  loss: 0.12,
  neutral: 0.06,
};

// ── gate model ───────────────────────────────────────────────────────────────
type Gate = {
  key: string;
  label: string;
  short: string;
  x: number;
  y: number;
  branch: boolean;
  tone: Tone;
  word: string;
  overdue: boolean;
  metric: string | null;
  time: string;
  lastRunAt: string | null;
  dependsOn: string | null;
  ariaLabel: string;
  kind: "stage" | "check";
  stage?: PipelineStage;
  check?: CheckRow | null;
  note?: string;
};

function buildGates(
  stages: PipelineStage[],
  checks: ChecksStatus | undefined,
  checksError: boolean | undefined,
  deployedSha: string | undefined,
  serverMin: number | null,
): Gate[] {
  const note = checkNote(checks, checksError);
  const out: Gate[] = [];

  const SHORT: Record<string, string> = {
    metadata: "Metadata",
    pricing: "Pricing",
    valuation: "Valuation",
    alerts: "Alerts",
  };

  for (const s of stages) {
    const tone = stageTone(s.status);
    const branch = s.depends_on != null;
    const min = timeToMin(s.scheduled_utc);
    // a never-run stage whose scheduled hour has passed is OVERDUE — late, not merely
    // awaiting (the timeline view shows the same cue).
    const overdue = serverMin != null && tone === "cold" && min < serverMin;
    const word = overdue ? "Overdue" : STAGE_WORD[tone];
    const metric =
      s.metric_value != null ? `${s.metric_value.toLocaleString("en-US")} ${s.metric_label}` : null;
    const parts = [
      s.label,
      `${s.scheduled_utc} UTC`,
      word,
      metric ?? "",
      s.last_run_at ? `last run ${formatDateTimeUtc(s.last_run_at)}` : "",
      s.depends_on ? `gated on ${s.depends_on}` : "",
    ].filter(Boolean);
    out.push({
      key: s.key,
      label: s.label,
      short: SHORT[s.key] ?? s.label,
      x: xForMin(min),
      y: branch ? TRIB_Y : RIVER_Y,
      branch,
      tone,
      word,
      overdue,
      metric,
      time: s.scheduled_utc,
      lastRunAt: s.last_run_at,
      dependsOn: s.depends_on,
      ariaLabel: parts.join(" — "),
      kind: "stage",
      stage: s,
    });
  }

  const backup = checks?.backup;
  const cd = checks?.cd;
  const checkGate = (
    key: string,
    label: string,
    short: string,
    x: number,
    time: string,
    check: CheckRow | null | undefined,
    metric: string | null,
  ): Gate => {
    const tone = checkTone(check);
    const word = check ? (CHECK_LABEL[check.status] ?? check.status) : "Not wired";
    const parts = [
      label,
      check ? word : note,
      check?.last_ping_at ? `last ping ${formatDateTimeUtc(check.last_ping_at)}` : "",
      metric ?? "",
    ].filter(Boolean);
    return {
      key,
      label,
      short,
      x,
      y: RIVER_Y,
      branch: false,
      tone,
      word,
      // check gates are dead-men (Healthchecks grace), not scheduled-hour gates — their
      // lateness is the `grace` status, not a clock comparison.
      overdue: false,
      metric,
      time,
      lastRunAt: check?.last_ping_at ?? null,
      dependsOn: null,
      ariaLabel: parts.join(" — "),
      kind: "check",
      check,
      note,
    };
  };
  out.push(checkGate("backup", "Database backup", "Backup", xForMin(BAND_MIN1), "06:00", backup, null));
  out.push(
    checkGate(
      "cd",
      "Continuous deploy",
      "Deploy",
      CD_X,
      "~2 min",
      cd,
      deployedSha ? `deployed ${deployedSha}` : null,
    ),
  );
  return out;
}

function relatedSet(activeKey: string | null, gates: Gate[]): Set<string> {
  if (activeKey === null) return new Set();
  const set = new Set<string>([activeKey]);
  const active = gates.find((g) => g.key === activeKey);
  if (active?.dependsOn) set.add(active.dependsOn);
  for (const g of gates) if (g.dependsOn === activeKey) set.add(g.key);
  return set;
}

// which segment of the night the barque sits in (for the debounced sr announcement).
function barqueRegion(min: number | null, severity: Severity): string {
  if (min == null) return "Server time unknown.";
  if (min < BAND_MIN0) return "Before tonight's run.";
  if (min > BAND_MIN1) {
    // past dawn — but "complete" is EARNED by the rollup, not the clock (else this would
    // claim success ~18h/day even after a failed run, contradicting the dawn glow).
    switch (severity) {
      case "loss":
        return "Past dawn — tonight's run did not finish clean.";
      case "attention":
        return "Past dawn — tonight's run needs attention.";
      case "neutral":
        return "Past dawn — tonight's run hasn't run.";
      default:
        return "Tonight's run is complete — past dawn.";
    }
  }
  if (min === BAND_MIN0) return "At metadata.";
  if (min === BAND_MIN1) return "At backup.";
  if (min < 180) return "Between metadata and pricing.";
  if (min < 240) return "Between pricing and valuation.";
  if (min < 300) return "Between valuation and alerts.";
  return "Between alerts and backup.";
}

// An ancient-Egyptian carved sun disk (Ra) — a thin gold disk + radiating rays, matching
// the Wadjet Eye's carved-relief register. The DAWN sun GLOWS by rollup severity (rebirth
// earned by the night's work); the DUSK sun is a quiet, dim setting carving. Replaces the
// big radial wash so the gates stay readable.
function SunCarving({ cx, cy, glow = 0, dim = false }: { cx: number; cy: number; glow?: number; dim?: boolean }) {
  const r = 13;
  const stroke = dim ? "#9d814d" : "#e6c063";
  const op = dim ? 0.5 : 0.85;
  const rays = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330];
  return (
    <g aria-hidden>
      {glow > 0 ? <circle cx={cx} cy={cy} r={r * 2.6} fill="url(#ra-dawn)" opacity={glow} /> : null}
      {rays.map((a) => {
        const rad = (a * Math.PI) / 180;
        return (
          <line
            key={a}
            x1={cx + Math.cos(rad) * (r + 3)}
            y1={cy + Math.sin(rad) * (r + 3)}
            x2={cx + Math.cos(rad) * (r + 8)}
            y2={cy + Math.sin(rad) * (r + 8)}
            stroke={stroke}
            strokeWidth={1}
            opacity={op * 0.75}
          />
        );
      })}
      <circle cx={cx} cy={cy} r={r} fill={stroke} fillOpacity={0.05} stroke={stroke} strokeWidth={1.25} opacity={op} />
      <circle cx={cx} cy={cy} r={2.6} fill={stroke} opacity={op} />
    </g>
  );
}

export function NightPassage({
  stages,
  checks,
  checksError,
  deployedSha,
  serverTime,
  severity,
}: {
  stages: PipelineStage[];
  checks?: ChecksStatus;
  checksError?: boolean;
  deployedSha?: string;
  serverTime?: string;
  severity: Severity;
}) {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const serverMin = serverTime ? isoToUtcMin(serverTime) : null;
  const gates = buildGates(stages, checks, checksError, deployedSha, serverMin);
  const related = relatedSet(activeKey, gates);
  const dimmed = (key: string) => activeKey !== null && !related.has(key);

  const statusByKey = new Map<string, StatusKey>(
    stages.map((s) => [s.key, (s.status as StatusKey) ?? "grey"]),
  );
  const pricingTone = stageTone(statusByKey.get("pricing") ?? "grey");
  const tribHex = TONE_HEX[pricingTone];

  // barque position from the live server time (clamped to the band; rests at the anchors).
  const barqueX =
    serverMin == null ? null : serverMin < BAND_MIN0 ? DUSK_X : serverMin > BAND_MIN1 ? DAWN_X : xForMin(serverMin);

  // debounced sr announcement — only when the barque crosses into a new segment.
  const region = barqueRegion(serverMin, severity);
  const [announce, setAnnounce] = useState("");
  const lastRegion = useRef(region);
  useEffect(() => {
    if (region !== lastRegion.current) {
      lastRegion.current = region;
      setAnnounce(region);
    }
  }, [region]);

  const expanded = gates.find((g) => g.key === expandedKey) ?? null;
  // the upstream gate's real status drives the dependency caption (the same-day gate).
  const edgeStatus: StatusKey =
    (expanded?.dependsOn ? statusByKey.get(expanded.dependsOn) : undefined) ?? "grey";

  return (
    <div>
      <div className="relative">
        {/* purely decorative scene — the accessible controls are the overlay buttons */}
        <svg viewBox={`0 0 ${VW} ${VH}`} className="block w-full" aria-hidden>
          <defs>
            <linearGradient id="ra-river" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="#9d814d" stopOpacity="0" />
              <stop offset="10%" stopColor="#9d814d" stopOpacity="0.55" />
              <stop offset="90%" stopColor="#c8a24a" stopOpacity="0.55" />
              <stop offset="100%" stopColor="#e6c063" stopOpacity="0" />
            </linearGradient>
            <radialGradient id="ra-dawn" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#f0d98a" stopOpacity="0.9" />
              <stop offset="55%" stopColor="#e6c063" stopOpacity="0.32" />
              <stop offset="100%" stopColor="#e6c063" stopOpacity="0" />
            </radialGradient>
          </defs>

          {/* the river rail */}
          <line x1={32} y1={RIVER_Y} x2={VW - 32} y2={RIVER_Y} stroke="url(#ra-river)" strokeWidth={1.5} />

          {/* carved sun disks: a quiet setting sun at dusk, the rising sun at dawn whose
              glow is EARNED by the rollup severity (full when all-green, cold when broken) */}
          <SunCarving cx={62} cy={104} dim />
          <SunCarving cx={VW - 62} cy={104} glow={DAWN_OPACITY[severity]} />
          <text x={62} y={148} fontSize={10} textAnchor="middle" className="font-terminal" letterSpacing="2" fill="#7c7565">
            DUSK
          </text>
          <text
            x={VW - 62}
            y={148}
            fontSize={10}
            textAnchor="middle"
            className="font-terminal"
            letterSpacing="2"
            fill={severity === "green" ? "#e6c063" : "#7c7565"}
          >
            DAWN
          </text>

          {/* dependency tributary: pricing → {valuation, alerts}, coloured by pricing's health */}
          {(() => {
            const px = xForMin(timeToMin("03:00"));
            const vx = xForMin(timeToMin("04:00"));
            const ax = xForMin(timeToMin("05:00"));
            const tribDim = activeKey !== null && !["pricing", "valuation", "alerts"].some((k) => related.has(k));
            return (
              <g
                className="transition-opacity duration-200 motion-reduce:transition-none"
                opacity={tribDim ? 0.25 : 1}
              >
                {/* elbow up off pricing, then along the tributary to alerts */}
                <path
                  d={`M ${px} ${RIVER_Y} L ${px} ${TRIB_Y + 18} Q ${px} ${TRIB_Y} ${px + 18} ${TRIB_Y} L ${ax} ${TRIB_Y}`}
                  fill="none"
                  stroke={tribHex}
                  strokeWidth={1.25}
                  strokeDasharray={pricingTone === "cold" ? "3 4" : undefined}
                  opacity={pricingTone === "cold" ? 0.5 : 0.85}
                />
                {/* droplines marking the TRUE clock-x of valuation/alerts on the river */}
                {[
                  { x: vx, t: "04:00" },
                  { x: ax, t: "05:00" },
                ].map((d) => (
                  <g key={d.t}>
                    <line x1={d.x} y1={TRIB_Y + 8} x2={d.x} y2={RIVER_Y - 8} stroke="#7c7565" strokeWidth={0.75} strokeDasharray="2 4" opacity={0.4} />
                    <line x1={d.x} y1={RIVER_Y - 4} x2={d.x} y2={RIVER_Y + 4} stroke="#7c7565" strokeWidth={1} opacity={0.6} />
                    <text x={d.x} y={RIVER_Y + 18} fontSize={8.5} textAnchor="middle" className="font-terminal" fill="#7c7565">
                      {d.t}
                    </text>
                  </g>
                ))}
              </g>
            );
          })()}

          {/* gates */}
          {gates.map((g) => {
            const hex = TONE_HEX[g.tone];
            const hollow = g.tone === "cold";
            const labelAbove = g.branch;
            const nameY = labelAbove ? g.y - 46 : g.y + 24;
            const statusY = labelAbove ? g.y - 32 : g.y + 39;
            const metaY = labelAbove ? g.y - 18 : g.y + 53;
            return (
              <g
                key={g.key}
                className="transition-opacity duration-200 motion-reduce:transition-none"
                opacity={dimmed(g.key) ? 0.28 : 1}
              >
                {/* fresh-today breath */}
                {g.stage?.green_today ? (
                  <circle cx={g.x} cy={g.y} r={11} fill={hex} opacity={0.22} className="motion-safe:animate-pulse motion-reduce:hidden" />
                ) : null}
                <circle cx={g.x} cy={g.y} r={7} fill={hollow ? "none" : hex} stroke={hex} strokeWidth={1.75} opacity={hollow ? 0.65 : 1} />
                {/* hover passage-trace: where a STAGE gate actually ran vs its scheduled x
                    (check gates sit at a fixed pixel, not a clock x — exclude them) */}
                {activeKey === g.key && g.lastRunAt && g.kind === "stage" && !g.branch
                  ? (() => {
                      const lm = isoToUtcMin(g.lastRunAt);
                      if (lm == null) return null;
                      const lx = xForMin(lm);
                      if (Math.abs(lx - g.x) < 1.5) return null;
                      return (
                        <g>
                          <line x1={g.x} y1={g.y} x2={lx} y2={g.y} stroke={hex} strokeWidth={1} opacity={0.7} />
                          <path d={`M ${lx} ${g.y - 5} L ${lx + 5} ${g.y} L ${lx} ${g.y + 5} Z`} fill={hex} opacity={0.8} />
                        </g>
                      );
                    })()
                  : null}
                <text x={g.x} y={nameY} fontSize={11} textAnchor="middle" className="font-terminal" fill="#f5f1e6">
                  {g.short}
                </text>
                <text
                  x={g.x}
                  y={statusY}
                  fontSize={10.5}
                  textAnchor="middle"
                  className="font-terminal"
                  fill={g.overdue ? TONE_HEX.weak : hex}
                >
                  {g.overdue ? "!" : TONE_GLYPH[g.tone]} {g.word}
                </text>
                <text x={g.x} y={metaY} fontSize={9} textAnchor="middle" className="font-terminal" fill="#b8b09e">
                  {g.time}
                  {g.metric ? ` · ${g.metric}` : ""}
                </text>
              </g>
            );
          })}
        </svg>

        {/* interactive + accessible overlay: real buttons in chronological order */}
        <div className="absolute inset-0">
          {gates.map((g) => (
            <button
              key={g.key}
              type="button"
              aria-label={g.ariaLabel}
              aria-expanded={expandedKey === g.key}
              aria-controls={expandedKey === g.key ? `passage-${g.key}-detail` : undefined}
              onClick={() => setExpandedKey((p) => (p === g.key ? null : g.key))}
              onMouseEnter={() => setActiveKey(g.key)}
              onMouseLeave={() => setActiveKey(null)}
              onFocus={() => setActiveKey(g.key)}
              onBlur={() => setActiveKey(null)}
              className="absolute h-[24%] w-[12%] -translate-x-1/2 -translate-y-1/2 rounded-md focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-gold-700"
              style={{ left: pctX(g.x), top: pctY(g.y + (g.branch ? -18 : 18)) }}
            />
          ))}

          {/* the barque — the only self-moving element; glides on a real server_time change */}
          {barqueX != null ? (
            <div
              className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 transition-all duration-700 ease-out motion-reduce:transition-none"
              style={{ left: pctX(barqueX), top: pctY(RIVER_Y) }}
            >
              <span className="block text-gold-500" aria-hidden>
                {/* Ra's solar barque — a crescent reed-hull (upturned prow + stern)
                    carrying the sun disk amidships */}
                <svg viewBox="0 0 28 18" className="h-5 w-7">
                  <path d="M2 9 C5 16 23 16 26 9 C20 12 8 12 2 9 Z" fill="currentColor" />
                  <line x1="14" y1="10.5" x2="14" y2="5.5" stroke="currentColor" strokeWidth="1" />
                  <circle cx="14" cy="3.8" r="2.6" fill="currentColor" />
                </svg>
              </span>
            </div>
          ) : null}
        </div>
      </div>

      {/* the barque caption + a debounced sr announcement of segment crossings */}
      {barqueX != null ? (
        <p className="mt-2 text-center font-terminal text-[0.66rem] text-gold-700">
          now {serverTime ? formatDateTimeUtc(serverTime) : "—"} · {region.toLowerCase()}
        </p>
      ) : null}
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>

      {/* the unsealed gate's detail */}
      {expanded ? (
        <div id={`passage-${expanded.key}-detail`} className="mt-4 border-t border-border pt-4">
          <p className="mb-2 font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
            {expanded.label}
          </p>
          {expanded.kind === "stage" && expanded.stage ? (
            <>
              <StageDetail stage={expanded.stage} />
              {expanded.dependsOn ? (
                <p
                  className={`mt-2 font-terminal text-xs ${gateCaption(expanded.dependsOn, edgeStatus).className}`}
                >
                  ↳ {gateCaption(expanded.dependsOn, edgeStatus).text}
                </p>
              ) : null}
            </>
          ) : (
            <CheckDetail
              check={expanded.check}
              cadence={expanded.time}
              note={expanded.note ?? ""}
              metric={expanded.metric}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}
