from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.alerts.evaluation import evaluate_active_rules
from apps.alerts.models import AlertEvent, AlertRule, Direction
from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.portfolio.models import Portfolio
from apps.pricing.models import PriceSnapshot, Provider
from apps.valuation import movers as movers_module

# Evaluation anchors on timezone.localdate() (via compute_collection_movers), so tests
# place snapshots RELATIVE to "today" — the movers-test convention.
TODAY = timezone.localdate()


def _printing(*, name: str = "Ash Blossom", set_code: str = "L5DD-ENC09") -> CardPrinting:
    card = Card.objects.create(name=name)
    return CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Common", set_name="set"
    )


def _own(printing: CardPrinting, *, edition: Edition = Edition.FIRST_EDITION) -> None:
    portfolio = Portfolio.objects.get_or_create(name="Yubel Deck")[0]
    item = CollectionItem.objects.create(
        portfolio=portfolio,
        printing=printing,
        condition=Condition.NEAR_MINT,
        edition=edition,
        language=Language.ENGLISH,
    )
    CollectionLot.objects.create(collection_item=item, quantity=1, unit_cost=None, acquired_at=None)


def _snap(
    printing: CardPrinting,
    *,
    days_ago: int,
    edition: Edition = Edition.FIRST_EDITION,
    market: Decimal,
) -> None:
    PriceSnapshot.objects.create(
        printing=printing,
        edition=edition,
        source=Provider.TCGCSV,
        snapshot_date=TODAY - timedelta(days=days_ago),
        market_price=market,
    )


def _rule(
    *,
    threshold_pct: str,
    window_days: int = 30,
    direction: Direction = Direction.ANY,
    is_active: bool = True,
    name: str = "rule",
) -> AlertRule:
    return AlertRule.objects.create(
        name=name,
        threshold_pct=Decimal(threshold_pct),
        window_days=window_days,
        direction=direction,
        is_active=is_active,
    )


def _gainer(pct_str_pair: tuple[Decimal, Decimal] = (Decimal("10.00"), Decimal("11.80"))) -> CardPrinting:
    """An owned printing that moved from start→end over 30 days (default +18%)."""
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=pct_str_pair[0])
    _snap(printing, days_ago=0, market=pct_str_pair[1])
    return printing


# --- the units conversion (the #1 risk: ratio 0.18 vs human percent 18.00) ----------


@pytest.mark.django_db
def test_human_percent_threshold_matches_movers_ratio() -> None:
    """A +18% move (movers ratio 0.18) crosses an 18.00%-threshold rule. The rule stores
    a HUMAN percent; the evaluation converts it to the ratio (pct/100) before comparing.
    Getting this backwards (comparing 18 to 0.18) would make every rule fire."""
    _gainer()  # 10.00 -> 11.80 = +18%
    _rule(threshold_pct="18.00", direction=Direction.UP)

    result = evaluate_active_rules()

    assert (result.rules_evaluated, result.events_created) == (1, 1)


@pytest.mark.django_db
def test_threshold_just_above_the_move_does_not_fire() -> None:
    """A 19.00%-threshold rule does NOT fire on a +18% move — the boundary is real, not a
    units artifact (a 18 vs 0.18 bug would fire here too)."""
    _gainer()  # +18%
    _rule(threshold_pct="19.00", direction=Direction.UP)

    assert evaluate_active_rules().events_created == 0
    assert not AlertEvent.objects.exists()


@pytest.mark.django_db
def test_threshold_at_the_move_is_inclusive() -> None:
    """The threshold comparison is >= (a move exactly at the threshold fires)."""
    _gainer((Decimal("10.00"), Decimal("12.00")))  # +20% exactly
    _rule(threshold_pct="20.00", direction=Direction.UP)

    assert evaluate_active_rules().events_created == 1


# --- the stored event ----------------------------------------------------------------


