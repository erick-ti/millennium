"use client";

import { useState, useSyncExternalStore, type ReactNode } from "react";

import { WadjetEye } from "@/components/brand/wadjet-eye";
import type { CheckRow, ChecksStatus, PipelineStage } from "@/lib/api";
import { formatDateTimeUtc } from "@/lib/format";
import { useIsDesktop } from "@/lib/use-is-desktop";

import { NightPassage } from "./night-passage";
import {
  CHECK_LABEL,
  CHECK_STATUS,
  CheckDetail,
  LOCALE,
  SEVERITY_IRIS,
  STATUS_META,
  StageDetail,
  type StatusKey,
  checkNote,
  gateCaption,
  metaFor,
  summarize,
} from "./pipeline-shared";

/**
 * The /status centerpiece. DEFAULT view = "The Sentinel": the vertical timeline + a small
 * rollup-tinted Wadjet Eye beacon + a live server-time "now" marker on the spine. A
 * desktop-only toggle switches to "Ra's Nightly Journey" (`night-passage.tsx`). The choice
 * persists in localStorage; below md, only the timeline shows (Ra is a wide desktop view).
 *
 * Status is conveyed by colour AND glyph AND word (the Vault "never colour alone" rule);
 * the weave is the indented branch (valuation + alerts hang off pricing, sub-rail coloured
 * by pricing's health); all decorative motion is motion-safe.
 */

function timeToMin(hhmm: string): number {
  const [h, m] = hhmm.split(":").map((n) => parseInt(n, 10));
  return (h || 0) * 60 + (m || 0);
}
function isoToUtcMin(iso: string): number | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.getUTCHours() * 60 + d.getUTCMinutes();
}

// ── timeline (the Sentinel view + the <md fallback) ──────────────────────────

type Ui = {
  activeKey: string | null;
  expandedKey: string | null;
  related: Set<string>;
  onToggle: (key: string) => void;
  onActivate: (key: string) => void;
  onDeactivate: () => void;
};

function isDimmed(ui: Ui, key: string): boolean {
  return ui.activeKey !== null && !ui.related.has(key);
}

function FreshHalo({ fresh }: { fresh: boolean }) {
  if (!fresh) return null;
  return (
    <span className="absolute inset-0 -m-1 rounded-full bg-gain/25 blur-[2px] motion-safe:animate-pulse motion-reduce:hidden" />
  );
}

function HeaderButton({ ui, nodeKey, children }: { ui: Ui; nodeKey: string; children: ReactNode }) {
  const expanded = ui.expandedKey === nodeKey;
  return (
    <button
      type="button"
      onClick={() => ui.onToggle(nodeKey)}
      onMouseEnter={() => ui.onActivate(nodeKey)}
      onMouseLeave={ui.onDeactivate}
      onFocus={() => ui.onActivate(nodeKey)}
      onBlur={ui.onDeactivate}
      aria-expanded={expanded}
      aria-controls={expanded ? `flow-${nodeKey}-detail` : undefined}
      className="-mx-2 block w-full rounded-sm px-2 py-1 text-left transition-colors duration-150 hover:bg-gold-900/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-gold-700 motion-reduce:transition-none aria-expanded:bg-gold-900/10"
    >
      {children}
    </button>
  );
}

function StatusLine({
  m,
  metricValue,
  metricLabel,
  lastRunAt,
  overdue,
}: {
  m: (typeof STATUS_META)[StatusKey];
  metricValue: number | null;
  metricLabel: string;
  lastRunAt: string | null;
  overdue?: boolean;
}) {
  return (
    <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <span className={`inline-flex items-center gap-1 font-medium ${m.text}`}>
        <span aria-hidden>{m.glyph}</span>
        {m.label}
      </span>
      {metricValue != null ? (
        <span className="font-terminal tabular-nums text-bone-muted">
          {metricValue.toLocaleString(LOCALE)} {metricLabel}
        </span>
      ) : null}
      {lastRunAt ? (
        <span className="font-terminal text-muted-foreground">· {formatDateTimeUtc(lastRunAt)}</span>
      ) : null}
      {overdue ? <span className="font-terminal font-medium text-flat">· overdue</span> : null}
    </span>
  );
}

