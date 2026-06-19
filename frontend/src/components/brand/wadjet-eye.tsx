import { cn } from "@/lib/utils";

/**
 * The Eye of Wadjet — Millennium's brand mark, and (in the hero) a FUNCTIONAL
 * element: the iris coin-dot pulses gold as the live-catalog status light — the
 * all-seeing eye watching the synced catalog. Drawn as a thin gold line with an
 * "appraisal-dial" tick ring around the iris and deliberately uneven stroke
 * weights (a carved-relief feel, to match Fraunces — not a stock vector icon).
 *
 * Pure SVG + CSS: with `animate`, each stroke draws itself on mount via
 * `pathLength="1"` + stroke-dashoffset (gated to prefers-reduced-motion in
 * globals.css, so a reduced-motion / unsupported visitor sees it complete).
 * Decorative by default; pass a `title` for an accessible name.
 */
export function WadjetEye({
  className,
  animate = false,
  live = false,
  title,
  irisColor,
}: {
  className?: string;
  animate?: boolean;
  /** When true, the appraisal-dial ticks slowly rotate ("scanning" the live catalog). */
  live?: boolean;
  title?: string;
  /** Override the iris coin-dot + glow colour (the /status sentinel ties it to the
   * rollup severity). Omitted on the landing → the original gold gradient, unchanged. */
  irisColor?: string;
}) {
  const irisGid = irisColor ? `wadjet-iris-${irisColor.replace(/[^a-z0-9]/gi, "")}` : "wadjet-iris";
  const irisDot = irisColor ?? "#e6c063";
  const draw = (variant?: "2" | "3") =>
    animate
      ? cn(
          "eye-stroke",
          variant === "2" && "eye-stroke--2",
          variant === "3" && "eye-stroke--3",
        )
      : undefined;

  return (
    <svg
      viewBox="0 0 220 150"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : "presentation"}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      className={cn("text-gold-700", className)}
    >
      {title ? <title>{title}</title> : null}
      <defs>
        <radialGradient id={irisGid} cx="50%" cy="50%" r="50%">
          {irisColor ? (
            <>
              <stop offset="0%" stopColor={irisColor} stopOpacity="0.95" />
              <stop offset="55%" stopColor={irisColor} stopOpacity="0.4" />
              <stop offset="100%" stopColor={irisColor} stopOpacity="0" />
            </>
          ) : (
            <>
              <stop offset="0%" stopColor="#f0d98a" stopOpacity="0.95" />
              <stop offset="55%" stopColor="#c8a24a" stopOpacity="0.35" />
              <stop offset="100%" stopColor="#c8a24a" stopOpacity="0" />
            </>
          )}
        </radialGradient>
      </defs>

      {/* status-light glow (pulses = catalog synced) */}
      <circle
        className="iris-glow"
        cx="100"
        cy="70"
        r="30"
        fill={`url(#${irisGid})`}
        stroke="none"
      />

      {/* appraisal-dial ticks around the iris — the "data ring"; rotates when live */}
      <circle
        className={live ? "eye-dial" : undefined}
        cx="100"
        cy="70"
        r="25"
        fill="none"
        stroke="currentColor"
        strokeWidth="3.5"
        strokeLinecap="butt"
        strokeDasharray="0.45 3.55"
        pathLength={64}
        opacity="0.45"
      />

      {/* brow — heaviest stroke (carved) */}
      <path d="M34 48 C80 20 152 20 198 42" pathLength={1} strokeWidth={4.5} className={draw()} />
      {/* eye almond */}
      <path
        d="M30 72 C74 42 158 42 200 66 C158 98 76 100 30 72 Z"
        pathLength={1}
        strokeWidth={3.25}
        className={draw()}
      />
      {/* descender teardrop */}
      <path d="M97 100 L88 138" pathLength={1} strokeWidth={3} className={draw("2")} />
      {/* outer curl / tail — thinnest, tapering */}
      <path
        d="M198 66 C216 74 220 112 186 124 C172 128 162 120 168 108"
        pathLength={1}
        strokeWidth={2.5}
        className={draw("2")}
      />
      {/* iris ring */}
      <circle cx="100" cy="70" r="15" fill="none" strokeWidth={3} pathLength={1} className={draw("3")} />
      {/* coin-dot pupil — the price / status dot (pulses) */}
      <circle className="iris-glow" cx="100" cy="70" r="5.5" fill={irisDot} stroke="none" />
    </svg>
  );
}
