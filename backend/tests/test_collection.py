from datetime import date
from decimal import Decimal

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory

from apps.cards.models import Card, CardPrinting
from apps.collection.admin import CollectionItemAdmin, CollectionLotAdmin
from apps.collection.models import (
    CollectionItem,
    CollectionLot,
    Condition,
    Language,
    StorageLocation,
)
from apps.core.enums import Edition
from apps.portfolio.models import Portfolio

# --- StorageLocation -------------------------------------------------------


@pytest.mark.django_db
def test_storage_location_name_must_be_unique() -> None:
    """name is unique so two physical locations can't share a name. A
    single-column UNIQUE over a non-null column, so enforced on sqlite too."""
    StorageLocation.objects.create(name="Deck box #2")

    with pytest.raises(IntegrityError), transaction.atomic():
        StorageLocation.objects.create(name="Deck box #2")


@pytest.mark.django_db
def test_storage_location_str_returns_name() -> None:
    assert str(StorageLocation.objects.create(name="Safe deposit box")) == "Safe deposit box"


def test_storage_location_name_is_unique() -> None:
    """Intent check that runs on every backend, independent of DB enforcement."""
    assert StorageLocation._meta.get_field("name").unique is True


# --- CollectionItem --------------------------------------------------------


def _printing(card_name: str = "Ash Blossom & Joyous Spring", set_code: str = "L5DD-ENC09") -> CardPrinting:
    card = Card.objects.create(name=card_name)
    return CardPrinting.objects.create(
        card=card, set_code=set_code, set_rarity="Common", set_name="Legendary Decks"
    )


@pytest.mark.django_db
def test_collection_item_natural_key_is_unique() -> None:
    """One holding per (printing, condition, edition, language, portfolio). All
    columns non-null, so this plain UNIQUE is enforced on sqlite too."""
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    fields = dict(
        printing=printing,
        portfolio=portfolio,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )
    CollectionItem.objects.create(**fields)

    with pytest.raises(IntegrityError), transaction.atomic():
        CollectionItem.objects.create(**fields)


@pytest.mark.django_db
def test_differs_by_one_key_field_is_a_distinct_holding() -> None:
    """Same printing/condition/language/portfolio but a different edition is a
    separate holding, not a duplicate — edition is part of the identity."""
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    common = dict(
        printing=printing,
        portfolio=portfolio,
        condition=Condition.NEAR_MINT,
        language=Language.ENGLISH,
    )
    CollectionItem.objects.create(edition=Edition.FIRST_EDITION, **common)
    CollectionItem.objects.create(edition=Edition.UNLIMITED, **common)

    assert CollectionItem.objects.count() == 2


@pytest.mark.django_db
def test_deleting_referenced_printing_is_protected() -> None:
    """printing FK is PROTECT — a stray printing delete must not wipe holdings."""
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    CollectionItem.objects.create(
        printing=printing,
        portfolio=portfolio,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )

    with pytest.raises(ProtectedError):
        printing.delete()


@pytest.mark.django_db
def test_deleting_referenced_portfolio_is_protected() -> None:
    """portfolio FK is PROTECT — deleting a portfolio with holdings is blocked."""
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    CollectionItem.objects.create(
        printing=printing,
        portfolio=portfolio,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )

    with pytest.raises(ProtectedError):
        portfolio.delete()


@pytest.mark.django_db
def test_deleting_storage_location_nulls_the_holding() -> None:
    """storage_location FK is SET_NULL — the holding survives, just unlocated."""
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    location = StorageLocation.objects.create(name="Deck box #2")
    item = CollectionItem.objects.create(
        printing=printing,
        portfolio=portfolio,
        storage_location=location,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )

    location.delete()
    item.refresh_from_db()

    assert item.storage_location is None


