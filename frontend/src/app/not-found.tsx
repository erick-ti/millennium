import Link from "next/link";

/**
 * The vault-themed 404 (replaces Next's default). Renders inside the root layout
 * (nav + grain), so it only needs the centered message + a gold route back.
 */
export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-6xl flex-col items-center justify-center px-6 py-24 text-center">
      <p className="font-terminal text-xs uppercase tracking-[0.3em] text-gold-900">
        404 — Not found
      </p>
      <h1 className="mt-4 font-display text-4xl font-semibold leading-tight tracking-tight text-bone sm:text-5xl">
        This page isn&rsquo;t in the vault.
      </h1>
      <p className="mt-3 max-w-md font-body text-sm leading-relaxed text-bone-muted">
        The page you&rsquo;re looking for doesn&rsquo;t exist, has moved, or was
        never minted.
      </p>
      <hr className="gold-rule mt-8 w-40" />
      <Link
        href="/collection"
        className="mt-8 inline-flex items-center gap-1.5 rounded-lg border border-gold-700/50 bg-gold-700/10 px-4 py-2 font-terminal text-xs uppercase tracking-[0.12em] text-gold-300 transition-colors hover:bg-gold-700/20"
      >
        Back to your collection &rarr;
      </Link>
    </div>
  );
}