@pytest.mark.django_db
def test_event_stores_human_percent_and_fire_time_rule_snapshot() -> None:
    printing = _gainer()  # 10.00 -> 11.80 = +18% / +$1.80
    rule = _rule(threshold_pct="10.00", window_days=30, direction=Direction.UP, name="Big up")

    evaluate_active_rules()

    event = AlertEvent.objects.get()
    assert event.rule_id == rule.id
    assert event.printing_id == printing.id
    assert event.edition == Edition.FIRST_EDITION.value
    assert event.triggered_on == TODAY
    assert event.start_price == Decimal("10.00")
    assert event.end_price == Decimal("11.80")
    assert event.pct_change == Decimal("18.00")  # HUMAN percent, not the 0.18 ratio
    assert event.dollar_change == Decimal("1.80")
    # Fire-time rule snapshot (immutable copy of the mutable rule's params).
    assert event.rule_name == "Big up"
    assert event.rule_threshold_pct == Decimal("10.00")
    assert event.rule_window_days == 30
    assert event.rule_direction == Direction.UP.value


# --- direction filtering -------------------------------------------------------------


@pytest.mark.django_db
def test_up_rule_ignores_a_down_move() -> None:
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("20.00"))
    _snap(printing, days_ago=0, market=Decimal("15.00"))  # -25%
    _rule(threshold_pct="10.00", direction=Direction.UP)

    assert evaluate_active_rules().events_created == 0


@pytest.mark.django_db
def test_down_rule_fires_on_a_down_move_and_ignores_a_gain() -> None:
    loser = _printing(name="Loser", set_code="AAA-EN001")
    _own(loser)
    _snap(loser, days_ago=30, market=Decimal("20.00"))
    _snap(loser, days_ago=0, market=Decimal("15.00"))  # -25%
    gainer = _printing(name="Gainer", set_code="BBB-EN001")
    _own(gainer)
    _snap(gainer, days_ago=30, market=Decimal("10.00"))
    _snap(gainer, days_ago=0, market=Decimal("15.00"))  # +50%
    _rule(threshold_pct="20.00", direction=Direction.DOWN)

    evaluate_active_rules()

    event = AlertEvent.objects.get()  # only the loser
    assert event.printing_id == loser.id
    assert event.pct_change == Decimal("-25.00")  # signed
    assert event.dollar_change == Decimal("-5.00")


@pytest.mark.django_db
def test_any_rule_fires_in_both_directions() -> None:
    up = _printing(name="Up", set_code="AAA-EN001")
    _own(up)
    _snap(up, days_ago=30, market=Decimal("10.00"))
    _snap(up, days_ago=0, market=Decimal("13.00"))  # +30%
    down = _printing(name="Down", set_code="BBB-EN001")
    _own(down)
    _snap(down, days_ago=30, market=Decimal("20.00"))
    _snap(down, days_ago=0, market=Decimal("14.00"))  # -30%
    _rule(threshold_pct="25.00", direction=Direction.ANY)

    assert evaluate_active_rules().events_created == 2
    assert AlertEvent.objects.count() == 2


# --- sub-floor (null percent) and partial-exclusion ----------------------------------


@pytest.mark.django_db
def test_sub_floor_move_does_not_crash_or_fire() -> None:
    """A move off a base below the movers $1.00 floor has pct_change=None. A percent rule
    can't evaluate it → no event, and the is-not-None guard means `None >= threshold`
    never raises (the load-bearing crash guard)."""
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("0.50"))  # below floor
    _snap(printing, days_ago=0, market=Decimal("0.95"))
    _rule(threshold_pct="1.00", direction=Direction.ANY)  # would fire on any real move

    assert evaluate_active_rules().events_created == 0
    assert not AlertEvent.objects.exists()


@pytest.mark.django_db
def test_pair_missing_an_anchor_produces_no_event() -> None:
    """Partial ≠ zero: a pair priced only within the window has no start anchor → no
    movers row → no event (never a fake +100% from $0)."""
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=5, market=Decimal("10.00"))  # only inside the window
    _rule(threshold_pct="1.00", direction=Direction.ANY)

    assert evaluate_active_rules().events_created == 0


