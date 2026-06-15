"use client";

import { useEffect, useRef } from "react";

const SPARK = [168, 182, 176, 201, 224, 215, 238, 270, 258, 296, 312];

function Sparkline({ data }: { data: number[] }) {
  const w = 120;
  const h = 30;
  const pad = 2;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const points = data
    .map((v, i) => {
      const x = pad + (i / (data.length - 1)) * (w - 2 * pad);
      const y = h - pad - ((v - min) / span) * (h - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-7 w-full" preserveAspectRatio="none" aria-hidden>
      <polyline
        points={points}
        fill="none"
        stroke="#c8a24a"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * The signature element (Plate 02): the ONE foil surface on the page, fused
 * with the appraisal readout so a single object proves the whole thesis — a
 * Yu-Gi-Oh card rendered as a financial instrument. Pointer-tracked tilt + a
 * gold-biased holographic sheen over the art window only; the data panel stays
 * matte and crisp. No animation library — a tiny rAF-throttled pointer loop
 * writes CSS custom props. Touch / reduced-motion: no listener, a gentle static
 * sheen instead (the card still reads as foil without demanding interaction).
 */
export function FoilCard() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const card = ref.current;
    if (!card) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const coarse = window.matchMedia("(pointer: coarse)").matches;
    if (reduce || coarse) {
      card.dataset.static = "true";
      return;
    }

    let raf = 0;
    const set = (k: string, v: string) => card.style.setProperty(k, v);

    const onMove = (event: PointerEvent) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const r = card.getBoundingClientRect();
        const px = Math.min(1, Math.max(0, (event.clientX - r.left) / r.width));
        const py = Math.min(1, Math.max(0, (event.clientY - r.top) / r.height));
        set("--px", `${(px * 100).toFixed(1)}%`);
        set("--py", `${(py * 100).toFixed(1)}%`);
        set("--bg-x", `${(20 + px * 60).toFixed(1)}%`);
        set("--bg-y", `${(20 + py * 60).toFixed(1)}%`);
        set("--rx", `${((px - 0.5) * 15).toFixed(2)}deg`);
        set("--ry", `${((0.5 - py) * 15).toFixed(2)}deg`);
        set("--foil-opacity", "0.85");
        card.dataset.interacting = "true";
      });
    };

    const onLeave = () => {
      cancelAnimationFrame(raf);
      card.dataset.interacting = "false";
      set("--rx", "0deg");
      set("--ry", "0deg");
      set("--px", "50%");
      set("--py", "50%");
      set("--bg-x", "50%");
      set("--bg-y", "50%");
      set("--foil-opacity", "0");
    };

    card.addEventListener("pointermove", onMove);
    card.addEventListener("pointerleave", onLeave);
    return () => {
      card.removeEventListener("pointermove", onMove);
      card.removeEventListener("pointerleave", onLeave);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div
      ref={ref}
      className="foil-card vitrine mx-auto w-full max-w-[20rem] overflow-hidden rounded-xl"
    >
      {/* header */}
      <div className="flex items-center justify-between px-4 pt-4 font-terminal text-[0.65rem] uppercase tracking-[0.16em]">
        <span className="rounded-sm bg-gold-700/15 px-2 py-0.5 text-gold-300">Secret Rare</span>
        <span className="text-bone-muted">LOB-001</span>
      </div>

      {/* art window — the only place the foil plays */}
      <div className="foil-art mx-4 mt-3 flex h-44 items-center justify-center rounded-md border border-gold-900/30 bg-[radial-gradient(circle_at_50%_36%,oklch(0.23_0.02_70),oklch(0.13_0.006_70))]">
        <svg
          viewBox="0 0 100 100"
          className="w-24 text-gold-700"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M50 8 L86 38 L50 92 L14 38 Z" />
          <path d="M14 38 L86 38" />
          <path d="M50 8 L38 38 L50 92" />
          <path d="M50 8 L62 38 L50 92" />
          <path d="M38 38 L50 8 M62 38 L50 8" opacity={0.5} />
        </svg>
        <div className="foil-shine" aria-hidden />
        <div className="foil-glare" aria-hidden />
      </div>

      {/* identity */}
      <div className="px-4 pt-3.5">
        <p className="font-display text-xl font-semibold leading-tight text-bone">
          Blue-Eyes White Dragon
        </p>
        <p className="mt-0.5 font-terminal text-[0.68rem] uppercase tracking-[0.12em] text-bone-muted">
          Legend of Blue Eyes · 1st Edition
        </p>
      </div>

      {/* appraisal readout — matte, crisp, the card AS a financial instrument */}
      <dl className="mt-3.5 space-y-2 border-t border-gold-900/20 px-4 py-4 font-terminal text-sm nums-terminal">
        <div className="flex items-baseline justify-between">
          <dt className="text-xs uppercase tracking-wide text-bone-muted">Market</dt>
          <dd className="text-bone">
            $312.40 <span className="text-gain">▲ +4.2%</span>
          </dd>
        </div>
        <div className="flex items-baseline justify-between">
          <dt className="text-xs uppercase tracking-wide text-bone-muted">Cost basis</dt>
          <dd className="text-bone-muted">$180.00</dd>
        </div>
        <div className="flex items-baseline justify-between">
          <dt className="text-xs uppercase tracking-wide text-bone-muted">Unrealized</dt>
          <dd className="text-gain">+$132.40</dd>
        </div>
        <div className="flex items-center justify-between gap-4 pt-1">
          <dt className="shrink-0 text-xs uppercase tracking-wide text-bone-muted">90-day</dt>
          <dd className="w-28">
            <span className="sr-only">
              {`90-day trend: ${
                SPARK[SPARK.length - 1] >= SPARK[0] ? "rising" : "falling"
              }, $${SPARK[0]} to $${SPARK[SPARK.length - 1]}.`}
            </span>
            <Sparkline data={SPARK} />
          </dd>
        </div>
        <div className="flex items-baseline justify-between pt-1">
          <dt className="text-xs uppercase tracking-wide text-bone-muted">Coverage</dt>
          <dd className="text-gold-700">✓ complete · EXACT match</dd>
        </div>
      </dl>
    </div>
  );
}