@pytest.mark.django_db
def test_storage_location_is_not_part_of_identity() -> None:
    """Intentional scope limit (DECISIONS 2026-05-18): storage_location is a
    holding-level annotation, not part of the natural key — so the SAME holding
    can't be split across two locations; the second insert collides. Per-copy /
    slot placement is deferred to a future allocation layer (after collection_lots,
    which it would reconcile against)."""
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    binder = StorageLocation.objects.create(name="Binder A")
    box = StorageLocation.objects.create(name="Deck box #2")
    common = dict(
        printing=printing,
        portfolio=portfolio,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )
    CollectionItem.objects.create(storage_location=binder, **common)

    with pytest.raises(IntegrityError), transaction.atomic():
        CollectionItem.objects.create(storage_location=box, **common)


@pytest.mark.django_db
def test_collection_item_str() -> None:
    printing = _printing(set_code="L5DD-ENC09")
    portfolio = Portfolio.objects.create(name="Yubel Deck")
    item = CollectionItem.objects.create(
        printing=printing,
        portfolio=portfolio,
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )

    assert str(item) == "L5DD-ENC09 / Common (1st Edition, Near Mint, English) in Yubel Deck"


@pytest.mark.django_db
def test_invalid_condition_rejected_by_db() -> None:
    """choices is form/full_clean() validation, not DB enforcement: .create()
    bypasses full_clean(), so a CHECK is what actually keeps a raw DS value out
    of the natural key. Enforced on sqlite and Postgres alike."""
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        CollectionItem.objects.create(
            printing=printing,
            portfolio=portfolio,
            condition="NearMint",
            edition=Edition.FIRST_EDITION,
            language=Language.ENGLISH,
        )


@pytest.mark.django_db
def test_invalid_edition_rejected_by_db() -> None:
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        CollectionItem.objects.create(
            printing=printing,
            portfolio=portfolio,
            condition=Condition.NEAR_MINT,
            edition="1st Edition",
            language=Language.ENGLISH,
        )


@pytest.mark.django_db
def test_invalid_language_rejected_by_db() -> None:
    printing = _printing()
    portfolio = Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        CollectionItem.objects.create(
            printing=printing,
            portfolio=portfolio,
            condition=Condition.NEAR_MINT,
            edition=Edition.FIRST_EDITION,
            language="English",
        )


def test_collection_item_natural_key_constraint() -> None:
    """Intent check that runs on every backend, independent of DB enforcement."""
    constraint = next(
        c for c in CollectionItem._meta.constraints if isinstance(c, models.UniqueConstraint)
    )

    assert constraint.fields == ("printing", "condition", "edition", "language", "portfolio")


# --- CollectionLot ---------------------------------------------------------


def _collection_item() -> CollectionItem:
    """A holding to hang lots off of."""
    return CollectionItem.objects.create(
        printing=_printing(),
        portfolio=Portfolio.objects.create(name="Yubel Deck"),
        condition=Condition.NEAR_MINT,
        edition=Edition.FIRST_EDITION,
        language=Language.ENGLISH,
    )


@pytest.mark.django_db
def test_lots_sum_to_the_holding_quantity() -> None:
    """A holding's quantity is the SUM of its child lots (it is not a stored
    column), reachable via the `lots` reverse accessor."""
    item = _collection_item()
    CollectionLot.objects.create(collection_item=item, quantity=2, unit_cost=Decimal("4.50"))
    CollectionLot.objects.create(collection_item=item, quantity=1, unit_cost=Decimal("9.00"))

    assert item.lots.aggregate(total=models.Sum("quantity"))["total"] == 3


@pytest.mark.django_db
def test_deleting_holding_cascades_its_lots() -> None:
    """collection_item FK is CASCADE — a lot is part of its holding, so deleting
    the holding takes its acquisition events with it."""
    item = _collection_item()
    CollectionLot.objects.create(collection_item=item, quantity=1)

    item.delete()

    assert CollectionLot.objects.count() == 0


@pytest.mark.django_db
def test_lots_do_not_weaken_upstream_protect() -> None:
    """Cost basis on lots is shielded from accidental loss: even with lots
    present, the holding's printing FK is still PROTECT, so an upstream printing
    delete can't cascade through to wipe the lots (the reason CASCADE here is
    safe — nothing cascades *into* a CollectionItem)."""
    item = _collection_item()
    CollectionLot.objects.create(collection_item=item, quantity=1, unit_cost=Decimal("4.50"))

    with pytest.raises(ProtectedError):
        item.printing.delete()


