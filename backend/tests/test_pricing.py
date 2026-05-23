from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

from apps.cards.models import Card, CardPrinting
from apps.core.enums import Edition
from apps.pricing.models import ExternalPriceId, PriceSnapshot, Provider


def _printing(card: Card, set_code: str = "LOB-005") -> CardPrinting:
    """Minimal printing scaffold — the set fields are incidental to these tests."""
    return CardPrinting.objects.create(
        card=card,
        set_code=set_code,
        set_rarity="Common",
        set_name="Legend of Blue Eyes White Dragon",
    )


@pytest.mark.django_db
def test_external_price_id_linked_to_printing() -> None:
    card = Card.objects.create(name="Dark Magician")
    printing = _printing(card)
    epi = ExternalPriceId.objects.create(
        printing=printing,
        provider=Provider.TCGCSV,
        external_id="592559",
    )

    assert epi.printing == printing
    assert list(printing.external_price_ids.all()) == [epi]


@pytest.mark.django_db
def test_one_printing_may_have_multiple_external_ids() -> None:
    """(printing, provider) is intentionally not unique: a provider-side
    re-classification can leave one printing resolvable by more than one id."""
    card = Card.objects.create(name="Aqua Madoor")
    printing = _printing(card, set_code="LOB-040")
    ExternalPriceId.objects.create(
        printing=printing, provider=Provider.TCGCSV, external_id="21747"
    )
    ExternalPriceId.objects.create(
        printing=printing, provider=Provider.TCGCSV, external_id="999999"
    )

    assert printing.external_price_ids.count() == 2


