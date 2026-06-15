import type {
  ImportBatchStatusEnum,
  ImportRowStatusEnum,
  MatchConfidenceEnum,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// No shadcn Badge primitive exists; this is the project's ad-hoc chip (the
// card-detail "multi-variant" span) promoted to a tiny shared component, since
// slice 6 needs several. Tones map to the established palette (emerald/red/amber
// + the neutral muted chip + the destructive token for errors).
type Tone = "neutral" | "amber" | "green" | "red" | "blue";

// Vault semantic tones (the app runs dark): gain/flat/loss + a gold "in-flight"
// tone and the neutral muted chip.
const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  amber: "bg-flat/12 text-flat",
  green: "bg-gain/12 text-gain",
  red: "bg-destructive/12 text-destructive",
  blue: "bg-gold-700/12 text-gold-300",
};

export function Pill({
  tone = "neutral",
  children,
}: {
  tone?: Tone;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium",
        TONE_CLASSES[tone],
      )}
    >
      {children}
    </span>
  );
}

const BATCH_STATUS: Record<ImportBatchStatusEnum, { label: string; tone: Tone }> = {
  pending: { label: "Pending", tone: "neutral" },
  processing: { label: "Processing", tone: "blue" },
  review: { label: "Review", tone: "amber" },
  completed: { label: "Completed", tone: "green" },
  failed: { label: "Failed", tone: "red" },
};

const ROW_STATUS: Record<ImportRowStatusEnum, { label: string; tone: Tone }> = {
  pending: { label: "Pending", tone: "amber" },
  materialized: { label: "Materialized", tone: "green" },
  skipped: { label: "Skipped", tone: "neutral" },
  error: { label: "Error", tone: "red" },
};

const CONFIDENCE: Record<MatchConfidenceEnum, { label: string; tone: Tone }> = {
  exact: { label: "Exact", tone: "green" },
  high: { label: "High", tone: "green" },
  medium: { label: "Medium", tone: "amber" },
  low: { label: "Low", tone: "amber" },
  unmatched: { label: "Unmatched", tone: "red" },
};

export function BatchStatusPill({ status }: { status: ImportBatchStatusEnum }) {
  const { label, tone } = BATCH_STATUS[status];
  return <Pill tone={tone}>{label}</Pill>;
}

export function RowStatusPill({ status }: { status: ImportRowStatusEnum }) {
  const { label, tone } = ROW_STATUS[status];
  return <Pill tone={tone}>{label}</Pill>;
}

export function ConfidencePill({
  confidence,
}: {
  confidence: MatchConfidenceEnum;
}) {
  const { label, tone } = CONFIDENCE[confidence];
  return <Pill tone={tone}>{label}</Pill>;
}
