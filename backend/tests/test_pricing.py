import pytest
from django.db import IntegrityError, models, transaction

from apps.cards.models import Card, CardPrinting
from apps.pricing.models import ExternalPriceId


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
        provider=ExternalPriceId.Provider.TCGCSV,
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
        printing=printing, provider=ExternalPriceId.Provider.TCGCSV, external_id="21747"
    )
    ExternalPriceId.objects.create(
        printing=printing, provider=ExternalPriceId.Provider.TCGCSV, external_id="999999"
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
        printing=first, provider=ExternalPriceId.Provider.TCGCSV, external_id="21747"
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        ExternalPriceId.objects.create(
            printing=second, provider=ExternalPriceId.Provider.TCGCSV, external_id="21747"
        )


@pytest.mark.django_db
def test_deleting_printing_cascades_external_ids() -> None:
    """on_delete=CASCADE — the mapping is meaningless once its printing is gone."""
    card = Card.objects.create(name="Dark Magician")
    printing = _printing(card)
    ExternalPriceId.objects.create(
        printing=printing, provider=ExternalPriceId.Provider.TCGCSV, external_id="592559"
    )

    printing.delete()

    assert ExternalPriceId.objects.count() == 0


@pytest.mark.django_db
def test_str_uses_provider_display() -> None:
    card = Card.objects.create(name="Dark Magician")
    epi = ExternalPriceId.objects.create(
        printing=_printing(card),
        provider=ExternalPriceId.Provider.TCGCSV,
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
