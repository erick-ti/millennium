import pytest
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, Condition, Language, StorageLocation
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
