import Link from "next/link";

const routes: Array<{ href: string; label: string }> = [
  { href: "/collection", label: "Collection" },
  { href: "/cards", label: "Cards" },
  { href: "/portfolios", label: "Portfolios" },
  { href: "/imports", label: "Imports" },
];

export function Nav() {
  return (
    <nav className="border-b border-border bg-background">
      <div className="mx-auto flex max-w-6xl items-center gap-8 px-6 py-3">
        <Link
          href="/"
          className="font-semibold tracking-tight text-foreground"
        >
          Millennium
        </Link>
        <ul className="flex gap-5 text-sm text-muted-foreground">
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
      </div>
    </nav>
  );
}
