import { WadjetEye } from "@/components/brand/wadjet-eye";
import { cn } from "@/lib/utils";

/**
 * The Eye, made unmistakably functional: a prominent Eye of Wadjet (rotating
 * appraisal-dial = "scanning," pulsing pupil) beside a sync-cadence readout. The
 * point is legibility — a viewer should read "this eye is the catalog-sync
 * monitor," not "nice icon." The readout states the real nightly schedule (a
 * truthful, verifiable claim) rather than an unconditional real-time "all good"
 * status the static page can't actually know.
 */
export function EyeStatus({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-4 sm:gap-5", className)}>
      <WadjetEye animate live className="w-24 shrink-0 sm:w-28" />
      <div className="font-terminal">
        <p className="flex items-center gap-2 text-sm uppercase tracking-[0.2em] text-gold-500">
          <span className="relative flex size-2" aria-hidden>
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-gold-500/70 motion-reduce:animate-none" />
            <span className="relative inline-flex size-2 rounded-full bg-gold-500" />
          </span>
          Catalog
        </p>
        <p className="mt-2 text-sm text-bone">Synced nightly</p>
        <p className="mt-0.5 text-[0.72rem] text-bone-muted">02:00 UTC</p>
      </div>
    </div>
  );
}
