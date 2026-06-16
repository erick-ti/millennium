"use client";

import { useState } from "react";

import { deltaClass, deltaGlyph, deltaSign, TICKER } from "@/components/landing/data";
import { cn } from "@/lib/utils";

/**
 * The top-edge mover tape — a pure-CSS marquee (the track holds two copies and
 * translates −50% for a seamless loop). The scrolling content is aria-hidden (a
 * marquee is hostile to screen readers; the real, sortable movers table is the
 * Watch plate). Frozen entirely under prefers-reduced-motion.
 *
 * WCAG 2.2.2 (Pause, Stop, Hide): auto-starting motion that lasts >5s needs a
 * mechanism to pause it for keyboard AND touch users — `:hover` alone doesn't
 * cover them — so a small focusable pause/play control toggles `data-paused`,
 * which the CSS maps to `animation-play-state`.
 */
export function Ticker() {
  const [paused, setPaused] = useState(false);

  const cell = (item: (typeof TICKER)[number], key: string) => (
    <span key={key} className="flex items-center gap-2 px-5 text-[0.72rem] tracking-wide">
      <span className="text-bone-muted">{item.name}</span>
      <span className={cn("nums-terminal", deltaClass(item.dir))}>
        {deltaGlyph(item.dir)} {deltaSign(item.dir)}
        {item.pct}
      </span>
      <span className="text-gold-900/40">·</span>
    </span>
  );

  return (
    <div className="relative border-y border-gold-900/20 bg-vault-950/80 backdrop-blur">
      <div
        aria-hidden
        data-paused={paused}
        className="ticker-rail w-full overflow-hidden py-2.5 pr-10 font-terminal text-bone-muted [mask-image:linear-gradient(90deg,transparent,#000_6%,#000_94%,transparent)]"
      >
        <div className="ticker-track flex w-max items-center">
          {TICKER.map((item, i) => cell(item, `a-${i}`))}
          {TICKER.map((item, i) => cell(item, `b-${i}`))}
        </div>
      </div>

      <button
        type="button"
        onClick={() => setPaused((p) => !p)}
        aria-label={paused ? "Play the price ticker" : "Pause the price ticker"}
        className="absolute right-2 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-sm border border-gold-900/30 bg-vault-950/90 text-gold-700 outline-none transition-colors hover:border-gold-500 hover:text-gold-300 focus-visible:ring-2 focus-visible:ring-gold-500/60"
      >
        {paused ? (
          <svg viewBox="0 0 12 12" className="size-3" fill="currentColor" aria-hidden>
            <path d="M3 2l7 4-7 4z" />
          </svg>
        ) : (
          <svg viewBox="0 0 12 12" className="size-3" fill="currentColor" aria-hidden>
            <rect x="3" y="2.5" width="2" height="7" />
            <rect x="7" y="2.5" width="2" height="7" />
          </svg>
        )}
      </button>
    </div>
  );
}
