"use client";

import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * A tiny rAF count-up — the "tumblers locking" beat on the catalog figures.
 * No animation library: server-renders (and reduced-motion renders) the final
 * value, so a no-JS / reduced-motion visitor sees the real number immediately.
 * When motion is allowed it counts 0 → value once, the first time it scrolls
 * into view. Formats with thousands separators via Intl.
 */
export function CountUp({
  value,
  durationMs = 1600,
  className,
}: {
  value: number;
  durationMs?: number;
  className?: string;
}) {
  const [display, setDisplay] = useState(value);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Initial state is already `value`, so reduced-motion = leave it (no
    // synchronous setState in an effect — that's a cascading-render smell).
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let raf = 0;
    let done = false;
    const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

    const run = () => {
      if (done) return;
      done = true;
      setDisplay(0);
      const start = performance.now();
      const tick = (now: number) => {
        const t = Math.min(1, (now - start) / durationMs);
        setDisplay(Math.round(easeOutCubic(t) * value));
        if (t < 1) raf = requestAnimationFrame(tick);
        else setDisplay(value);
      };
      raf = requestAnimationFrame(tick);
    };

    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            run();
            io.disconnect();
          }
        }
      },
      { threshold: 0.4 },
    );
    io.observe(el);

    return () => {
      io.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [value, durationMs]);

  return (
    <span ref={ref} className={cn("nums-terminal tabular-nums", className)}>
      {display.toLocaleString("en-US")}
    </span>
  );
}
