from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.portfolio.models import Portfolio
from apps.pricing.models import PriceSnapshot, Provider

# The movers query anchors on timezone.localdate() (no injectable day, like
# value_all_portfolios), so tests place snapshots RELATIVE to "today".
TODAY = timezone.localdate()
URL = reverse("valuation:mover-list")


@pytest.fixture
def client() -> APIClient:
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _printing(*, name: str = "Ash Blossom", set_code: str = "L5DD-ENC09") -> CardPrinting:
    card = Card.objects.create(name=name)
    return CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Common", set_name="set"
    )


def _own(
    printing: CardPrinting,
    *,
    edition: Edition = Edition.FIRST_EDITION,
    quantity: int = 1,
) -> CollectionItem:
    """Own ``quantity`` copies of ``(printing, edition)``. ``quantity=0`` creates the
    holding identity with NO lots (derived quantity 0) — the "catalogued but not
    currently held" case the movers query excludes."""
    portfolio = Portfolio.objects.get_or_create(name="Yubel Deck")[0]
    item = CollectionItem.objects.create(
        portfolio=portfolio,
        printing=printing,
        condition=Condition.NEAR_MINT,
        edition=edition,
        language=Language.ENGLISH,
    )
    if quantity:
        CollectionLot.objects.create(
            collection_item=item, quantity=quantity, unit_cost=None, acquired_at=None
        )
    return item


def _snap(
    printing: CardPrinting,
    *,
    days_ago: int,
    edition: Edition = Edition.FIRST_EDITION,
    market: Decimal | None = None,
    mid: Decimal | None = None,
    low: Decimal | None = None,
    high: Decimal | None = None,
) -> PriceSnapshot:
    return PriceSnapshot.objects.create(
        printing=printing,
        edition=edition,
        source=Provider.TCGCSV,
        snapshot_date=TODAY - timedelta(days=days_ago),
        market_price=market,
        mid_price=mid,
        low_price=low,
        high_price=high,
    )


def _rows(client: APIClient, **query: Any) -> list[dict[str, Any]]:
    resp = client.get(URL, query)
    assert resp.status_code == 200, resp.content
    body = resp.json()
    # Standard paginated envelope (every list endpoint).
    assert set(body) == {"count", "next", "previous", "results"}
    results: list[dict[str, Any]] = body["results"]
    return results


# --- auth -----------------------------------------------------------------------


@pytest.mark.django_db
def test_requires_authentication() -> None:
    assert APIClient().get(URL).status_code == 403


# --- two-anchor delta -----------------------------------------------------------


@pytest.mark.django_db
def test_basic_two_anchor_delta(client: APIClient) -> None:
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("10.00"))
    _snap(printing, days_ago=0, market=Decimal("12.00"))

    rows = _rows(client, window=30)
    assert len(rows) == 1
    row = rows[0]
    assert row["printing"] == printing.id
    assert row["card_id"] == printing.card_id
    assert row["card_name"] == "Ash Blossom"
    assert row["edition"] == Edition.FIRST_EDITION.value
    assert row["start_price"] == "10.00"
    assert row["end_price"] == "12.00"
    assert row["abs_change"] == "2.00"
    assert row["pct_change"] == pytest.approx(0.2)
    assert row["start_date"] == (TODAY - timedelta(days=30)).isoformat()
    assert row["end_date"] == TODAY.isoformat()


@pytest.mark.django_db
def test_uses_latest_usable_on_or_before_each_anchor(client: APIClient) -> None:
    printing = _printing()
    _own(printing)
    # end anchor (today): the most recent on-or-before wins (today-1 over today-5).
    _snap(printing, days_ago=5, market=Decimal("8.00"))
    _snap(printing, days_ago=1, market=Decimal("10.00"))
    # start anchor (today-30): latest on-or-before it is today-35, not today-40.
    _snap(printing, days_ago=40, market=Decimal("4.00"))
    _snap(printing, days_ago=35, market=Decimal("5.00"))

    rows = _rows(client, window=30)
    assert len(rows) == 1
    assert rows[0]["start_price"] == "5.00"
    assert rows[0]["end_price"] == "10.00"