function StageHeader({
  stage,
  m,
  overdue,
}: {
  stage: PipelineStage;
  m: (typeof STATUS_META)[StatusKey];
  overdue?: boolean;
}) {
  return (
    <>
      <span className="flex flex-wrap items-baseline justify-between gap-x-4">
        <span className="text-sm font-medium text-foreground">{stage.label}</span>
        <span className="font-terminal text-xs text-muted-foreground">{stage.scheduled_utc} UTC</span>
      </span>
      <StatusLine
        m={m}
        metricValue={stage.metric_value}
        metricLabel={stage.metric_label}
        lastRunAt={stage.last_run_at}
        overdue={overdue}
      />
    </>
  );
}

function SpineRail({ dotClass, fresh, last }: { dotClass: string; fresh?: boolean; last: boolean }) {
  return (
    <div className="relative flex flex-col items-center" aria-hidden>
      <span className="relative mt-1 flex h-2.5 w-2.5 shrink-0">
        <FreshHalo fresh={!!fresh} />
        <span className={`relative h-2.5 w-2.5 rounded-full ring-2 ring-vault-900 ${dotClass}`} />
      </span>
      {!last ? <span className="mt-1 w-px grow bg-border" /> : null}
    </div>
  );
}

function StageNode({ stage, ui, overdue }: { stage: PipelineStage; ui: Ui; overdue?: boolean }) {
  const m = metaFor(stage.status);
  return (
    <li
      className={`flex gap-4 pb-6 transition-opacity duration-200 motion-reduce:transition-none ${
        isDimmed(ui, stage.key) ? "opacity-35" : ""
      }`}
    >
      <SpineRail dotClass={m.dot} fresh={stage.green_today} last={false} />
      <div className="-mt-0.5 min-w-0 flex-1">
        <HeaderButton ui={ui} nodeKey={stage.key}>
          <StageHeader stage={stage} m={m} overdue={overdue} />
        </HeaderButton>
        {ui.expandedKey === stage.key ? (
          <div id={`flow-${stage.key}-detail`} className="mt-2 border-l border-border pl-3">
            <StageDetail stage={stage} />
          </div>
        ) : null}
      </div>
    </li>
  );
}

function BranchMember({
  stage,
  parentKey,
  edgeStatus,
  last,
  ui,
  overdue,
}: {
  stage: PipelineStage;
  parentKey: string;
  edgeStatus: StatusKey;
  last: boolean;
  ui: Ui;
  overdue?: boolean;
}) {
  const m = metaFor(stage.status);
  const edge = metaFor(edgeStatus);
  const gate = gateCaption(parentKey, edgeStatus);
  return (
    <li
      className={`flex gap-3 pb-5 last:pb-0 transition-opacity duration-200 motion-reduce:transition-none ${
        isDimmed(ui, stage.key) ? "opacity-35" : ""
      }`}
    >
      <div className="relative flex flex-col items-center" aria-hidden>
        <span className="relative mt-1 flex h-2 w-2 shrink-0">
          <FreshHalo fresh={stage.green_today} />
          <span className={`relative h-2 w-2 rounded-full ring-2 ring-vault-900 ${m.dot}`} />
        </span>
        {!last ? <span className={`mt-1 w-px grow ${edge.dot}`} /> : null}
      </div>
      <div className="-mt-0.5 min-w-0 flex-1">
        <HeaderButton ui={ui} nodeKey={stage.key}>
          <StageHeader stage={stage} m={m} overdue={overdue} />
          <span className={`mt-1 block font-terminal text-xs ${gate.className}`}>↳ {gate.text}</span>
        </HeaderButton>
        {ui.expandedKey === stage.key ? (
          <div id={`flow-${stage.key}-detail`} className="mt-2 border-l border-border pl-3">
            <StageDetail stage={stage} />
          </div>
        ) : null}
      </div>
    </li>
  );
}

// A compact "now" tick sized for the indented branch sub-rail.
function BranchNowMarker({ label }: { label: string }) {
  return (
    <li className="flex items-center gap-3 py-0.5" aria-label={label}>
      <div className="flex h-2 w-2 justify-center" aria-hidden>
        <span className="h-1.5 w-1.5 rotate-45 border-r border-t border-gold-700" />
      </div>
      <span className="font-terminal text-[0.58rem] uppercase tracking-[0.16em] text-gold-700">
        {label}
      </span>
    </li>
  );
}

