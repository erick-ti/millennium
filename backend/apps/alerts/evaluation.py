from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from apps.alerts.models import AlertEvent, AlertRule, Direction
from apps.valuation.movers import MoverRow, compute_collection_movers


@dataclass(frozen=True, slots=True)
class AlertEvaluationResult:
    """Outcome of one evaluation pass over the active rules."""

    rules_evaluated: int
    events_created: int
    # Idempotent skips: a matched pair that already had an event for today (a same-day
    # re-run). Proves the UNIQUE-keyed get_or_create is the idempotency boundary.
    events_existing: int


def _row_matches(row: MoverRow, direction: str, threshold_ratio: float) -> bool:
    """Whether a movers row crosses a rule's percent threshold in its direction.

    ``row.pct_change`` is a signed RATIO (``0.18`` = +18%) and can be ``None`` (the
    older-anchor base is below the movers $1.00 floor). A percent rule can't evaluate a
    near-zero base, so a ``None`` percent never matches — the sub-floor row is skipped
    (no dollar fallback in this slice), and the explicit ``is not None`` guard also keeps
    ``None >= threshold`` from raising a ``TypeError``."""
    pct = row.pct_change
    if pct is None:
        return False
    if direction == Direction.UP:
        return pct >= threshold_ratio
    if direction == Direction.DOWN:
        return pct <= -threshold_ratio
    # Direction.ANY — a move of the threshold magnitude in EITHER direction.
    return abs(pct) >= threshold_ratio


def _human_percent(row: MoverRow) -> Decimal:
    """The event's stored percent in HUMAN form (``18.00`` = +18%), 2dp.

    Recomputed from the row's Decimal anchors rather than its float ``pct_change`` to
    avoid a float round-trip into the DecimalField. Called only for a matched row, whose
    ``pct_change`` is non-null ⇒ ``start_price >= $1.00`` (the movers floor), so the
    division is always well-defined."""
    return (row.abs_change / row.start_price * 100).quantize(Decimal("0.01"))


def evaluate_active_rules() -> AlertEvaluationResult:
    """Evaluate every active ``AlertRule`` against today's collection price movers and
    record an ``AlertEvent`` for each matched ``(printing, edition)`` pair.

    Reuses ``compute_collection_movers`` (``apps/valuation/movers.py``) — the same
    two-anchor, usable-price delta the ``/movers`` view computes — so the near-zero
    floor, the market→mid→low fallback, and the partial-≠-zero anchor exclusion all apply
    identically and we never read a raw ``market_price``. Rules are grouped by
    ``window_days`` so the (collection-wide) movers query runs once per DISTINCT window
    (≤3 for the 7/30/90 menu), not once per rule.

    Each match get_or_creates on the ``AlertEvent`` UNIQUE
    ``(rule, printing, edition, triggered_on)``, denormalizing the rule's defining params
    onto the event at fire time, so a same-day re-run is idempotent (creates nothing
    new). The writes happen here; the advisory lock, the same-day-pricing gate, and the
    ``AlertRun`` recording live in ``run_alerts`` (``apps/alerts/sync.py``).
    """
    rules = list(AlertRule.objects.filter(is_active=True))
    today = timezone.localdate()

    # One movers computation per distinct window across all active rules (≤3).
    movers_by_window: dict[int, list[MoverRow]] = {
        window: compute_collection_movers(window_days=window)
        for window in {rule.window_days for rule in rules}
    }

    events_created = 0
    events_existing = 0
    for rule in rules:
        threshold_ratio = float(rule.threshold_pct) / 100.0
        for row in movers_by_window[rule.window_days]:
            if not _row_matches(row, rule.direction, threshold_ratio):
                continue
            _, created = AlertEvent.objects.get_or_create(
                rule=rule,
                printing_id=row.printing_id,
                edition=row.edition,
                triggered_on=today,
                defaults={
                    "rule_name": rule.name,
                    "rule_threshold_pct": rule.threshold_pct,
                    "rule_window_days": rule.window_days,
                    "rule_direction": rule.direction,
                    "start_price": row.start_price,
                    "end_price": row.end_price,
                    "pct_change": _human_percent(row),
                    "dollar_change": row.abs_change,
                },
            )
            if created:
                events_created += 1
            else:
                events_existing += 1

    return AlertEvaluationResult(
        rules_evaluated=len(rules),
        events_created=events_created,
        events_existing=events_existing,
    )