@pytest.mark.django_db
def test_base_price_falls_back_market_mid_low(client: APIClient) -> None:
    # Reuses the engine's market->mid->low priority: start row has only `low`,
    # end row has only `mid` — both usable, neither a raw market_price.
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, low=Decimal("2.00"))
    _snap(printing, days_ago=0, mid=Decimal("3.00"))

    rows = _rows(client, window=30)
    assert rows[0]["start_price"] == "2.00"
    assert rows[0]["end_price"] == "3.00"
    assert rows[0]["abs_change"] == "1.00"


# --- exclusion rules (partial != zero) ------------------------------------------


@pytest.mark.django_db
def test_excludes_pair_without_start_anchor(client: APIClient) -> None:
    # Priced only recently (within the window) — no usable snapshot on-or-before
    # today-30, so the pair is EXCLUDED, never reported as a +100% move from $0.
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=5, market=Decimal("10.00"))

    assert _rows(client, window=30) == []


@pytest.mark.django_db
def test_excludes_pair_with_unusable_start_anchor(client: APIClient) -> None:
    # The only snapshot on-or-before the start anchor is high-only (no
    # market/mid/low) -> unusable -> start anchor missing -> EXCLUDED. Guards the
    # _USABLE_PRICE reuse (a newer high-only row must not mask "no usable price").
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=35, high=Decimal("9.99"))  # unusable
    _snap(printing, days_ago=0, market=Decimal("10.00"))

    assert _rows(client, window=30) == []


# --- collection scoping ---------------------------------------------------------


@pytest.mark.django_db
def test_only_owned_pairs_are_ranked(client: APIClient) -> None:
    owned = _printing(name="Owned", set_code="AAA-EN001")
    _own(owned)
    _snap(owned, days_ago=30, market=Decimal("10.00"))
    _snap(owned, days_ago=0, market=Decimal("12.00"))

    # Priced but not owned at all -> not ranked.
    unowned = _printing(name="Unowned", set_code="BBB-EN001")
    _snap(unowned, days_ago=30, market=Decimal("1.00"))
    _snap(unowned, days_ago=0, market=Decimal("99.00"))

    rows = _rows(client, window=30)
    assert [row["card_name"] for row in rows] == ["Owned"]


@pytest.mark.django_db
def test_zero_quantity_holding_is_excluded(client: APIClient) -> None:
    # A holding identity that exists but holds no copies (no lots) -> not "owned".
    printing = _printing()
    _own(printing, quantity=0)
    _snap(printing, days_ago=30, market=Decimal("10.00"))
    _snap(printing, days_ago=0, market=Decimal("12.00"))

    assert _rows(client, window=30) == []


@pytest.mark.django_db
def test_scoped_per_edition(client: APIClient) -> None:
    # Own only the 1st-Edition pair; the Unlimited pair is priced but not owned.
    printing = _printing()
    _own(printing, edition=Edition.FIRST_EDITION)
    _snap(printing, days_ago=30, edition=Edition.FIRST_EDITION, market=Decimal("10.00"))
    _snap(printing, days_ago=0, edition=Edition.FIRST_EDITION, market=Decimal("12.00"))
    _snap(printing, days_ago=30, edition=Edition.UNLIMITED, market=Decimal("5.00"))
    _snap(printing, days_ago=0, edition=Edition.UNLIMITED, market=Decimal("50.00"))

    rows = _rows(client, window=30)
    assert len(rows) == 1
    assert rows[0]["edition"] == Edition.FIRST_EDITION.value
    assert rows[0]["end_price"] == "12.00"


# --- window ---------------------------------------------------------------------


@pytest.mark.django_db
def test_window_selects_the_start_anchor(client: APIClient) -> None:
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("10.00"))
    _snap(printing, days_ago=7, market=Decimal("11.00"))
    _snap(printing, days_ago=0, market=Decimal("12.00"))

    assert _rows(client, window=7)[0]["start_price"] == "11.00"
    assert _rows(client, window=30)[0]["start_price"] == "10.00"