@pytest.mark.django_db
def test_external_id_unique_per_provider() -> None:
    """A provider id maps to one printing — two printings can't claim the same
    (provider, external_id). Plain UNIQUE, so this is enforced on sqlite too."""
    card = Card.objects.create(name="Aqua Madoor")
    first = _printing(card, set_code="LOB-040")
    second = _printing(card, set_code="SDK-040")
    ExternalPriceId.objects.create(
        printing=first, provider=Provider.TCGCSV, external_id="21747"
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        ExternalPriceId.objects.create(
            printing=second, provider=Provider.TCGCSV, external_id="21747"
        )


@pytest.mark.django_db
def test_deleting_printing_cascades_external_ids() -> None:
    """on_delete=CASCADE — the mapping is meaningless once its printing is gone."""
    card = Card.objects.create(name="Dark Magician")
    printing = _printing(card)
    ExternalPriceId.objects.create(
        printing=printing, provider=Provider.TCGCSV, external_id="592559"
    )

    printing.delete()

    assert ExternalPriceId.objects.count() == 0


@pytest.mark.django_db
def test_str_uses_provider_display() -> None:
    card = Card.objects.create(name="Dark Magician")
    epi = ExternalPriceId.objects.create(
        printing=_printing(card),
        provider=Provider.TCGCSV,
        external_id="592559",
    )

    assert str(epi) == "TCGCSV:592559"


def test_unique_constraint_on_provider_and_external_id() -> None:
    """Intent check that runs on every backend, independent of DB enforcement."""
    constraint = next(
        c for c in ExternalPriceId._meta.constraints if isinstance(c, models.UniqueConstraint)
    )

    assert constraint.fields == ("provider", "external_id")


def test_printing_provider_index_defined() -> None:
    assert any(index.fields == ["printing", "provider"] for index in ExternalPriceId._meta.indexes)


# --- PriceSnapshot ----------------------------------------------------------


@pytest.mark.django_db
def test_price_snapshot_natural_key_is_unique() -> None:
    """One snapshot per (printing, edition, source, snapshot_date). All non-null,
    so this plain UNIQUE is enforced on sqlite too."""
    printing = _printing(Card.objects.create(name="Pot of Greed"))
    fields = dict(
        printing=printing,
        edition=Edition.FIRST_EDITION,
        source=Provider.TCGCSV,
        snapshot_date=date(2026, 5, 1),
    )
    PriceSnapshot.objects.create(**fields)

    with pytest.raises(IntegrityError), transaction.atomic():
        PriceSnapshot.objects.create(**fields)


@pytest.mark.django_db
def test_snapshot_differing_by_edition_is_distinct() -> None:
    """Edition is a pricing dimension: 1st Edition and Unlimited prices for the
    same printing/source/day are two snapshots, not a duplicate."""
    printing = _printing(Card.objects.create(name="Pot of Greed"))
    common = dict(printing=printing, source=Provider.TCGCSV, snapshot_date=date(2026, 5, 1))
    PriceSnapshot.objects.create(edition=Edition.FIRST_EDITION, **common)
    PriceSnapshot.objects.create(edition=Edition.UNLIMITED, **common)

    assert PriceSnapshot.objects.count() == 2


@pytest.mark.django_db
def test_deleting_priced_printing_is_protected() -> None:
    """printing FK is PROTECT — a price series isn't re-derivable, so a stray
    printing delete must not cascade it away."""
    printing = _printing(Card.objects.create(name="Pot of Greed"))
    PriceSnapshot.objects.create(
        printing=printing,
        edition=Edition.FIRST_EDITION,
        source=Provider.TCGCSV,
        snapshot_date=date(2026, 5, 1),
    )

    with pytest.raises(ProtectedError):
        printing.delete()


@pytest.mark.django_db
def test_invalid_edition_rejected_by_db() -> None:
    """choices is form-layer only; the CHECK is what keeps a raw value out of the key."""
    printing = _printing(Card.objects.create(name="Pot of Greed"))

    with pytest.raises(IntegrityError), transaction.atomic():
        PriceSnapshot.objects.create(
            printing=printing,
            edition="1st Edition",
            source=Provider.TCGCSV,
            snapshot_date=date(2026, 5, 1),
        )


@pytest.mark.django_db
def test_invalid_source_rejected_by_db() -> None:
    printing = _printing(Card.objects.create(name="Pot of Greed"))

    with pytest.raises(IntegrityError), transaction.atomic():
        PriceSnapshot.objects.create(
            printing=printing,
            edition=Edition.FIRST_EDITION,
            source="ebay",
            snapshot_date=date(2026, 5, 1),
        )


@pytest.mark.django_db
def test_prices_optional_and_confidence_defaults_to_one() -> None:
    """Every price point is nullable (a provider may omit some); confidence
    defaults to 1.0 for the single trusted source."""
    printing = _printing(Card.objects.create(name="Pot of Greed"))
    snap = PriceSnapshot.objects.create(
        printing=printing,
        edition=Edition.FIRST_EDITION,
        source=Provider.TCGCSV,
        snapshot_date=date(2026, 5, 1),
    )

    assert snap.market_price is None
    assert snap.low_price is None
    assert snap.confidence == 1.0


@pytest.mark.django_db
def test_snapshots_ordered_latest_first() -> None:
    """Default order is deterministic latest-first within a printing+edition — the
    ordering fields are the unique key (all non-null), so no tiebreaker is needed."""
    printing = _printing(Card.objects.create(name="Pot of Greed"))
    common = dict(printing=printing, edition=Edition.FIRST_EDITION, source=Provider.TCGCSV)
    may = PriceSnapshot.objects.create(snapshot_date=date(2026, 5, 1), **common)
    june = PriceSnapshot.objects.create(snapshot_date=date(2026, 6, 1), **common)

    assert list(printing.price_snapshots.all()) == [june, may]


@pytest.mark.django_db
def test_price_snapshot_str() -> None:
    printing = _printing(Card.objects.create(name="Pot of Greed"), set_code="LOB-119")
    snap = PriceSnapshot.objects.create(
        printing=printing,
        edition=Edition.FIRST_EDITION,
        source=Provider.TCGCSV,
        snapshot_date=date(2026, 5, 1),
        market_price=Decimal("12.50"),
    )

    assert str(snap) == "LOB-119 / Common (1st Edition) TCGCSV 2026-05-01: market 12.50"


def test_price_snapshot_natural_key_constraint() -> None:
    """Intent check that runs on every backend, independent of DB enforcement."""
    constraint = next(
        c for c in PriceSnapshot._meta.constraints if isinstance(c, models.UniqueConstraint)
    )

    assert constraint.fields == ("printing", "edition", "source", "snapshot_date")


def test_price_snapshot_latest_index_defined() -> None:
    assert any(
        index.fields == ["printing", "edition", "-snapshot_date"]
        for index in PriceSnapshot._meta.indexes
    )
