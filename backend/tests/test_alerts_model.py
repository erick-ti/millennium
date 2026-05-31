from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone

from apps.alerts.admin import AlertEventAdmin, AlertRunAdmin
from apps.alerts.models import AlertEvent, AlertRule, AlertRun, Direction
from apps.cards.models import Card, CardPrinting
from apps.core.enums import Edition

TODAY = timezone.localdate()


def _rule(**overrides: Any) -> AlertRule:
    defaults: dict[str, Any] = {
        "name": "rule",
        "threshold_pct": Decimal("10.00"),
        "window_days": 30,
        "direction": Direction.ANY,
    }
    defaults.update(overrides)
    return AlertRule.objects.create(**defaults)


def _printing(*, set_code: str = "AAA-EN001") -> CardPrinting:
    card = Card.objects.create(name="Ash Blossom")
    return CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Common", set_name="set"
    )


def _event(rule: AlertRule, printing: CardPrinting, **overrides: Any) -> AlertEvent:
    defaults: dict[str, Any] = {
        "edition": Edition.FIRST_EDITION,
        "triggered_on": TODAY,
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


# --- enum / domain CHECK constraints (enforced on sqlite too) -----------------------


@pytest.mark.django_db
def test_alert_rule_direction_check_rejects_unknown_value() -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AlertRule.objects.create(name="x", threshold_pct=Decimal("5"), direction="sideways")


@pytest.mark.django_db
def test_alert_rule_window_check_rejects_off_menu_value() -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AlertRule.objects.create(name="x", threshold_pct=Decimal("5"), window_days=45)


@pytest.mark.django_db
@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-5")])
def test_alert_rule_threshold_must_be_positive(bad: Decimal) -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AlertRule.objects.create(name="x", threshold_pct=bad)


@pytest.mark.django_db
def test_alert_event_edition_check_rejects_unknown_value() -> None:
    rule, printing = _rule(), _printing()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _event(rule, printing, edition="promo")


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["start_price", "end_price"])
def test_alert_event_anchor_prices_are_non_negative(field: str) -> None:
    rule, printing = _rule(), _printing()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _event(rule, printing, **{field: Decimal("-0.01")})


@pytest.mark.django_db
def test_alert_event_allows_signed_change_fields() -> None:
    """A down-move event has a negative pct_change and dollar_change — no sign CHECK."""
    rule, printing = _rule(direction=Direction.DOWN), _printing()
    event = _event(rule, printing, pct_change=Decimal("-25.00"), dollar_change=Decimal("-5.00"))
    assert event.pct_change == Decimal("-25.00")


@pytest.mark.django_db
def test_alert_run_status_check_rejects_unknown_value() -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AlertRun.objects.create(status="bogus")


# --- the granular idempotency UNIQUE -------------------------------------------------


@pytest.mark.django_db
def test_alert_event_is_unique_per_rule_printing_edition_day() -> None:
    """All four key columns are non-null, so this plain UNIQUE is created AND exercised on
    sqlite (no nulls_distinct Postgres-only apparatus) — the get_or_create idempotency key."""
    rule, printing = _rule(), _printing()
    _event(rule, printing)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _event(rule, printing)  # same (rule, printing, edition, triggered_on)


@pytest.mark.django_db
def test_alert_event_distinct_per_edition_same_day() -> None:
    """The same rule firing on two editions of one printing on one day is two rows (the
    grain is per-(printing, edition), not per-printing)."""
    rule, printing = _rule(), _printing()
    _event(rule, printing, edition=Edition.FIRST_EDITION)
    _event(rule, printing, edition=Edition.UNLIMITED)
    assert AlertEvent.objects.filter(rule=rule, printing=printing).count() == 2


# --- append-only admin (event + run) -------------------------------------------------


@pytest.mark.parametrize("admin_cls,model", [(AlertEventAdmin, AlertEvent), (AlertRunAdmin, AlertRun)])
def test_append_only_admin_blocks_edit_and_delete_of_existing(
    admin_cls: type, model: type
) -> None:
    admin_obj = admin_cls(model, AdminSite())
    request = RequestFactory().get("/")
    existing = model()

    assert admin_obj.has_delete_permission(request) is False
    assert admin_obj.has_delete_permission(request, existing) is False
    assert admin_obj.has_change_permission(request, existing) is False


@pytest.mark.django_db
@pytest.mark.parametrize("admin_cls,model", [(AlertEventAdmin, AlertEvent), (AlertRunAdmin, AlertRun)])
def test_append_only_admin_change_permission_defers_to_user(
    admin_cls: type, model: type
) -> None:
    """The obj=None (model-level) case — which gates the changelist — still defers to the
    user's perms; it must not be hard-coded True (the ValuationRunAdmin precedent)."""
    admin_obj = admin_cls(model, AdminSite())
    request = RequestFactory().get("/")

    request.user = User.objects.create_user("limited", is_staff=True)
    assert admin_obj.has_change_permission(request) is False

    request.user = User.objects.create_superuser("super", "super@example.com", "x")
    assert admin_obj.has_change_permission(request) is True