@pytest.mark.django_db
def test_window_defaults_to_30(client: APIClient) -> None:
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("10.00"))
    _snap(printing, days_ago=0, market=Decimal("12.00"))

    # No window param -> 30, so the today-30 anchor resolves.
    rows = _rows(client)
    assert len(rows) == 1
    assert rows[0]["start_price"] == "10.00"


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["45", "0", "abc", "-7", "31"])
def test_invalid_window_is_400(client: APIClient, bad: str) -> None:
    resp = client.get(URL, {"window": bad})
    assert resp.status_code == 400
    assert "window" in resp.json()


# --- ordering -------------------------------------------------------------------


def _two_movers() -> None:
    # Big gainer: +50% / +$5.  Small gainer: +10% / +$1.
    big = _printing(name="Big", set_code="AAA-EN001")
    _own(big)
    _snap(big, days_ago=30, market=Decimal("10.00"))
    _snap(big, days_ago=0, market=Decimal("15.00"))
    small = _printing(name="Small", set_code="BBB-EN001")
    _own(small)
    _snap(small, days_ago=30, market=Decimal("10.00"))
    _snap(small, days_ago=0, market=Decimal("11.00"))


@pytest.mark.django_db
def test_default_ordering_is_pct_change_desc(client: APIClient) -> None:
    _two_movers()
    assert [row["card_name"] for row in _rows(client, window=30)] == ["Big", "Small"]


@pytest.mark.django_db
def test_ordering_pct_change_ascending(client: APIClient) -> None:
    _two_movers()
    rows = _rows(client, window=30, ordering="pct_change")
    assert [row["card_name"] for row in rows] == ["Small", "Big"]


@pytest.mark.django_db
def test_ordering_abs_change_desc(client: APIClient) -> None:
    _two_movers()
    rows = _rows(client, window=30, ordering="-abs_change")
    assert [row["card_name"] for row in rows] == ["Big", "Small"]


@pytest.mark.django_db
def test_invalid_ordering_is_400(client: APIClient) -> None:
    resp = client.get(URL, {"ordering": "card_name"})
    assert resp.status_code == 400
    assert "ordering" in resp.json()


def _inverted_movers() -> None:
    # The dollar ranking and the percent ranking DISAGREE, so an assertion can
    # tell which key the server actually sorted on. BigDollar moves +$1.00 / +10%;
    # BigPercent moves +$0.40 / +40% (base $1.00, at the inclusive floor → a real
    # percent). -abs_change ⇒ [BigDollar, BigPercent]; -pct_change ⇒ the reverse.
    big_dollar = _printing(name="BigDollar", set_code="AAA-EN001")
    _own(big_dollar)
    _snap(big_dollar, days_ago=30, market=Decimal("10.00"))
    _snap(big_dollar, days_ago=0, market=Decimal("11.00"))
    big_percent = _printing(name="BigPercent", set_code="BBB-EN001")
    _own(big_percent)
    _snap(big_percent, days_ago=30, market=Decimal("1.00"))
    _snap(big_percent, days_ago=0, market=Decimal("1.40"))


@pytest.mark.django_db
def test_ordering_separates_dollar_from_percent(client: APIClient) -> None:
    # The load-bearing ordering test: with a fixture where the two rankings
    # invert, the server must honor the REQUESTED key (a silent fallback to the
    # default or a key/attribute swap would order both the same and fail here).
    _inverted_movers()
    by_dollar = _rows(client, window=30, ordering="-abs_change")
    assert [row["card_name"] for row in by_dollar] == ["BigDollar", "BigPercent"]
    by_percent = _rows(client, window=30, ordering="-pct_change")
    assert [row["card_name"] for row in by_percent] == ["BigPercent", "BigDollar"]


