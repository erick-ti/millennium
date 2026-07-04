from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class Portfolio(TimeStampedModel):
    """A logical grouping of holdings, the investment-account analogue.

    Dragon Shield's ``Folder Name`` find-or-creates a portfolio by name on
    import, so ``name`` is unique: ``get_or_create``
    resolves a folder to exactly one portfolio. This is a single-column UNIQUE
    over a non-null text column, so unlike the ``CardPrinting`` natural key it
    IS created and exercised on sqlite under ``make test``. Distinct from
    ``storage_location`` (physical whereabouts), which a portfolio does not own.

    Name canonicalization (trim / case-fold for matching) is deliberately
    deferred to the Phase 3 DS-import boundary, the single-function approach
    taken for ``set_code`` / ``external_id``, not a
    per-field ``save()`` coercion or CHECK here. That boundary must trim
    ``Folder Name`` before ``get_or_create``, since unique-on-raw-name shares
    ``external_id``'s dirty-alias gap (``"Yubel Deck "`` vs ``"Yubel Deck"``).
    """

    name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class PortfolioValueSnapshot(TimeStampedModel):
    """A portfolio's total value on a given day, append-only daily history.

    The valuation engine (Phase 2) writes one row per portfolio per day, rolling
    the holdings x prices up into total ``market_value``, ``liquidation_value``
    (a quick-sell estimate), ``cost_basis`` (SUM of acquisition lots), and the
    derived ``unrealized_gain``. Append-only: a day's snapshot is inserted once
    and never updated, so the value timeline powering historical analytics is a
    pure range scan. (Append-only is a convention here, not a ``save()``-enforced
    lock.)

    The lower layers honestly model "unknown" (``CollectionLot.unit_cost`` and
    ``PriceSnapshot`` prices are nullable; a printing may have no snapshot at
    all), so a rolled-up total must not silently look complete. The engine sums
    only what it can value/cost, unknowns are *excluded*, never coerced to 0,
    and records how much of the portfolio each total actually covers via the
    ``*_card_count`` fields; ``unrealized_gain`` is left NULL unless coverage is
    full, because under partial coverage ``market_value`` and ``cost_basis`` sum
    different subsets and their difference is not a gain.

    ``valuation_method`` and ``valuation_version`` record *how* the row was
    computed, so a snapshot stays interpretable after the valuation formula
    changes: a change applies going forward (one snapshot per portfolio per day,
    tagged with its version), not by re-valuing history, hence the key is
    ``(portfolio, snapshot_date)``, version excluded.

    The ``portfolio`` FK is ``PROTECT``: the value timeline is not cheaply
    re-derivable (it needs the holdings *and* the prices as they were on each
    past day), so a portfolio delete must not cascade it away, consistent with
    the PROTECT on ``CollectionItem.portfolio``.
    """

    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.PROTECT, related_name="value_snapshots"
    )
    snapshot_date = models.DateField()
    # Portfolio totals use a wider Decimal than per-card prices (12,2): a holding
    # of many cards aggregates well past any single card's range. NOT NULL with NO
    # default: a valuation is a computed event, so a writer that omits a total
    # must fail closed rather than silently record 0; an empty portfolio writes 0
    # explicitly.
    market_value = models.DecimalField(max_digits=14, decimal_places=2)
    liquidation_value = models.DecimalField(max_digits=14, decimal_places=2)
    cost_basis = models.DecimalField(max_digits=14, decimal_places=2)
    # market_value - cost_basis, but only when the valuation is fully covered.
    # Under partial coverage market_value and cost_basis sum *different* subsets
    # (priced cards vs costed lots), so their difference is not a gain: the engine
    # leaves this NULL and the consumer reads it as "not computable yet". The CHECKs
    # below allow NULL or, when set, tie it to market_value - cost_basis AND require
    # full coverage. A set value may still be negative (a holding underwater is a
    # legitimate loss). This is the conditional successor to Phase
    # 1B's unconditional gain CHECK.
    unrealized_gain = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    # Coverage, as card-quantity counts. total = every owned
    # card (SUM of lot quantities); priced = cards whose holding got a usable price;
    # costed = cards whose acquisition lot had a known unit_cost. NOT NULL, no
    # default (the money-fields posture: the engine always sets them). The
    # market_value_complete / cost_basis_complete flags are *derived* from these
    # (properties below), not stored, so they can't drift from the counts.
    total_card_count = models.PositiveIntegerField()
    priced_card_count = models.PositiveIntegerField()
    costed_card_count = models.PositiveIntegerField()
    # How this row was valued, recorded so older snapshots stay interpretable when
    # the formula changes. The valuation engine defines the method vocabulary, so
    # this is open text (no enum / CHECK).
    valuation_method = models.CharField(max_length=64)
    valuation_version = models.PositiveSmallIntegerField()

    class Meta:
        # (portfolio, snapshot_date) is the unique key with the date reversed
        # (latest first), so the order is fully deterministic: one row per
        # portfolio per day, no ties, snapshot_date non-null, no tiebreaker.
        ordering = ["portfolio", "-snapshot_date"]
        constraints = [
            # One valuation per portfolio per day. Both columns non-null → a plain
            # UNIQUE, created and exercised on sqlite too.
            models.UniqueConstraint(
                fields=["portfolio", "snapshot_date"],
                name="unique_portfolio_value_snapshot_per_day",
            ),
            # Totals can't be negative; unrealized_gain is deliberately excluded
            # (a loss is a valid negative gain).
            models.CheckConstraint(
                condition=models.Q(market_value__gte=0),
                name="portfolio_value_snapshot_market_value_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(liquidation_value__gte=0),
                name="portfolio_value_snapshot_liquidation_value_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(cost_basis__gte=0),
                name="portfolio_value_snapshot_cost_basis_non_negative",
            ),
            # Coverage counts are subsets of the total, so neither can exceed it.
            models.CheckConstraint(
                condition=models.Q(priced_card_count__lte=models.F("total_card_count")),
                name="portfolio_value_snapshot_priced_count_within_total",
            ),
            models.CheckConstraint(
                condition=models.Q(costed_card_count__lte=models.F("total_card_count")),
                name="portfolio_value_snapshot_costed_count_within_total",
            ),
            # unrealized_gain is stored (queryable / sortable) but, when set, must
            # equal market_value - cost_basis so it can't drift from the row's own
            # totals. NULL is allowed (partial coverage, see the next CHECK); no
            # sign bound, since a loss is a valid negative gain.
            models.CheckConstraint(
                condition=models.Q(unrealized_gain__isnull=True)
                | models.Q(unrealized_gain=models.F("market_value") - models.F("cost_basis")),
                name="portfolio_value_snapshot_unrealized_gain_matches",
            ),
            # unrealized_gain is set if and ONLY if coverage is full on both sides:
            # only when market_value and cost_basis describe the same (whole) portfolio
            # is their difference a true gain. So a complete row MUST carry the gain and
            # a partial row MUST leave it NULL, the biconditional, not just one
            # direction, so an admin/bulk writer can't persist a complete row with a
            # NULL gain (which would read as is_complete yet have no P&L). The counts
            # are non-null, so each equality is a clean boolean, the CHECK never
            # evaluates to NULL. This constraint was tightened after review; it is
            # the conditional successor to Phase 1B's unconditional
            # gain CHECK.
            models.CheckConstraint(
                condition=models.Q(
                    priced_card_count=models.F("total_card_count"),
                    costed_card_count=models.F("total_card_count"),
                    unrealized_gain__isnull=False,
                )
                | (
                    ~(
                        models.Q(priced_card_count=models.F("total_card_count"))
                        & models.Q(costed_card_count=models.F("total_card_count"))
                    )
                    & models.Q(unrealized_gain__isnull=True)
                ),
                name="portfolio_value_snapshot_gain_iff_complete",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.portfolio} @ {self.snapshot_date}: {self.market_value}"

    @property
    def market_value_complete(self) -> bool:
        """True iff every owned card was priced, so market_value covers the whole
        portfolio rather than a subset. Derived from the counts (not stored) so it
        can't drift; for an empty portfolio (total 0) it is vacuously True."""
        return self.priced_card_count >= self.total_card_count

    @property
    def cost_basis_complete(self) -> bool:
        """True iff every owned card's acquisition lot had a known unit_cost."""
        return self.costed_card_count >= self.total_card_count

    @property
    def is_complete(self) -> bool:
        """Both sides fully covered, the only state in which unrealized_gain is
        computed (non-null) and the totals describe the whole portfolio."""
        return self.market_value_complete and self.cost_basis_complete