@pytest.mark.django_db
def test_unowned_pair_is_not_evaluated() -> None:
    printing = _printing(name="Unowned", set_code="ZZZ-EN001")  # priced, not owned
    _snap(printing, days_ago=30, market=Decimal("10.00"))
    _snap(printing, days_ago=0, market=Decimal("99.00"))
    _rule(threshold_pct="10.00", direction=Direction.ANY)

    assert evaluate_active_rules().events_created == 0


# --- active flag, per-window grouping, idempotency -----------------------------------


@pytest.mark.django_db
def test_inactive_rule_is_skipped() -> None:
    _gainer()
    _rule(threshold_pct="1.00", direction=Direction.UP, is_active=False)

    result = evaluate_active_rules()

    assert result.rules_evaluated == 0  # the inactive rule isn't in the active set
    assert result.events_created == 0


@pytest.mark.django_db
def test_movers_query_runs_once_per_distinct_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two rules sharing window 30 + one rule at window 7 ⇒ the (collection-wide) movers
    query runs once per DISTINCT window (2 calls), not once per rule (3)."""
    _gainer()
    _rule(threshold_pct="5.00", window_days=30, name="a")
    _rule(threshold_pct="9.00", window_days=30, name="b")
    _rule(threshold_pct="5.00", window_days=7, name="c")

    windows_called: list[int] = []
    real = movers_module.compute_collection_movers

    def _spy(*, window_days: int) -> list[movers_module.MoverRow]:
        windows_called.append(window_days)
        return real(window_days=window_days)

    monkeypatch.setattr("apps.alerts.evaluation.compute_collection_movers", _spy)

    evaluate_active_rules()

    assert sorted(windows_called) == [7, 30]  # the two window-30 rules share one call


@pytest.mark.django_db
def test_evaluation_is_idempotent_for_the_same_day() -> None:
    _gainer()
    _rule(threshold_pct="10.00", direction=Direction.UP)

    first = evaluate_active_rules()
    second = evaluate_active_rules()

    assert (first.events_created, first.events_existing) == (1, 0)
    assert (second.events_created, second.events_existing) == (0, 1)  # re-run creates nothing
    assert AlertEvent.objects.count() == 1


@pytest.mark.django_db
def test_extreme_move_does_not_overflow_pct_change_column() -> None:
    """A move off the $1.00 movers floor to a high price yields a percent in the
    millions; the pct_change column (16,2) must hold it without a numeric-overflow
    DataError that would roll back the whole run. Postgres enforces the precision
    (sqlite ignores it), so this runs on the CI postgres job where the (8,2) bug bit
    (adversarial review 2026-05-31)."""
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("1.00"))  # at the inclusive floor
    _snap(printing, days_ago=0, market=Decimal("20000.00"))  # +1,999,900%
    _rule(threshold_pct="50.00", direction=Direction.UP)

    result = evaluate_active_rules()

    assert result.events_created == 1
    event = AlertEvent.objects.get()
    assert event.pct_change == Decimal("1999900.00")
    assert event.dollar_change == Decimal("19999.00")


@pytest.mark.django_db
def test_human_percent_quantizes_a_non_terminating_ratio() -> None:
    """_human_percent recomputes the stored percent from the Decimal anchors (not the
    float ratio) and quantizes to 2dp. A non-terminating ratio (1/30 = 3.333…%) must
    store as 3.33 — pins the quantize + the Decimal (not float) recompute."""
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("30.00"))
    _snap(printing, days_ago=0, market=Decimal("31.00"))  # +1/30 = 3.3333…%
    _rule(threshold_pct="3.00", direction=Direction.UP)

    evaluate_active_rules()

    assert AlertEvent.objects.get().pct_change == Decimal("3.33")


@pytest.mark.django_db
def test_one_rule_fires_across_multiple_owned_pairs() -> None:
    for idx in range(3):
        printing = _printing(name=f"Card {idx}", set_code=f"SET-EN00{idx}")
        _own(printing)
        _snap(printing, days_ago=30, market=Decimal("10.00"))
        _snap(printing, days_ago=0, market=Decimal("15.00"))  # +50%
    _rule(threshold_pct="20.00", direction=Direction.UP)

    assert evaluate_active_rules().events_created == 3
