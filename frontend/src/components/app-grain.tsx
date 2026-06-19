"use client";

import { usePathname } from "next/navigation";

/**
 * The fixed feTurbulence grain overlay (`.vault-grain` in globals.css),
 * propagated from the landing into the authed app so the whole surface shares
 * the same aged-stone tooth. Skipped on "/" — the landing renders its own grain,
 * and a second copy would double the texture. Pointer-events:none + ~3.5%
 * opacity, so it never affects interaction or readability; `<main>` sits at
 * `z-10` above it (layout), keeping content crisp over the texture.
 */
export function AppGrain() {
  const pathname = usePathname();
  if (pathname === "/") return null;
  return <div className="vault-grain" />;
}
