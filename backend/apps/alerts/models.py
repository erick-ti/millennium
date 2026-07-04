from __future__ import annotations

from django.db import models

from apps.core.enums import Edition
from apps.core.models import TimeStampedModel

# The lookback windows an alert rule may use, the same closed set the "biggest movers"
# view offers (apps/valuation/movers.py WINDOW_DAYS_CHOICES). A small fixed menu keeps
# the rule form, the DB CHECK, and the per-window evaluation grouping honest; an open
# integer column would spread the evaluation's "one movers query per distinct window"
# grouping and need a free-text input. Defined here (not imported from valuation) so the
# alerts model layer carries no dependency on the valuation app.
ALERT_WINDOW_DAYS_CHOICES: tuple[int, ...] = (7, 30, 90)
DEFAULT_ALERT_WINDOW_DAYS = 30


class Direction(models.TextChoices):
    """Which direction of price move a rule fires on. ANY = either direction by
    magnitude (``|move| >= threshold``)."""

    UP = "up", "Up"
    DOWN = "down", "Down"
    ANY = "any", "Any direction"


class AlertStatus(models.TextChoices):
    """Outcome of an alert-evaluation run. Mirrors ``ValuationStatus``: SUCCESS/FAILED
    plus SKIPPED, a run refused because its same-day pricing dependency wasn't met."""

    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class AlertRule(TimeStampedModel):
    """A user-configured price-move alert: fire when an owned ``(printing, edition)``
    pair moves at least ``threshold_pct`` percent over ``window_days``, in ``direction``.

    Mutable user config (NOT append-only, unlike the ``Portfolio`` / ``StorageLocation``
    posture): the user creates/edits/deactivates rules. ``is_active`` gates evaluation;
    the rich edit/delete/mute UI is deferred (this is the pipeline-first slice), but the
    column exists so a later slice can toggle it without a migration.

    ``threshold_pct`` is a HUMAN percent (``10.00`` = 10%), always positive (a
    magnitude: the ``direction`` decides up/down). The evaluation converts it to the
    movers ratio (``pct / 100``) before comparing (see ``apps/alerts/evaluation.py``).
    """

    name = models.CharField(max_length=255)
    threshold_pct = models.DecimalField(max_digits=6, decimal_places=2)
    window_days = models.PositiveSmallIntegerField(
        choices=[(days, f"{days} days") for days in ALERT_WINDOW_DAYS_CHOICES],
        default=DEFAULT_ALERT_WINDOW_DAYS,
    )
    direction = models.CharField(
        max_length=8, choices=Direction.choices, default=Direction.ANY
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # Closed-vocabulary / domain guards at the DB (``choices`` is form-layer
            # only), the SyncRun / ValuationRun enum-CHECK precedent; all enforced on
            # sqlite too. ``threshold_pct`` is a magnitude, so it must be strictly > 0.
            models.CheckConstraint(
                condition=models.Q(threshold_pct__gt=0),
                name="alert_rule_threshold_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(direction__in=Direction.values),
                name="alert_rule_direction_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(window_days__in=list(ALERT_WINDOW_DAYS_CHOICES)),
                name="alert_rule_window_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} (>={self.threshold_pct}% / {self.window_days}d / {self.direction})"


class AlertEvent(TimeStampedModel):
    """Append-only record of one rule firing on one ``(printing, edition)`` on one day,
    the rows the in-app feed renders.

    Append-only (the ``PriceSnapshot`` / ``ValuationRun`` posture): inserted by the
    daily evaluation, never edited; the admin blocks edit/delete. The ``rule`` FK is
    PROTECT: an event is non-re-derivable history, so deleting a rule must not silently
    erase its feed (the ``PriceSnapshot.printing`` posture). The rule's defining
    parameters are ALSO denormalized onto the event at fire time (the ``rule_*`` fields),
    so a later edit of the *mutable* rule can't rewrite what a past event reports it
    fired on, the event stays a faithful immutable record (the user-settled choice).

    All price/move fields are non-null: an event is created only from a movers row that
    has a usable price at BOTH anchors AND a non-null ``pct_change`` (a percent rule
    skips a sub-floor, null-percent row, so there is no event without a real percent move).
    If a future slice adds dollar-threshold rules, ``pct_change`` becomes nullable then.
    """

    rule = models.ForeignKey(AlertRule, on_delete=models.PROTECT, related_name="events")
    printing = models.ForeignKey(
        "cards.CardPrinting", on_delete=models.PROTECT, related_name="alert_events"
    )
    edition = models.CharField(max_length=16, choices=Edition.choices)
    # The UTC day the event was recorded (``timezone.localdate()``, the snapshot_date
    # append-only-key precedent; the daily get_or_create keys on it).
    triggered_on = models.DateField()

    # Fire-time snapshot of the mutable rule's defining params (immutable copy).
    rule_name = models.CharField(max_length=255)
    rule_threshold_pct = models.DecimalField(max_digits=6, decimal_places=2)
    rule_window_days = models.PositiveSmallIntegerField()
    rule_direction = models.CharField(max_length=8)

    # The move that triggered the event (the movers two-anchor delta). Prices are the
    # latest *usable* TCGCSV snapshot at each anchor (``start_price`` on-or-before
    # today-window, ``end_price`` on-or-before today). ``pct_change`` is a HUMAN percent
    # (``18.00`` = +18%); ``dollar_change`` is signed (a down-move is negative). Money is
    # DecimalField (serialized as strings, the PriceSnapshot convention).
    #
    # ``pct_change`` is WIDER than the money fields (16,2 vs 12,2): the percent is a RATIO of
    # two prices, so an extreme move off the $1.00 movers floor (e.g. a $1 base → a $20k chase
    # printing) yields ~2,000,000%, which a (8,2) column (max 999,999.99) would overflow,
    # and because the whole pass runs in one transaction that DataError would roll back the
    # ENTIRE day's events (and recur daily until the move ages out of the window). 14 integer
    # digits covers the full reachable range: a max price (~1e10) over the $1.00 floor, as a
    # percent (times 100), is about 1e12. This is a Postgres-only overflow: sqlite ignores
    # the precision, so it surfaces only on Postgres.
    start_price = models.DecimalField(max_digits=12, decimal_places=2)
    end_price = models.DecimalField(max_digits=12, decimal_places=2)
    pct_change = models.DecimalField(max_digits=16, decimal_places=2)
    dollar_change = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        # Newest-first feed; ``-id`` is a stable tiebreaker so "latest" is deterministic
        # when two events share a ``triggered_on`` (the CollectionLot ordering lesson).
        ordering = ["-triggered_on", "-id"]
        constraints = [
            # One event per (rule, printing, edition) per UTC day, granular, so the feed
            # shows *which* cards moved (a per-(rule, day) digest couldn't reconstruct
            # that). All four columns are non-null, so this is a plain UNIQUE created AND
            # exercised on sqlite (no ``nulls_distinct`` Postgres-only apparatus); the
            # daily evaluation get_or_creates on this key, so a re-run is idempotent.
            models.UniqueConstraint(
                fields=["rule", "printing", "edition", "triggered_on"],
                name="unique_alert_event_per_rule_pair_day",
            ),
            models.CheckConstraint(
                condition=models.Q(edition__in=Edition.values),
                name="alert_event_edition_valid",
            ),
            # Anchor prices are non-negative (the PriceSnapshot posture); ``pct_change``
            # and ``dollar_change`` carry no sign bound: a down-move is legitimately
            # negative (the ``unrealized_gain`` precedent).
            models.CheckConstraint(
                condition=models.Q(start_price__gte=0),
                name="alert_event_start_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(end_price__gte=0),
                name="alert_event_end_price_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["-triggered_on", "-id"], name="alert_event_feed_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.rule_name}: printing {self.printing_id} "
            f"{self.edition} on {self.triggered_on}"
        )


class AlertRun(TimeStampedModel):
    """Append-only record of one alert-evaluation pass, the run history backing the
    daily beat job (mirrors ``ValuationRun``).

    A dedicated model rather than a ``SyncKind`` on ``core.SyncRun``: alerts does no
    fetch (none of SyncRun's card/printing/product/price-row cardinality columns apply),
    and a new ``SyncKind`` would force a CHECK-altering migration on the shared sync
    model. SUCCESS with the pass's counts (``rules_evaluated`` / ``events_created``);
    SKIPPED with the reason when refused because no successful same-day TCGCSV pricing
    ``SyncRun`` exists (the dependency the evaluation reads its prices through); FAILED
    with the error after a rollback. Append-only: inserted, never updated; the admin
    blocks edit/delete (the ``ValuationRun`` posture).
    """

    status = models.CharField(max_length=16, choices=AlertStatus.choices)
    # Per-pass counts, filled on SUCCESS. Left NULL on SKIPPED (nothing evaluated) and
    # FAILED (the pass rolled back), the SyncRun/ValuationRun pattern, where a count
    # that doesn't apply to an outcome stays NULL rather than a misleading 0.
    rules_evaluated = models.PositiveIntegerField(null=True, blank=True)
    events_created = models.PositiveIntegerField(null=True, blank=True)
    # Open-shape audit detail (no CHECK, the SyncRun.detail precedent).
    detail = models.JSONField(default=dict, blank=True)
    # Why a run FAILED or was SKIPPED (blank on success).
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=AlertStatus.values),
                name="alert_run_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="alert_run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Alerts {self.get_status_display()} ({self.created_at:%Y-%m-%d %H:%M})"
