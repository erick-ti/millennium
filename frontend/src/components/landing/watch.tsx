import { deltaClass, deltaGlyph, deltaSign, MOVERS } from "@/components/landing/data";
import { cn } from "@/lib/utils";

/**
 * 03 — The Watch. The trading-desk beat: a dense, right-aligned monospace
 * movers table (negative space does the work, not flashing colors) beside a
 * compact alerts panel. Encodes real engineering taste — a sub-$1 base shows
 * the dollar move but withholds the percent (no fake volatility), edition-aware
 * rows, append-only events.
 */
export function Watch() {
  return (
    <section className="relative z-10 border-t border-gold-900/15 bg-vault-950 py-24 text-bone">
      <div className="mx-auto max-w-6xl px-6">
        <p className="font-terminal text-xs uppercase tracking-[0.3em] text-gold-900">
          02 — The Watch
        </p>
        <h2 className="mt-3 font-display text-3xl font-semibold leading-tight text-bone sm:text-4xl">
          What moved.
        </h2>
        <p className="mt-5 max-w-2xl font-body leading-relaxed text-bone-muted">
          Owned (printing, edition) pairs ranked by price change over a window.
          A pair missing a usable price at either anchor is excluded — a gap is
          never a fake +100%.
        </p>

        <div className="mt-9 grid gap-10 lg:grid-cols-[1.7fr_1fr]">
          {/* movers tape */}
          <div className="overflow-hidden rounded-lg border border-gold-900/25">
            <table className="w-full font-terminal text-sm nums-terminal">
              <thead>
                <tr className="border-b border-gold-900/25 text-[0.68rem] uppercase tracking-[0.12em] text-gold-900">
                  <th scope="col" className="py-2.5 pl-4 text-left font-medium">Card</th>
                  <th scope="col" className="px-2 text-left font-medium">Ed.</th>
                  <th scope="col" className="px-2 text-right font-medium">Price</th>
                  <th scope="col" className="px-2 text-right font-medium">Δ%</th>
                  <th scope="col" className="py-2.5 pr-4 text-right font-medium">Δ$</th>
                </tr>
              </thead>
              <tbody>
                {MOVERS.map((m) => (
                  <tr
                    key={`${m.name}-${m.edition}`}
                    className="border-b border-gold-900/10 last:border-0 transition-colors hover:bg-gold-700/[0.04]"
                  >
                    <td className="py-2.5 pl-4 text-bone">{m.name}</td>
                    <td className="px-2 text-bone-muted">{m.edition}</td>
                    <td className="px-2 text-right text-bone">{m.price}</td>
                    <td className={cn("px-2 text-right", deltaClass(m.dir))}>
                      {m.pct ? (
                        <>
                          {deltaGlyph(m.dir)} {deltaSign(m.dir)}
                          {m.pct}
                        </>
                      ) : (
                        <span className="text-bone-muted">—</span>
                      )}
                    </td>
                    <td className={cn("py-2.5 pr-4 text-right", deltaClass(m.dir))}>
                      {deltaGlyph(m.dir)} {deltaSign(m.dir)}
                      {m.dollar}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="border-t border-gold-900/15 px-4 py-2.5 font-terminal text-[0.68rem] text-bone-muted">
              Sub-$1.00 base → percent withheld, dollar move still shown. No fake
              volatility off a five-cent card.
            </p>
          </div>

          {/* alerts */}
          <div className="vitrine flex flex-col gap-4 rounded-lg p-6">
            <p className="font-terminal text-[0.68rem] uppercase tracking-[0.14em] text-gold-900">
              Alerts
            </p>
            <p className="font-body text-sm leading-relaxed text-bone-muted">
              Set a watch on any card — a percent move over a window, in a
              direction, fires once a day.
            </p>
            <div className="rounded-md border border-gold-900/20 px-3 py-2.5 font-terminal text-[0.72rem] text-bone-muted">
              <span className="text-gold-700">RULE</span> · move ≥ 10% · 30d ·
              ANY · <span className="text-gain">ACTIVE</span>
            </div>
            <dl className="space-y-2 font-terminal text-[0.72rem] nums-terminal">
              <div className="flex items-baseline justify-between">
                <dt className="text-bone">Ash Blossom</dt>
                <dd className="text-gain">▲ +9.1% · 30d</dd>
              </div>
              <div className="flex items-baseline justify-between">
                <dt className="text-bone">Blue-Eyes White Dragon</dt>
                <dd className="text-gain">▲ +4.2% · 7d</dd>
              </div>
            </dl>
            <p className="mt-auto font-terminal text-[0.66rem] leading-relaxed text-bone-muted">
              Evaluated nightly at 05:00 UTC, after pricing and valuation.
              Append-only event feed.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