function BranchGroup({
  parentKey,
  edgeStatus,
  members,
  ui,
  overdueFor,
  nowMin,
  nowLabel,
}: {
  parentKey: string;
  edgeStatus: StatusKey;
  members: PipelineStage[];
  ui: Ui;
  overdueFor: (s: PipelineStage) => boolean;
  nowMin: number | null;
  nowLabel: string;
}) {
  const edge = metaFor(edgeStatus);
  // place the live "now" tick BETWEEN members when the clock falls within the branch's
  // span, so it never lands below the branch (which would imply a later member ran).
  let innerIdx = -1;
  if (nowMin != null && members.length > 0) {
    const first = timeToMin(members[0].scheduled_utc);
    const last = timeToMin(members[members.length - 1].scheduled_utc);
    if (nowMin >= first && nowMin < last) {
      const nm = nowMin;
      const i = members.findIndex((m) => timeToMin(m.scheduled_utc) > nm);
      innerIdx = i === -1 ? members.length : i;
    }
  }
  const rendered: ReactNode[] = [];
  members.forEach((stage, idx) => {
    if (idx === innerIdx) rendered.push(<BranchNowMarker key="branch-now" label={nowLabel} />);
    rendered.push(
      <BranchMember
        key={stage.key}
        stage={stage}
        parentKey={parentKey}
        edgeStatus={edgeStatus}
        last={idx === members.length - 1}
        ui={ui}
        overdue={overdueFor(stage)}
      />,
    );
  });
  return (
    <li className="flex gap-4 pb-6">
      <div className="flex flex-col items-center" aria-hidden>
        <span className="w-px grow bg-border" />
      </div>
      <div className="relative min-w-0 flex-1">
        <span
          aria-hidden
          className={`absolute -left-4 top-2 h-3 w-4 rounded-bl-md border-b border-l ${edge.edge}`}
        />
        <ol>{rendered}</ol>
      </div>
    </li>
  );
}

function CheckNode({
  nodeKey,
  label,
  cadence,
  check,
  note,
  metric,
  last,
  ui,
}: {
  nodeKey: string;
  label: string;
  cadence: string;
  check: CheckRow | null | undefined;
  note: string;
  metric: string | null;
  last: boolean;
  ui: Ui;
}) {
  const m = check ? metaFor(CHECK_STATUS[check.status] ?? "grey") : null;
  return (
    <li
      className={`flex gap-4 pb-6 last:pb-0 transition-opacity duration-200 motion-reduce:transition-none ${
        isDimmed(ui, nodeKey) ? "opacity-35" : ""
      }`}
    >
      <SpineRail dotClass={m ? m.dot : "bg-muted-foreground"} last={last} />
      <div className="-mt-0.5 min-w-0 flex-1">
        <HeaderButton ui={ui} nodeKey={nodeKey}>
          <span className="flex flex-wrap items-baseline justify-between gap-x-4">
            <span
              className={`text-sm font-medium ${check ? "text-foreground" : "text-muted-foreground"}`}
            >
              {label}
            </span>
            <span className="font-terminal text-xs text-muted-foreground">{cadence}</span>
          </span>
          {check && m ? (
            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className={`inline-flex items-center gap-1 font-medium ${m.text}`}>
                <span aria-hidden>{m.glyph}</span>
                {CHECK_LABEL[check.status] ?? check.status}
              </span>
              {check.last_ping_at ? (
                <span className="font-terminal text-muted-foreground">
                  · last ping {formatDateTimeUtc(check.last_ping_at)}
                </span>
              ) : null}
              {metric ? (
                <span className="font-terminal tabular-nums text-bone-muted">· {metric}</span>
              ) : null}
            </span>
          ) : (
            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="font-terminal text-muted-foreground">{note}</span>
              {metric ? (
                <span className="font-terminal tabular-nums text-bone-muted">· {metric}</span>
              ) : null}
            </span>
          )}
        </HeaderButton>
        {ui.expandedKey === nodeKey ? (
          <div id={`flow-${nodeKey}-detail`} className="mt-2 border-l border-border pl-3">
            <CheckDetail check={check} cadence={cadence} note={note} metric={metric} />
          </div>
        ) : null}
      </div>
    </li>
  );
}

// The live server-time "now" tick on the spine — a small gold caret + label inserted at
// the chronological position between gates.
function NowMarker({ label }: { label: string }) {
  return (
    <li className="flex items-center gap-4 py-1" aria-label={label}>
      <div className="flex w-2.5 justify-center" aria-hidden>
        <span className="h-2 w-2 rotate-45 border-r border-t border-gold-700" />
      </div>
      <span className="font-terminal text-[0.62rem] uppercase tracking-[0.18em] text-gold-700">
        {label}
      </span>
    </li>
  );
}

