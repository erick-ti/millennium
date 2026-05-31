from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.alerts.models import AlertEvent, AlertRule, Direction
from apps.cards.models import Card, CardPrinting
from apps.core.enums import Edition

TODAY = timezone.localdate()
EVENTS_URL = reverse("alerts:alertevent-list")
RULES_URL = reverse("alerts:alertrule-list")


@pytest.fixture
def client() -> APIClient:
    user = get_user_model().objects.create_user("reader", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _rule(*, name: str = "rule", direction: Direction = Direction.ANY) -> AlertRule:
    return AlertRule.objects.create(
        name=name, threshold_pct=Decimal("10.00"), window_days=30, direction=direction
    )


def _printing(*, name: str = "Ash Blossom", set_code: str = "AAA-EN001") -> CardPrinting:
    card = Card.objects.create(name=name)
    return CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Common", set_name="set"
    )


def _event(rule: AlertRule, printing: CardPrinting, *, days_ago: int = 0, **overrides: Any) -> AlertEvent:
    defaults: dict[str, Any] = {
        "edition": Edition.FIRST_EDITION,
        "triggered_on": TODAY - timedelta(days=days_ago),
        "rule_name": rule.name,
        "rule_threshold_pct": rule.threshold_pct,
        "rule_window_days": rule.window_days,
        "rule_direction": rule.direction,
        "start_price": Decimal("10.00"),
        "end_price": Decimal("12.00"),
        "pct_change": Decimal("20.00"),
        "dollar_change": Decimal("2.00"),
    }
    defaults.update(overrides)
    return AlertEvent.objects.create(rule=rule, printing=printing, **defaults)


# --- auth ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_require_authentication() -> None:
    assert APIClient().get(EVENTS_URL).status_code == 403
    assert APIClient().get(RULES_URL).status_code == 403
    assert APIClient().post(RULES_URL, {}).status_code == 403


# --- events feed ---------------------------------------------------------------------


@pytest.mark.django_db
def test_events_list_returns_feed_rows(client: APIClient) -> None:
    rule = _rule(name="Big up moves")
    printing = _printing()
    _event(rule, printing)

    resp = client.get(EVENTS_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"count", "next", "previous", "results"}
    row = body["results"][0]
    assert row["rule"] == rule.id
    assert row["rule_name"] == "Big up moves"
    assert row["rule_window_days"] == 30
    assert row["card_id"] == printing.card_id
    assert row["card_name"] == "Ash Blossom"
    assert row["set_code"] == "AAA-EN001"
    assert row["edition"] == Edition.FIRST_EDITION.value
    assert row["triggered_on"] == TODAY.isoformat()
    assert row["pct_change"] == "20.00"  # string-decimal, exact
    assert row["dollar_change"] == "2.00"


@pytest.mark.django_db
def test_events_are_newest_first(client: APIClient) -> None:
    rule = _rule()
    old = _printing(name="Old", set_code="AAA-EN001")
    new = _printing(name="New", set_code="BBB-EN001")
    _event(rule, old, days_ago=5)
    _event(rule, new, days_ago=0)

    names = [row["card_name"] for row in client.get(EVENTS_URL).json()["results"]]
    assert names == ["New", "Old"]


@pytest.mark.django_db
def test_events_filter_by_rule(client: APIClient) -> None:
    rule_a = _rule(name="A")
    rule_b = _rule(name="B")
    printing = _printing()
    _event(rule_a, printing, edition=Edition.FIRST_EDITION)
    _event(rule_b, printing, edition=Edition.UNLIMITED)

    rows = client.get(EVENTS_URL, {"rule": rule_a.id}).json()["results"]
    assert len(rows) == 1
    assert rows[0]["rule"] == rule_a.id


@pytest.mark.django_db
def test_events_invalid_rule_filter_is_400(client: APIClient) -> None:
    resp = client.get(EVENTS_URL, {"rule": "abc"})
    assert resp.status_code == 400
    assert "rule" in resp.json()


# --- rules: list + create ------------------------------------------------------------


@pytest.mark.django_db
def test_rules_list(client: APIClient) -> None:
    _rule(name="Zeta")
    _rule(name="Alpha")
    names = [row["name"] for row in client.get(RULES_URL).json()["results"]]
    assert names == ["Alpha", "Zeta"]  # ordered by name


@pytest.mark.django_db
def test_create_rule(client: APIClient) -> None:
    resp = client.post(
        RULES_URL,
        {"name": "Big movers", "threshold_pct": "15.00", "window_days": 30, "direction": "up"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["id"]
    assert body["name"] == "Big movers"
    assert body["is_active"] is True  # model default echoed in the response
    rule = AlertRule.objects.get()
    assert (rule.threshold_pct, rule.window_days, rule.direction) == (Decimal("15.00"), 30, "up")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload,bad_field",
    [
        ({"name": "x", "threshold_pct": "10", "window_days": 30, "direction": "sideways"}, "direction"),
        ({"name": "x", "threshold_pct": "10", "window_days": 45, "direction": "up"}, "window_days"),
        ({"name": "x", "threshold_pct": "0", "window_days": 30, "direction": "up"}, "threshold_pct"),
        ({"name": "x", "threshold_pct": "-5", "window_days": 30, "direction": "up"}, "threshold_pct"),
        ({"threshold_pct": "10", "window_days": 30, "direction": "up"}, "name"),
    ],
)
def test_create_rule_validation(client: APIClient, payload: dict[str, Any], bad_field: str) -> None:
    resp = client.post(RULES_URL, payload, format="json")
    assert resp.status_code == 400
    assert bad_field in resp.json()
    assert not AlertRule.objects.exists()
