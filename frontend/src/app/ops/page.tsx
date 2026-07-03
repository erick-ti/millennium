"use client";

import { useAuth } from "@/components/auth-provider";
import { AuditFeedPanel } from "@/components/ops/audit-feed-panel";
import { ErrorGroupsPanel } from "@/components/ops/error-groups-panel";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";

/**
 * The owner-only operations console (apps.audit). Superuser-gated: the nav link is
 * hidden for non-supers and the page itself renders a "Restricted" state rather than
 * firing the queries (so a demo/anon visitor isn't bounced to /login by the global 403
 * handler). The REAL boundary is server-side — every /api/audit read is IsSuperUser.
 */
export default function OpsPage() {
  const { isLoading, isAuthenticated, isSuperuser } = useAuth();

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <PageHeader
        kicker="THE SCRIBE"
        title="Operations"
        subtitle="Who did what, and what broke — an audit trail of every write across the app, and a fingerprint-grouped view of backend and frontend errors."
      />

      <div className="mt-6">
        {isLoading ? (
          <p className="font-terminal text-xs uppercase tracking-[0.12em] text-bone-muted">
            Checking access…
          </p>
        ) : !isSuperuser ? (
          <EmptyState
            title="Restricted"
            description={
              isAuthenticated
                ? "The operations console is owner-only."
                : "Sign in as the owner to view the operations console."
            }
          />
        ) : (
          <div className="space-y-12">
            <ErrorGroupsPanel />
            <AuditFeedPanel />
          </div>
        )}
      </div>
    </div>
  );
}