type FlowRow =
  | { type: "stage"; stage: PipelineStage }
  | { type: "branch"; parentKey: string; edgeStatus: StatusKey; members: PipelineStage[] };

function buildRows(stages: PipelineStage[]): FlowRow[] {
  const statusByKey = new Map<string, StatusKey>(
    stages.map((s) => [s.key, (s.status as StatusKey) ?? "grey"]),
  );
  const rows: FlowRow[] = [];
  let i = 0;
  while (i < stages.length) {
    const s = stages[i];
    if (s.depends_on == null) {
      rows.push({ type: "stage", stage: s });
      i += 1;
      continue;
    }
    const parentKey = s.depends_on;
    const members: PipelineStage[] = [];
    while (i < stages.length && stages[i].depends_on === parentKey) {
      members.push(stages[i]);
      i += 1;
    }
    rows.push({
      type: "branch",
      parentKey,
      edgeStatus: statusByKey.get(parentKey) ?? "grey",
      members,
    });
  }
  return rows;
}

function relatedKeys(activeKey: string | null, stages: PipelineStage[]): Set<string> {
  if (activeKey === null) return new Set();
  const set = new Set<string>([activeKey]);
  const active = stages.find((s) => s.key === activeKey);
  if (active?.depends_on) set.add(active.depends_on);
  for (const s of stages) if (s.depends_on === activeKey) set.add(s.key);
  return set;
}

function PipelineTimeline({
  stages,
  checks,
  checksError,
  deployedSha,
  serverTime,
}: {
  stages: PipelineStage[];
  checks?: ChecksStatus;
  checksError?: boolean;
  deployedSha?: string;
  serverTime?: string;
}) {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const ui: Ui = {
    activeKey,
    expandedKey,
    related: relatedKeys(activeKey, stages),
    onToggle: (key) => setExpandedKey((prev) => (prev === key ? null : key)),
    onActivate: (key) => setActiveKey(key),
    onDeactivate: () => setActiveKey(null),
  };

  const note = checkNote(checks, checksError);
  const rows = buildRows(stages);
  const external = [
    { key: "backup", label: "Database backup", cadence: "06:00 UTC", check: checks?.backup, metric: null as string | null, time: 360 },
    {
      key: "cd",
      label: "Continuous deploy",
      cadence: "~2 min",
      check: checks?.cd,
      metric: deployedSha ? `deployed ${deployedSha}` : null,
      time: Number.POSITIVE_INFINITY, // continuous — always after the nightly chain
    },
  ];

  const nowMin = serverTime ? isoToUtcMin(serverTime) : null;
  const nowLabel = serverTime ? `now ${formatDateTimeUtc(serverTime)}` : "";
  const isOverdue = (s: PipelineStage) =>
    nowMin != null && timeToMin(s.scheduled_utc) < nowMin && s.status === "grey";

  // when the clock falls within the branch's member span, the branch renders its OWN
  // internal "now" tick — suppress the top-level one so it isn't misplaced below the branch.
  const branchRow = rows.find(
    (r): r is Extract<FlowRow, { type: "branch" }> => r.type === "branch",
  );
  const nowInBranch =
    nowMin != null &&
    branchRow != null &&
    branchRow.members.length > 0 &&
    nowMin >= timeToMin(branchRow.members[0].scheduled_utc) &&
    nowMin < timeToMin(branchRow.members[branchRow.members.length - 1].scheduled_utc);

  // ordered render items (with a representative time) so the "now" marker slots in
  // chronologically between gates.
  const items: { node: ReactNode; time: number }[] = rows.map((row) =>
    row.type === "stage"
      ? {
          node: <StageNode key={row.stage.key} stage={row.stage} ui={ui} overdue={isOverdue(row.stage)} />,
          time: timeToMin(row.stage.scheduled_utc),
        }
      : {
          node: (
            <BranchGroup
              key={`branch-${row.parentKey}`}
              parentKey={row.parentKey}
              edgeStatus={row.edgeStatus}
              members={row.members}
              ui={ui}
              overdueFor={isOverdue}
              nowMin={nowMin}
              nowLabel={nowLabel}
            />
          ),
          time: timeToMin(row.members[0].scheduled_utc),
        },
  );
  external.forEach((node, i) =>
    items.push({
      node: (
        <CheckNode
          key={node.key}
          nodeKey={node.key}
          label={node.label}
          cadence={node.cadence}
          check={node.check}
          note={note}
          metric={node.metric}
          last={i === external.length - 1}
          ui={ui}
        />
      ),
      time: node.time,
    }),
  );

  const markerIdx =
    nowMin == null || nowInBranch ? -1 : (() => {
      const idx = items.findIndex((it) => it.time > nowMin);
      return idx === -1 ? items.length : idx;
    })();

  const rendered: ReactNode[] = [];
  items.forEach((it, i) => {
    if (i === markerIdx) rendered.push(<NowMarker key="now-marker" label={nowLabel} />);
    rendered.push(it.node);
  });
  if (markerIdx === items.length) rendered.push(<NowMarker key="now-marker" label={nowLabel} />);

  return <ol>{rendered}</ol>;
}

