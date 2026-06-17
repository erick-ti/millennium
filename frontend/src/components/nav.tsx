"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import { LogoutButton } from "@/components/auth/logout-button";

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
  const { user, isAuthenticated } = useAuth();
  const pathname = usePathname();

  // The public landing ("/") is a full-bleed cinematic surface with its own
  // masthead; the app chrome would clash with it.
  if (pathname === "/") return null;

  return (
    <nav className="border-b border-border bg-background">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-2 px-6 py-3">
        <Link
          href="/"
          className="font-semibold tracking-tight text-foreground"
        >
          Millennium
        </Link>
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted-foreground">
          {routes.map((route) => (
            <li key={route.href}>
              <Link
                href={route.href}
                className="transition-colors hover:text-foreground"
              >
                {route.label}
              </Link>
            </li>
          ))}
        </ul>
        {isAuthenticated ? (
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-muted-foreground">{user?.username}</span>
            <LogoutButton />
          </div>
        ) : null}
      </div>
    </nav>
  );
}
