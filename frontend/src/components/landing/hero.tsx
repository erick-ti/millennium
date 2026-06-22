import { CountUp } from "@/components/landing/count-up";
import { CATALOG } from "@/components/landing/data";
import { DemoCta } from "@/components/landing/demo-cta";
import { EyeStatus } from "@/components/landing/eye-status";
import { FoilCard } from "@/components/landing/foil-card";
import { Ticker } from "@/components/landing/ticker";

/**
 * Section 00 — the hero. Leads with the signature foil/appraisal card above the
 * fold ("lead with your strongest punch"): brand + the prominent Eye-as-live-
 * monitor + a count-up stat strip on the left, the interactive card on the
 * right, interlocked across the connective gold hairline. All motion is opt-in
 * over a complete static state.
 */
export function Hero() {
  return (
    <header className="relative overflow-hidden bg-vault-950 text-bone">
      <Ticker />

      <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-6 pb-20 pt-14 lg:grid-cols-[1.05fr_0.95fr] lg:gap-0 lg:pb-24 lg:pt-16">
        {/* LEFT — brand + the live-catalog monitor */}
        <div className="relative z-10 lg:pr-14">
          <p className="font-accent text-sm uppercase tracking-[0.24em] text-gold-900">
            ‹ A private collection, appraised like a portfolio ›
          </p>

          <h1 className="gold-leaf gold-leaf-sweep mt-5 font-display text-[clamp(2.5rem,5.5vw,5.25rem)] font-semibold leading-[0.86] tracking-[-0.02em]">
            MILLENNIUM
          </h1>

          <p className="mt-6 max-w-md font-body text-xl leading-snug text-bone">
            A Yu-Gi-Oh collection, appraised like an investment portfolio.
          </p>
          <p className="mt-3 max-w-md font-body text-[0.95rem] leading-relaxed text-bone-muted">
            Per-lot cost basis. Daily card pricing. Coverage-aware valuation.
            Built, deployed, and operated by one engineer.
          </p>

          <EyeStatus className="mt-8" />
          <p className="mt-4 font-terminal text-sm text-gold-500 nums-terminal">
            <CountUp value={CATALOG.cards} /> cards ·{" "}
            <CountUp value={CATALOG.printings} /> printings ·{" "}
            <CountUp value={CATALOG.snapshots} /> snapshots
            <span className="text-bone-muted"> · as of Jun 2026</span>
          </p>

          <DemoCta
            className="mt-8 border border-gold-700/55 text-gold-300 hover:border-gold-500 hover:bg-gold-700/10"
            caption="Live, read-only demo — no sign-in needed."
          />
        </div>

        {/* RIGHT — the signature foil/appraisal card, above the fold */}
        <div className="relative z-10 flex flex-col items-center gap-4 lg:border-l lg:border-gold-900/20 lg:pl-14">
          <FoilCard />
          <p className="max-w-[20rem] text-center font-terminal text-[0.68rem] leading-relaxed text-gold-900">
            The rarest card, rendered as a financial instrument — the only foil
            on the page. Move your cursor across it.
          </p>
        </div>
      </div>

      {/* the seam of the vault door */}
      <hr className="gold-rule gold-seam mx-auto max-w-6xl" />
    </header>
  );
}