type View = "timeline" | "passage";
const VIEW_KEY = "status-pipeline-view";

// localStorage-backed view, read via useSyncExternalStore so there is NO hydration
// mismatch (SSR/first paint = the Sentinel timeline) and no setState-in-effect.
const viewListeners = new Set<() => void>();
function readView(): View {
  if (typeof window === "undefined") return "timeline";
  try {
    return window.localStorage.getItem(VIEW_KEY) === "passage" ? "passage" : "timeline";
  } catch {
    return "timeline";
  }
}
function useStoredView(): [View, (v: View) => void] {
  const view = useSyncExternalStore(
    (cb) => {
      viewListeners.add(cb);
      // also pick up a change made in another tab (readView re-reads localStorage on notify)
      if (typeof window !== "undefined") window.addEventListener("storage", cb);
      return () => {
        viewListeners.delete(cb);
        if (typeof window !== "undefined") window.removeEventListener("storage", cb);
      };
    },
    readView,
    () => "timeline" as View,
  );
  const setView = (v: View) => {
    try {
      window.localStorage.setItem(VIEW_KEY, v);
    } catch {
      // ignore (private mode / unavailable storage)
    }
    viewListeners.forEach((l) => l());
  };
  return [view, setView];
}

function ViewToggle({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  const opt = (v: View, label: string) => (
    <button
      type="button"
      onClick={() => onChange(v)}
      aria-pressed={view === v}
      className={`rounded px-2 py-1 transition-colors duration-150 motion-reduce:transition-none ${
        view === v ? "bg-gold-900/25 text-gold-300" : "text-bone-muted hover:text-bone"
      }`}
    >
      {label}
    </button>
  );
  return (
    <div
      role="group"
      aria-label="Pipeline view"
      className="flex gap-0.5 rounded-md border border-border p-0.5 font-terminal text-[0.6rem] uppercase tracking-[0.14em]"
    >
      {opt("timeline", "Timeline")}
      {opt("passage", "Passage")}
    </div>
  );
}

export function PipelineFlow({
  stages,
  checks,
  checksError,
  deployedSha,
  serverTime,
  fetching,
}: {
  stages: PipelineStage[];
  checks?: ChecksStatus;
  checksError?: boolean;
  deployedSha?: string;
  serverTime?: string;
  fetching?: boolean;
}) {
  const summary = summarize(stages, checks, checksError);
  const isDesktop = useIsDesktop();
  const [view, choose] = useStoredView();
  const showPassage = isDesktop && view === "passage";

  return (
    <section className="vitrine rounded-lg p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h2 className="font-terminal text-xs uppercase tracking-[0.18em] text-gold-700">
          Nightly pipeline
        </h2>
        <div className="flex items-center gap-4">
          {isDesktop ? <ViewToggle view={view} onChange={choose} /> : null}
          <span className="flex items-center gap-2">
            {/* the Sentinel beacon — a small Wadjet Eye whose iris is the SAME summarize()
                rollup the headline shows, so it can never disagree (timeline view only;
                the passage's dawn sun is its own rollup signifier) */}
            {!showPassage ? (
              <WadjetEye
                irisColor={SEVERITY_IRIS[summary.severity]}
                live={fetching}
                className="w-8"
              />
            ) : null}
            <span className={`text-xs font-medium ${summary.className}`}>{summary.text}</span>
          </span>
        </div>
      </div>
      <div className="mt-5">
        {showPassage ? (
          <NightPassage
            stages={stages}
            checks={checks}
            checksError={checksError}
            deployedSha={deployedSha}
            serverTime={serverTime}
            severity={summary.severity}
          />
        ) : (
          <PipelineTimeline
            stages={stages}
            checks={checks}
            checksError={checksError}
            deployedSha={deployedSha}
            serverTime={serverTime}
          />
        )}
      </div>
    </section>
  );
}
