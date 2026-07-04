"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import {
  type CollectionItemList,
  type DeckMembership,
  decksDecksDestroy,
  decksDecksListQueryKey,
  decksDecksRetrieveOptions,
  decksDecksRetrieveQueryKey,
  decksMembershipsCreate,
  decksMembershipsDestroy,
  decksMembershipsListOptions,
  decksMembershipsListQueryKey,
} from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { DetailSkeleton } from "@/components/cards/detail-skeleton";
import { HoldingPicker } from "@/components/decks/holding-picker";
import { formatDayShort } from "@/lib/format";
import { seedCsrf } from "@/lib/csrf";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 100;

const CONDITION_LABELS: Record<string, string> = {
  mint: "Mint",
  near_mint: "Near Mint",
  excellent: "Excellent",
  good: "Good",
  light_played: "Light Played",
  played: "Played",
  poor: "Poor",
};
const EDITION_LABELS: Record<string, string> = {
  first: "1st",
  unlimited: "Unlimited",
  limited: "Limited",
};
const LANGUAGE_LABELS: Record<string, string> = {
  en: "EN",
  fr: "FR",
  de: "DE",
  it: "IT",
  es: "ES",
  pt: "PT",
  ja: "JA",
  ko: "KO",
};

/** Pull a DRF `{detail: "..."}` message out of an error body. */
function detailMessage(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function failure(action: string, error: unknown, response: Response | undefined): Error {
  // A 403 can be a missing/stale CSRF cookie; re-seed so the next attempt carries a token
  // without a reload (harmless for an auth 403: the backend detail still tells the user to
  // sign in). The import/alert write recovery.
  if (response?.status === 403) seedCsrf();
  return new Error(
    detailMessage(error) ??
      (response
        ? `${action} failed (HTTP ${response.status}).`
        : `${action} failed: could not reach the server.`),
  );
}

type AddOutcome = "added" | "duplicate";

// Bare SDK fns (not the *Mutation helpers) so we can read response.status, and add returns
// 409 when the holding is already in the deck, surfaced distinctly (the import 409 pattern).
async function addMember(args: {
  deckId: number;
  item: CollectionItemList;
}): Promise<AddOutcome> {
  const { data, error, response } = await decksMembershipsCreate({
    body: { deck: args.deckId, collection_item: args.item.id },
  });
  if (response?.status === 409) return "duplicate";
  if (!data) throw failure("Add", error, response);
  return "added";
}

async function removeMember(membershipId: number): Promise<void> {
  // DELETE → 204 with no body, so `data` is undefined on success; branch on the status.
  const { error, response } = await decksMembershipsDestroy({ path: { id: membershipId } });
  if (response && response.status >= 200 && response.status < 300) return;
  throw failure("Remove", error, response);
}

async function deleteDeck(deckId: number): Promise<void> {
  const { error, response } = await decksDecksDestroy({ path: { id: deckId } });
  if (response && response.status >= 200 && response.status < 300) return;
  throw failure("Delete deck", error, response);
}

type Feedback = { tone: "green" | "amber" | "red" | "neutral"; text: string };

const FEEDBACK_CLASSES: Record<Feedback["tone"], string> = {
  green: "border-gain/30 bg-gain/10 text-gain",
  amber: "border-flat/30 bg-flat/10 text-flat",
  red: "border-loss/30 bg-loss/10 text-loss",
  neutral: "border-gold-900/20 bg-vault-900 text-bone-muted",
};

export function DeckDetail({ deckId }: { deckId: number }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { canWrite } = useAuth();
  const [page, setPage] = useState(1);
  const [showPicker, setShowPicker] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  // The "Add holdings" trigger stays mounted (toggling its label) so closing the picker
  // never strands keyboard focus; closePicker also restores focus to it when the picker's
  // own Cancel button unmounts (the slice-3 focus rule, the import-batch-detail pattern).
  const addButtonRef = useRef<HTMLButtonElement>(null);

  const deckQuery = useQuery(decksDecksRetrieveOptions({ path: { id: deckId } }));

  const membersQuery = useQuery({
    ...decksMembershipsListOptions({ query: { deck: deckId, page } }),
    placeholderData: keepPreviousData,
  });

  function invalidate() {
    // A membership add/remove changes three caches: this deck's member feed (all pages), its
    // header (member_count), AND the /decks list row's member_count. Invalidate all three, or
    // returning to the list shows a stale count within the provider's staleTime window.
    queryClient.invalidateQueries({ queryKey: decksMembershipsListQueryKey() });
    queryClient.invalidateQueries({
      queryKey: decksDecksRetrieveQueryKey({ path: { id: deckId } }),
    });
    queryClient.invalidateQueries({ queryKey: decksDecksListQueryKey() });
  }

  const addMutation = useMutation({
    mutationFn: addMember,
    onSuccess: (outcome, variables) => {
      if (outcome === "duplicate") {
        setFeedback({
          tone: "amber",
          text: `${variables.item.card_name} is already in this deck.`,
        });
      } else {
        setFeedback({
          tone: "green",
          text: `Added ${variables.item.card_name} to the deck.`,
        });
      }
      invalidate();
    },
    onError: (error) => setFeedback({ tone: "red", text: error.message }),
  });

  const removeMutation = useMutation({
    mutationFn: removeMember,
    onSuccess: () => {
      setFeedback({ tone: "neutral", text: "Removed from the deck." });
      invalidate();
    },
    onError: (error) => setFeedback({ tone: "red", text: error.message }),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteDeck,
    onSuccess: () => {
      // Drop the deleted deck's own caches BEFORE navigating, or a browser-back would render
      // the deleted deck from a still-fresh retrieve cache. Use removeQueries (not invalidate)
      // since there's no point refetching a 404.
      queryClient.removeQueries({
        queryKey: decksDecksRetrieveQueryKey({ path: { id: deckId } }),
      });
      queryClient.removeQueries({ queryKey: decksMembershipsListQueryKey() });
      // Refetch the list so the deleted deck drops out, then soft-nav back. A soft nav is fine
      // here (a voluntary delete, not an auth boundary: no stale auth observer to tear down,
      // unlike logout).
      queryClient.invalidateQueries({ queryKey: decksDecksListQueryKey() });
      router.push("/decks");
    },
    onError: (error) => {
      setConfirmingDelete(false);
      setFeedback({ tone: "red", text: error.message });
    },
  });

  // One shared mutation object per action can't track WHICH row is in flight, so disable
  // all row actions while ANY mutation is pending or a keepPreviousData page transition is
  // showing stale rows (the import-batch-detail rule).
  const anyActionPending =
    addMutation.isPending || removeMutation.isPending || deleteMutation.isPending;

  function goToPage(next: number) {
    setPage(next);
    setShowPicker(false);
  }

  function closePicker() {
    setShowPicker(false);
    // The picker's Cancel button unmounts with the picker, so restore focus to the (always
    // mounted) trigger rather than letting it fall to <body>.
    addButtonRef.current?.focus();
  }

  if (deckQuery.isPending) {
    return <DetailSkeleton label="deck" />;
  }

  if (deckQuery.isError || deckQuery.data == null) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-10">
        <BackLink />
        <div className="mt-4">
          <QueryErrorState
            title="Couldn't load this deck."
            onRetry={() => deckQuery.refetch()}
          />
        </div>
      </div>
    );
  }

  const deck = deckQuery.data;
  const members = membersQuery.data?.results ?? [];
  const count = membersQuery.data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = membersQuery.isPlaceholderData;
  const hasPrev = Boolean(membersQuery.data?.previous);
  const hasNext = Boolean(membersQuery.data?.next);
  const actionsLocked = anyActionPending || isPaging;

  const columns: Array<ColumnDef<DeckMembership>> = [
    {
      accessorKey: "card_name",
      header: "Card",
      cell: ({ row }) => (
        <div>
          <div className="font-medium text-bone">{row.original.card_name}</div>
          <div className="text-xs text-bone-muted">
            {row.original.set_code} · {row.original.set_rarity}
            {row.original.variant_label ? ` · ${row.original.variant_label}` : ""}
          </div>
        </div>
      ),
    },
    {
      accessorKey: "quantity",
      header: () => <div className="text-right">Copies</div>,
      // The holding's copy count (SUM of its lots). A deck counts distinct holdings, but a
      // single tagged holding can be N physical copies, so show that here.
      cell: ({ row }) => (
        <div className="text-right nums-terminal">{row.original.quantity}</div>
      ),
    },
    {
      accessorKey: "condition",
      header: "Condition",
      cell: ({ row }) =>
        CONDITION_LABELS[row.original.condition] ?? row.original.condition,
    },
    {
      accessorKey: "edition",
      header: "Edition",
      cell: ({ row }) => EDITION_LABELS[row.original.edition] ?? row.original.edition,
    },
    {
      accessorKey: "language",
      header: "Language",
      cell: ({ row }) =>
        LANGUAGE_LABELS[row.original.language] ?? row.original.language,
    },
    {
      accessorKey: "portfolio_name",
      header: "Portfolio",
      cell: ({ row }) => (
        <span className="text-bone-muted">{row.original.portfolio_name}</span>
      ),
    },
    {
      accessorKey: "created_at",
      header: () => <div className="text-right">Added</div>,
      cell: ({ row }) => (
        <div className="text-right nums-terminal text-bone-muted">
          {formatDayShort(row.original.created_at.slice(0, 10))}
        </div>
      ),
    },
  ];

  // The Remove column is owner-only: the demo can't mutate memberships (DemoReadOnly).
  if (canWrite) {
    columns.push({
      id: "actions",
      header: () => <div className="text-right">Actions</div>,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <Button
            size="xs"
            variant="destructive"
            disabled={actionsLocked}
            onClick={() => removeMutation.mutate(row.original.id)}
          >
            Remove
          </Button>
        </div>
      ),
    });
  }

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <BackLink />
      <PageHeader
        className="mt-3"
        kicker="DECK"
        title={deck.name}
        subtitle={
          <>
            {deck.description?.trim() ? deck.description : "No description."} ·{" "}
            {deck.member_count}{" "}
            {deck.member_count === 1 ? "holding" : "holdings"}
          </>
        }
        actions={
          !canWrite ? undefined : (
          <div className="flex items-center gap-2">
            {confirmingDelete ? (
              <>
                <span className="text-sm text-bone-muted">Delete this deck?</span>
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={deleteMutation.isPending}
                  onClick={() => deleteMutation.mutate(deckId)}
                >
                  {deleteMutation.isPending ? "Deleting…" : "Confirm delete"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={deleteMutation.isPending}
                  onClick={() => setConfirmingDelete(false)}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirmingDelete(true)}
              >
                Delete deck
              </Button>
            )}
          </div>
          )
        }
      />

      {feedback ? (
        <div
          role="status"
          className={cn(
            "flex items-start justify-between gap-3 rounded-lg border p-3 text-sm",
            FEEDBACK_CLASSES[feedback.tone],
          )}
        >
          <span>{feedback.text}</span>
          <button
            type="button"
            onClick={() => setFeedback(null)}
            className="shrink-0 text-xs underline underline-offset-4"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      <div
        className={cn(
          "flex items-center justify-between gap-3",
          feedback ? "mt-6" : "mt-0",
        )}
      >
        <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
          Holdings in this deck
        </h2>
        {canWrite ? (
          <Button
            ref={addButtonRef}
            size="sm"
            onClick={() => setShowPicker((open) => !open)}
          >
            {showPicker ? "Close" : "Add holdings"}
          </Button>
        ) : null}
      </div>

      {showPicker ? (
        <div className="mt-3">
          <HoldingPicker
            isSubmitting={addMutation.isPending}
            onCancel={closePicker}
            onSelect={(item) => addMutation.mutate({ deckId, item })}
          />
        </div>
      ) : null}

      <div className="mt-3">
        {membersQuery.isPending ? (
          <TableSkeleton columnCount={columns.length} label="Loading deck" />
        ) : membersQuery.isError ? (
          <QueryErrorState
            title="Couldn't load this deck's cards."
            onRetry={() => membersQuery.refetch()}
            backLabel={page > 1 ? `Back to page ${page - 1}` : undefined}
            onBack={page > 1 ? () => goToPage(Math.max(1, page - 1)) : undefined}
          />
        ) : count === 0 ? (
          // No holdings tagged into this deck yet → the lit display-case empty
          // state (the "Add holdings" trigger above is the CTA).
          <EmptyState
            title="No holdings in this deck yet"
            description="Use “Add holdings” above to tag cards from your collection into this deck."
          />
        ) : (
          <div
            aria-busy={isPaging}
            className={isPaging ? "opacity-60 transition-opacity" : undefined}
          >
            <DataTable
              columns={columns}
              data={members}
              emptyMessage="No holdings in this deck yet. Use “Add holdings” to tag cards from your collection."
            />
            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="holding"
              isPaging={isPaging}
              hasPrev={hasPrev}
              hasNext={hasNext}
              onPageChange={goToPage}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/decks"
      className="font-terminal text-xs uppercase tracking-[0.12em] text-gold-700 transition-colors hover:text-gold-500"
    >
      ← Decks
    </Link>
  );
}
