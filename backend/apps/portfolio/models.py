from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class Portfolio(TimeStampedModel):
    """A logical grouping of holdings — the investment-account analogue.

    Dragon Shield's ``Folder Name`` find-or-creates a portfolio by name on
    import (DECISIONS 2026-05-18), so ``name`` is unique: ``get_or_create``
    resolves a folder to exactly one portfolio. This is a single-column UNIQUE
    over a non-null text column, so unlike the ``CardPrinting`` natural key it
    IS created and exercised on sqlite under ``make test``. Distinct from
    ``storage_location`` (physical whereabouts), which a portfolio does not own.

    Name canonicalization (trim / case-fold for matching) is deliberately
    deferred to the Phase 3 DS-import boundary — the single-function approach
    taken for ``set_code`` / ``external_id`` (DECISIONS 2026-05-21), not a
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
    """A portfolio's total value on a given day — append-only daily history.

    The valuation engine (Phase 2) writes one row per portfolio per day, rolling
    the holdings x prices up into total ``market_value``, ``liquidation_value``
    (a quick-sell estimate), ``cost_basis`` (SUM of acquisition lots), and the
    derived ``unrealized_gain``. Append-only: a day's snapshot is inserted once
    and never updated, so the value timeline powering historical analytics is a
    pure range scan. (Append-only is a convention here, not a ``save()``-enforced
    lock.)

    ``valuation_method`` and ``valuation_version`` record *how* the row was
    computed, so a snapshot stays interpretable after the valuation formula
    changes: a change applies going forward (one snapshot per portfolio per day,
    tagged with its version), not by re-valuing history — hence the key is
    ``(portfolio, snapshot_date)``, version excluded.

    The ``portfolio`` FK is ``PROTECT``: the value timeline is not cheaply
    re-derivable (it needs the holdings *and* the prices as they were on each
    past day), so a portfolio delete must not cascade it away — consistent with
    the PROTECT on ``CollectionItem.portfolio``.
    """

    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.PROTECT, related_name="value_snapshots"
    )
    snapshot_date = models.DateField()
    # Portfolio totals use a wider Decimal than per-card prices (12,2): a holding
    # of many cards aggregates well past any single card's range. NOT NULL with NO
    # default — a valuation is a computed event, so a writer that omits a total
    # must fail closed rather than silently record 0; an empty portfolio writes 0
    # explicitly.
    market_value = models.DecimalField(max_digits=14, decimal_places=2)
    liquidation_value = models.DecimalField(max_digits=14, decimal_places=2)
    cost_basis = models.DecimalField(max_digits=14, decimal_places=2)
    # market_value - cost_basis. Stored (so it's queryable / sortable) but a CHECK
    # below ties it to those two columns so it can't drift; the value itself may be
    # negative — a holding underwater is a legitimate loss.
    unrealized_gain = models.DecimalField(max_digits=14, decimal_places=2)
    # How this row was valued, recorded so older snapshots stay interpretable when
    # the formula changes. The Phase 2 valuation engine defines the method
    # vocabulary, so this is open text (no enum / CHECK) for now.
    valuation_method = models.CharField(max_length=64)
    valuation_version = models.PositiveSmallIntegerField()

    class Meta:
        # (portfolio, snapshot_date) is the unique key with the date reversed
        # (latest first), so the order is fully deterministic: one row per
        # portfolio per day, no ties, snapshot_date non-null — no tiebreaker.
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
            # unrealized_gain is stored (queryable) but must equal market_value -
            # cost_basis, so it can't drift from the row's own totals — no separate
            # sign bound, since a loss is a valid negative gain.
            models.CheckConstraint(
                condition=models.Q(unrealized_gain=models.F("market_value") - models.F("cost_basis")),
                name="portfolio_value_snapshot_unrealized_gain_matches",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.portfolio} @ {self.snapshot_date}: {self.market_value}"