@pytest.mark.django_db
def test_quantity_zero_rejected_by_db() -> None:
    """CHECK quantity > 0 — PositiveIntegerField only adds a form-layer validator,
    so the DB CHECK is what rejects a zero-copy lot created via .create()
    (enforced on sqlite and Postgres alike)."""
    item = _collection_item()

    with pytest.raises(IntegrityError), transaction.atomic():
        CollectionLot.objects.create(collection_item=item, quantity=0)


@pytest.mark.django_db
def test_negative_unit_cost_rejected_by_db() -> None:
    """CHECK unit_cost IS NULL OR >= 0 — cost basis is never negative."""
    item = _collection_item()

    with pytest.raises(IntegrityError), transaction.atomic():
        CollectionLot.objects.create(collection_item=item, quantity=1, unit_cost=Decimal("-1.00"))


@pytest.mark.django_db
def test_unit_cost_unknown_and_free_are_both_allowed() -> None:
    """NULL unit_cost means "cost unknown"; 0.00 means "free". Both are valid and
    distinct — the reason unit_cost is nullable rather than defaulting to 0."""
    item = _collection_item()
    unknown = CollectionLot.objects.create(collection_item=item, quantity=1, unit_cost=None)
    free = CollectionLot.objects.create(collection_item=item, quantity=1, unit_cost=Decimal("0.00"))

    assert unknown.unit_cost is None
    assert free.unit_cost == Decimal("0.00")


@pytest.mark.django_db
def test_acquired_at_is_optional() -> None:
    """acquired_at is nullable — an acquisition with an unknown date is allowed."""
    item = _collection_item()
    lot = CollectionLot.objects.create(collection_item=item, quantity=1, acquired_at=None)

    assert lot.acquired_at is None


@pytest.mark.django_db
def test_collection_lot_str() -> None:
    item = _collection_item()
    lot = CollectionLot.objects.create(collection_item=item, quantity=3, unit_cost=Decimal("12.50"))

    assert str(lot) == (
        "3 x L5DD-ENC09 / Common (1st Edition, Near Mint, English) in Yubel Deck (12.50 each)"
    )


@pytest.mark.django_db
def test_lot_default_ordering_is_deterministic_and_nulls_last() -> None:
    """Default order is chronological with `id` as a stable same-date tiebreaker and
    unknown-date lots last. nulls_last is explicit so sqlite (NULLs-first by default)
    and Postgres (NULLs-last) agree — otherwise undated lots sort to opposite ends
    per backend and same-date lots come back in arbitrary order."""
    item = _collection_item()
    older = CollectionLot.objects.create(collection_item=item, quantity=1, acquired_at=date(2023, 6, 1))
    same_a = CollectionLot.objects.create(collection_item=item, quantity=1, acquired_at=date(2024, 1, 1))
    same_b = CollectionLot.objects.create(collection_item=item, quantity=1, acquired_at=date(2024, 1, 1))
    undated = CollectionLot.objects.create(collection_item=item, quantity=1, acquired_at=None)

    assert list(item.lots.all()) == [older, same_a, same_b, undated]


def test_collection_admins_disable_bulk_delete() -> None:
    """The bulk "delete selected" action is removed from the holding and lot admins
    so cost-basis history can't be mass-deleted in one click; single-object delete
    (which shows the cascade confirmation) stays available."""
    request = RequestFactory().get("/")
    request.user = User(is_superuser=True, is_active=True)  # superuser short-circuits has_perm; unsaved, no DB
    site = AdminSite()

    item_actions = CollectionItemAdmin(CollectionItem, site).get_actions(request)
    lot_actions = CollectionLotAdmin(CollectionLot, site).get_actions(request)

    assert "delete_selected" not in item_actions
    assert "delete_selected" not in lot_actions
