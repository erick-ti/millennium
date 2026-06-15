/**
 * Demo data for the public landing page. These are illustrative figures shown
 * to logged-out visitors (recruiters) — the catalog counts are the REAL seeded
 * production numbers; the movers / chart / appraisal rows are representative
 * sample data, deliberately static so the landing needs no auth and no API
 * call (and so the global 403→/login gate never fires here).
 */

export type Direction = "up" | "down" | "flat";

export const CATALOG = {
  cards: 14_388,
  printings: 43_313,
  snapshots: 51_481,
} as const;

export const TICKER: ReadonlyArray<{ name: string; dir: Direction; pct: string }> = [
  { name: "BLUE-EYES WHITE DRAGON", dir: "up", pct: "4.2%" },
  { name: "DARK MAGICIAN", dir: "down", pct: "1.8%" },
  { name: "POT OF GREED", dir: "up", pct: "11.4%" },
  { name: "ASH BLOSSOM", dir: "up", pct: "2.6%" },
  { name: "EXODIA THE FORBIDDEN ONE", dir: "up", pct: "7.1%" },
  { name: "MAXX ‘C’", dir: "down", pct: "0.9%" },
];

/** ▲ / ▼ / — glyph + sign for a delta, CVD-safe (never color alone). */
export function deltaGlyph(dir: Direction): string {
  if (dir === "up") return "▲"; // ▲
  if (dir === "down") return "▼"; // ▼
  return "—"; // —
}

export function deltaSign(dir: Direction): string {
  if (dir === "up") return "+";
  if (dir === "down") return "−"; // U+2212 true minus
  return "";
}

export function deltaClass(dir: Direction): string {
  if (dir === "up") return "text-gain";
  if (dir === "down") return "text-loss";
  return "text-flat";
}

/** Daily portfolio value (sample), with one marked partial-coverage day. */
export const CURVE: ReadonlyArray<{ date: string; value: number; complete: boolean }> = [
  { date: "2026-03-20", value: 3812, complete: true },
  { date: "2026-03-27", value: 3905, complete: true },
  { date: "2026-04-03", value: 3878, complete: true },
  { date: "2026-04-10", value: 4021, complete: true },
  { date: "2026-04-17", value: 4096, complete: true },
  { date: "2026-04-24", value: 4188, complete: true },
  { date: "2026-05-01", value: 4142, complete: true },
  { date: "2026-05-08", value: 4290, complete: true },
  { date: "2026-05-15", value: 4376, complete: true },
  { date: "2026-05-22", value: 4051, complete: false }, // TCGCSV pricing gap — partial, not a real drop
  { date: "2026-05-29", value: 4512, complete: true },
  { date: "2026-06-05", value: 4604, complete: true },
  { date: "2026-06-12", value: 4783, complete: true },
  { date: "2026-06-15", value: 4912, complete: true },
];

export type Mover = {
  name: string;
  edition: string;
  price: string;
  dir: Direction;
  pct: string | null;
  dollar: string;
};

export const MOVERS: ReadonlyArray<Mover> = [
  { name: "Blue-Eyes White Dragon", edition: "1st Ed", price: "$312.40", dir: "up", pct: "4.2%", dollar: "$12.60" },
  { name: "Ash Blossom & Joyous Spring", edition: "Unlimited", price: "$41.10", dir: "up", pct: "9.1%", dollar: "$3.43" },
  { name: "Pot of Greed", edition: "Limited", price: "$14.25", dir: "up", pct: "2.4%", dollar: "$0.33" },
  { name: "Dark Magician", edition: "1st Ed", price: "$88.00", dir: "down", pct: "1.8%", dollar: "$1.61" },
  { name: "Maxx ‘C’", edition: "Unlimited", price: "$6.40", dir: "down", pct: "3.0%", dollar: "$0.20" },
  { name: "Mystical Space Typhoon", edition: "Unlimited", price: "$0.80", dir: "flat", pct: null, dollar: "$0.40" },
];

export const SPEC: ReadonlyArray<{ k: string; v: string }> = [
  { k: "Backend", v: "Django 5.2 · DRF · drf-spectacular · Postgres 16 (modular monolith, 7 apps)" },
  { k: "Frontend", v: "Next 16 App Router · React 19 · TypeScript · Tailwind v4" },
  { k: "API contract", v: "OpenAPI → generated typed TS client, CI drift-gated (two orthogonal halves)" },
  { k: "Pipeline", v: "Dragon Shield import · confidence-scored matching · human review · append-only snapshots" },
  { k: "Hosting", v: "self-hosted Hetzner VPS · Caddy auto-TLS edge · gunicorn + Next-standalone" },
  { k: "Scheduling", v: "daily syncs as systemd timers (Persistent catch-up after downtime)" },
  { k: "Backups", v: "nightly pg_dump → Cloudflare R2 · restore-tested" },
  { k: "Quality", v: "main gated by six required CI checks · reviewed in-house + adversarial" },
  { k: "Accessibility", v: "sr-only data tables mirror every chart · aria-sort · reduced-motion throughout" },
];
