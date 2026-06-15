import { WadjetEye } from "@/components/brand/wadjet-eye";
import { SPEC } from "@/components/landing/data";

/**
 * 04 — The Engine Room. The tonal pivot and recruiter payload: ornament drops
 * to near-zero, the discipline IS the visual. A sober two-column spec manifest
 * (this is where the import/reconciliation lineage lives, folded in), then the
 * Eye reappears small and static to close the frame it opened. Deliberately
 * breaks the page's rhythm — denser, quieter, terminal.
 */
export function EngineRoom() {
  return (
    <section className="relative z-10 border-t border-gold-900/15 bg-vault-950 py-28 text-bone">
      <div className="mx-auto max-w-5xl px-6">
        <p className="font-terminal text-xs uppercase tracking-[0.3em] text-gold-900">
          03 — The Engine Room
        </p>
        <h2 className="mt-3 font-display text-3xl font-semibold leading-tight text-bone sm:text-4xl">
          Built like the vault it frames.
        </h2>
        <p className="mt-5 max-w-2xl font-body leading-relaxed text-bone-muted">
          No fantasy pricing. A reconciled, traceable appraisal pipeline,
          self-hosted and operated end to end.
        </p>

        <dl className="mt-11 grid gap-x-12 gap-y-0 sm:grid-cols-2">
          {SPEC.map((row) => (
            <div
              key={row.k}
              className="flex flex-col gap-1 border-b border-gold-900/15 py-4"
            >
              <dt className="font-terminal text-[0.68rem] uppercase tracking-[0.16em] text-gold-700">
                {row.k}
              </dt>
              <dd className="font-terminal text-[0.82rem] leading-relaxed text-bone-muted">
                {row.v}
              </dd>
            </div>
          ))}
        </dl>

        <div className="mt-14 flex flex-col items-center gap-5 text-center">
          <WadjetEye className="w-12 opacity-80" title="The Eye of Wadjet" />
          <p className="font-body text-bone-muted">
            Designed, built, deployed, and operated by Erick Ti.
          </p>
          <a
            href="/collection"
            className="group inline-flex items-center gap-2.5 rounded-sm border border-gold-700/55 bg-gold-700/10 px-5 py-3 font-terminal text-xs uppercase tracking-[0.18em] text-gold-300 transition-colors hover:border-gold-500 hover:bg-gold-700/20"
          >
            Enter the vault
            <span className="transition-transform group-hover:translate-x-0.5">→</span>
          </a>
          <p className="font-terminal text-[0.7rem] text-bone-muted">
            Source · github.com/erickti/millennium
          </p>
        </div>

        <hr className="gold-rule mt-14" />
      </div>
    </section>
  );
}