@pytest.mark.django_db
def test_ordering_abs_change_ascending(client: APIClient) -> None:
    # The fourth allowlist token (smallest dollar move first), on the inverted
    # fixture so it also can't accidentally key on percent.
    _inverted_movers()
    rows = _rows(client, window=30, ordering="abs_change")
    assert [row["card_name"] for row in rows] == ["BigPercent", "BigDollar"]


@pytest.mark.django_db
def test_ordering_pct_ascending_puts_loss_before_gain(client: APIClient) -> None:
    # Ascending percent is the "biggest losers" view — verify a loss sorts before
    # a gain (signed comparison, not magnitude).
    loser = _printing(name="Loser", set_code="AAA-EN001")
    _own(loser)
    _snap(loser, days_ago=30, market=Decimal("20.00"))
    _snap(loser, days_ago=0, market=Decimal("15.00"))  # -25%
    gainer = _printing(name="Gainer", set_code="BBB-EN001")
    _own(gainer)
    _snap(gainer, days_ago=30, market=Decimal("10.00"))
    _snap(gainer, days_ago=0, market=Decimal("15.00"))  # +50%

    asc = _rows(client, window=30, ordering="pct_change")
    assert [row["card_name"] for row in asc] == ["Loser", "Gainer"]
    desc = _rows(client, window=30, ordering="-pct_change")
    assert [row["card_name"] for row in desc] == ["Gainer", "Loser"]


# --- near-zero percent floor ----------------------------------------------------


@pytest.mark.django_db
def test_sub_floor_base_nulls_percent_but_keeps_dollar(client: APIClient) -> None:
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("0.50"))  # below the $1.00 floor
    _snap(printing, days_ago=0, market=Decimal("1.50"))

    rows = _rows(client, window=30)
    assert len(rows) == 1
    assert rows[0]["abs_change"] == "1.00"
    assert rows[0]["pct_change"] is None


@pytest.mark.django_db
def test_zero_base_nulls_percent(client: APIClient) -> None:
    # A legitimate 0.00 base price (real, not missing) -> percent undefined -> null,
    # dollar move still reported.
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("0.00"))
    _snap(printing, days_ago=0, market=Decimal("2.00"))

    rows = _rows(client, window=30)
    assert rows[0]["abs_change"] == "2.00"
    assert rows[0]["pct_change"] is None


@pytest.mark.django_db
def test_floor_boundary_is_inclusive(client: APIClient) -> None:
    # Exactly at the floor ($1.00) -> percent IS computed.
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("1.00"))
    _snap(printing, days_ago=0, market=Decimal("2.00"))

    rows = _rows(client, window=30)
    assert rows[0]["pct_change"] == pytest.approx(1.0)


@pytest.mark.django_db
def test_null_percent_rows_sort_last(client: APIClient) -> None:
    # A normal mover and a sub-floor (null-percent) mover; ordering by pct_change
    # in either direction puts the null-percent row last.
    normal = _printing(name="Normal", set_code="AAA-EN001")
    _own(normal)
    _snap(normal, days_ago=30, market=Decimal("10.00"))
    _snap(normal, days_ago=0, market=Decimal("12.00"))
    cheap = _printing(name="Cheap", set_code="BBB-EN001")
    _own(cheap)
    _snap(cheap, days_ago=30, market=Decimal("0.50"))
    _snap(cheap, days_ago=0, market=Decimal("0.90"))

    desc = _rows(client, window=30, ordering="-pct_change")
    assert [row["card_name"] for row in desc] == ["Normal", "Cheap"]
    asc = _rows(client, window=30, ordering="pct_change")
    assert [row["card_name"] for row in asc] == ["Normal", "Cheap"]


# --- loss (negative move) -------------------------------------------------------


@pytest.mark.django_db
def test_negative_move_is_reported(client: APIClient) -> None:
    printing = _printing()
    _own(printing)
    _snap(printing, days_ago=30, market=Decimal("20.00"))
    _snap(printing, days_ago=0, market=Decimal("15.00"))

    row = _rows(client, window=30)[0]
    assert row["abs_change"] == "-5.00"
    assert row["pct_change"] == pytest.approx(-0.25)
