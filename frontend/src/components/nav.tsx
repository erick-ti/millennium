"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import { LogoutButton } from "@/components/auth/logout-button";
import { cn } from "@/lib/utils";

const routes: Array<{ href: string; label: string }> = [
  { href: "/collection", label: "Collection" },
  { href: "/cards", label: "Cards" },
  { href: "/portfolios", label: "Portfolios" },
  { href: "/movers", label: "Movers" },
  { href: "/alerts", label: "Alerts" },
  { href: "/decks", label: "Decks" },
  { href: "/imports", label: "Imports" },
  { href: "/status", label: "Status" },
];

export function Nav() {
  const { user, isAuthenticated, isDemo } = useAuth();
  const pathname = usePathname();

  // The public landing ("/") is a full-bleed cinematic surface with its own
  // masthead; the app chrome would clash with it.
  if (pathname === "/") return null;

  return (
    <nav className="border-b border-gold-900/25 bg-vault-950/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-2 px-6 py-3">
        <Link
          href="/"
          className="font-display text-lg font-semibold tracking-tight text-gold-700 transition-colors hover:text-gold-500"
        >
          Millennium
        </Link>
        <ul className="flex flex-wrap gap-x-5 gap-y-1.5">
          {routes.map((route) => {
            const active =
              pathname === route.href || pathname.startsWith(`${route.href}/`);
            return (
              <li key={route.href}>
                <Link
                  href={route.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "font-terminal text-xs uppercase tracking-[0.12em] transition-colors",
                    active ? "text-gold-500" : "text-bone-muted hover:text-bone"
                  )}
                >
                  {route.label}
                </Link>
              </li>
            );
          })}
        </ul>
        {isAuthenticated ? (
          <div className="ml-auto flex items-center gap-3">
            {isDemo ? (
              <span
                title="You’re viewing the read-only demo — sign in for full access"
                className="rounded-sm border border-gold-700/50 px-2 py-0.5 font-terminal text-[0.62rem] uppercase tracking-[0.14em] text-gold-500"
              >
                Demo · read-only
              </span>
            ) : (
              <span className="font-terminal text-xs tracking-wide text-bone-muted">
                {user?.username}
              </span>
            )}
            <LogoutButton />
          </div>
        ) : null}
      </div>
    </nav>
  );
}
