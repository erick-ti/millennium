import type { Metadata } from "next";
import {
  Archivo,
  Cormorant_Garamond,
  Fraunces,
  IBM_Plex_Mono,
} from "next/font/google";
import "./globals.css";
import { AppGrain } from "@/components/app-grain";
import { AuthProvider } from "@/components/auth-provider";
import { CsrfBootstrap } from "@/components/csrf-bootstrap";
import { ErrorReporter } from "@/components/error-reporter";
import { Nav } from "@/components/nav";
import { Providers } from "@/components/providers";

// The Vault typefaces — used across both the landing and the authed app.
// Fraunces carries the SOFT/WONK axes for its inscriptional display character;
// Archivo is the body workhorse, IBM Plex Mono the terminal numerics.
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  axes: ["SOFT", "WONK"],
});

const cormorant = Cormorant_Garamond({
  variable: "--font-cormorant",
  subsets: ["latin"],
  weight: ["500", "600"],
  style: ["normal", "italic"],
});

const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Millennium — a collection, appraised like a portfolio",
  description:
    "A Yu-Gi-Oh collection tracked like an investment portfolio: per-lot cost basis, confidence-scored daily pricing, and coverage-aware valuation. Built, deployed, and operated by one engineer.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${fraunces.variable} ${cormorant.variable} ${archivo.variable} ${plexMono.variable} dark h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <Providers>
          <CsrfBootstrap />
          <ErrorReporter />
          <AuthProvider>
            <AppGrain />
            <Nav />
            {/* relative z-10 keeps authed content crisp above the fixed grain (z-1). */}
            <main className="relative z-10 flex-1">{children}</main>
          </AuthProvider>
        </Providers>
      </body>
    </html>
  );
}
