from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from django.db import transaction

from apps.cards.models import CardPrinting, MetadataSource, PrintingAlias
from apps.pricing.models import ExternalPriceId, Provider, UnmatchedProduct, UnmatchedReason
from apps.pricing.providers.base import ProductListing

# TCGCSV labels Quarter-Century reprints "Prismatic <rarity>" where YGOPRODeck
# seeded the plain "<rarity>" (recon Q3/Q5; DECISIONS 2026-05-23). When an exact
# match fails we retry once with this prefix stripped, then correct the printing's
# rarity to the canonical TCGCSV value.
_PRISMATIC_PREFIX = "Prismatic "


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Per-run counts from reconciling provider products to printings."""

    products_seen: int = 0
    skipped_blank_external_id: int = 0
    exact_matched: int = 0
    rarity_reconciled: int = 0
    external_ids_created: int = 0
    external_ids_existing: int = 0
    aliases_created: int = 0
    queued_no_printing_match: int = 0
    queued_multi_variant: int = 0
    queued_rarity_disagreement: int = 0
    queued_external_id_conflict: int = 0
    # The external_ids flagged EXTERNAL_ID_CONFLICT *this run* — passed to ingestion so
    # it skips them regardless of the queue row's (mutable, human-set) triage status.
    conflicted_external_ids: frozenset[str] = frozenset()


@dataclass
class _Counts:
    """Mutable accumulator (ints can't be incremented through helpers by value)."""

    products_seen: int = 0
    skipped_blank_external_id: int = 0
    exact_matched: int = 0
    rarity_reconciled: int = 0
    external_ids_created: int = 0
    external_ids_existing: int = 0
    aliases_created: int = 0
    queued_no_printing_match: int = 0
    queued_multi_variant: int = 0
    queued_rarity_disagreement: int = 0
    queued_external_id_conflict: int = 0
    conflicted_external_ids: set[str] = field(default_factory=set)

    def result(self) -> ReconcileResult:
        data = asdict(self)
        conflicted = data.pop("conflicted_external_ids")
        return ReconcileResult(conflicted_external_ids=frozenset(conflicted), **data)


def reconcile_products_to_printings(products: Iterable[ProductListing]) -> ReconcileResult:
    """Resolve provider products to ``CardPrinting`` rows, writing ``external_price_ids``,
    correcting provisional rarities in place, and queueing what can't be resolved.

    Identity only — no prices (that's the next slice). Per product:

    * **Exact** ``(set_code, set_rarity)`` match → attach an ``ExternalPriceId``.
    * **No exact, ``"Prismatic X"`` → ``X`` fallback** match → correct the printing's
      ``set_rarity`` to the canonical TCGCSV value *in place* (FKs are by ``id``, so a
      column UPDATE preserves every reference), record a ``PrintingAlias`` from the
      provisional key, and attach the ``ExternalPriceId``.
    * **Anything else** — no match, a ``(set_code, set_rarity)`` shared by several
      products (multi-variant), or a non-Prismatic disagreement — goes to the
      ``UnmatchedProduct`` review queue, never silently guessed (DECISIONS 2026-05-23).

    Two passes so a ``"Prismatic"`` fallback can never claim a printing another product
    matched exactly. Each group/product commits in its own transaction, so a failure is
    local rather than rolling back the run. Idempotent: ``external_price_ids`` / aliases
    get-or-create, the queue upserts, and a re-run finds corrected printings by their
    now-canonical key. Single-writer — no row locking.
    """
    counts = _Counts()
    groups: dict[tuple[str, str], list[ProductListing]] = defaultdict(list)
    for product in products:
        counts.products_seen += 1
        if not product.external_id.strip():
            # The provider id is the only handle for pricing later, so a blank one is
            # unusable — trim/reject at this boundary (the deferred external_id
            # obligation, DECISIONS 2026-05-21).
            counts.skipped_blank_external_id += 1
            continue
        groups[(product.set_code, product.set_rarity)].append(product)

    # Pass 1: exact matches (and multi-variant groups, which never claim a printing).
    # `claimed` records the printing ids taken here so pass 2 can't re-touch them.
    claimed: set[int] = set()
    deferred: list[ProductListing] = []
    for group in groups.values():
        if len(group) > 1:
            with transaction.atomic():
                for product in group:
                    _queue(product, UnmatchedReason.MULTI_VARIANT, counts)
            continue
        product = group[0]
        printing = _exact_match(product)
        if printing is None:
            deferred.append(product)
            continue
        with transaction.atomic():
            if _attach_external_id(printing, product, counts):
                counts.exact_matched += 1
            else:
                _queue(product, UnmatchedReason.EXTERNAL_ID_CONFLICT, counts)
        claimed.add(printing.pk)

    # Pass 2: Prismatic-fallback rarity corrections, else queue.
    for product in deferred:
        with transaction.atomic():
            _reconcile_deferred(product, claimed, counts)

    return counts.result()


def _exact_match(product: ProductListing) -> CardPrinting | None:
    return CardPrinting.objects.filter(
        set_code=product.set_code,
        set_rarity=product.set_rarity,
        variant_label__isnull=True,
    ).first()


def _reconcile_deferred(product: ProductListing, claimed: set[int], counts: _Counts) -> None:
    if product.set_rarity.startswith(_PRISMATIC_PREFIX):
        provisional_rarity = product.set_rarity[len(_PRISMATIC_PREFIX) :]
        base = CardPrinting.objects.filter(
            set_code=product.set_code,
            set_rarity=provisional_rarity,
            variant_label__isnull=True,
        ).first()
        if base is not None and base.pk not in claimed:
            _reconcile_rarity(base, product, provisional_rarity, counts)
            return
        if base is not None:
            # Another product already matched this printing exactly; correcting it now
            # would double-claim it. Defer the disagreement to a human.
            _queue(product, UnmatchedReason.RARITY_DISAGREEMENT, counts)
            return
    # No resolution. Distinguish "no card at all for this set_code" (e.g. a Token absent
    # from YGOPRODeck) from "the card exists but at a rarity we won't auto-correct"
    # (the New artwork / Short Print disagreement class — DECISIONS 2026-05-23).
    if CardPrinting.objects.filter(set_code=product.set_code).exists():
        _queue(product, UnmatchedReason.RARITY_DISAGREEMENT, counts)
    else:
        _queue(product, UnmatchedReason.NO_PRINTING_MATCH, counts)


def _reconcile_rarity(
    base: CardPrinting, product: ProductListing, provisional_rarity: str, counts: _Counts
) -> None:
    # Resolve the id first, so an id conflict (it already maps to a different printing)
    # is caught before we mutate anything — on conflict, queue and leave base as-is.
    if not _attach_external_id(base, product, counts):
        _queue(product, UnmatchedReason.EXTERNAL_ID_CONFLICT, counts)
        return
    # Correct the provisional rarity in place. FKs reference `base` by id, so this is a
    # column UPDATE that preserves every reference (DECISIONS 2026-05-23). It can't hit a
    # unique-key collision: exact-match-first (pass 1) means no printing already carries
    # product.set_rarity for this set_code — one would have matched there, not reached
    # here — and products sharing a canonical rarity group together (multi-variant), so
    # no two corrections target one base. The base-already-claimed case is the caller's
    # (queued as a disagreement, never corrected here).
    base.set_rarity = product.set_rarity
    base.save()
    _ensure_alias(base, provisional_rarity, counts)
    counts.rarity_reconciled += 1


def _attach_external_id(printing: CardPrinting, product: ProductListing, counts: _Counts) -> bool:
    """Map the provider id to ``printing``; return ``False`` on a conflict.

    A conflict is the id already resolving to a *different* printing — provider-side
    drift across runs, a manual edit, or a prior bad run. ``(provider, external_id)``
    is unique and which side is correct needs a human, so we never silently rewrite the
    mapping or report a false match: the caller queues it instead. (Can't use
    ``get_or_create`` here — it returns the existing row without revealing that its
    printing differs.)
    """
    external_id = product.external_id.strip()
    existing = ExternalPriceId.objects.filter(
        provider=Provider.TCGCSV, external_id=external_id
    ).first()
    if existing is not None:
        if existing.printing_id != printing.pk:
            return False
        counts.external_ids_existing += 1
        return True
    ExternalPriceId.objects.create(
        provider=Provider.TCGCSV, external_id=external_id, printing=printing
    )
    counts.external_ids_created += 1
    return True


def _ensure_alias(target: CardPrinting, provisional_rarity: str, counts: _Counts) -> None:
    # Maps YGOPRODeck's original (set_code, provisional_rarity) → the canonical printing,
    # so a YGOPRODeck re-sync resolves to it instead of recreating the provisional row.
    _, created = PrintingAlias.objects.get_or_create(
        source=MetadataSource.YGOPRODECK,
        card=target.card,
        set_code=target.set_code,
        set_rarity=provisional_rarity,
        defaults={"printing": target},
    )
    if created:
        counts.aliases_created += 1


def _queue(product: ProductListing, reason: UnmatchedReason, counts: _Counts) -> None:
    # Upsert on (provider, external_id): a re-run refreshes the entry's fields but
    # `update_or_create` leaves `status`/`notes` untouched, preserving human triage.
    UnmatchedProduct.objects.update_or_create(
        provider=Provider.TCGCSV,
        external_id=product.external_id.strip(),
        defaults={
            "set_code": product.set_code,
            "set_rarity": product.set_rarity,
            "product_name": product.name,
            "set_name": product.set_name,
            "reason": reason,
        },
    )
    if reason == UnmatchedReason.NO_PRINTING_MATCH:
        counts.queued_no_printing_match += 1
    elif reason == UnmatchedReason.MULTI_VARIANT:
        counts.queued_multi_variant += 1
    elif reason == UnmatchedReason.EXTERNAL_ID_CONFLICT:
        counts.queued_external_id_conflict += 1
        counts.conflicted_external_ids.add(product.external_id.strip())
    else:
        counts.queued_rarity_disagreement += 1
