import pytest
from django.db import IntegrityError, transaction

from apps.cards.models import Card
from apps.cards.normalization import normalize_name


@pytest.mark.django_db
def test_passcode_is_unique_when_present() -> None:
    Card.objects.create(passcode=46986414, name="Dark Magician")

    with pytest.raises(IntegrityError), transaction.atomic():
        Card.objects.create(passcode=46986414, name="Dark Magician (alt art)")


@pytest.mark.django_db
def test_multiple_cards_may_have_null_passcode() -> None:
    """Tokens and other TCGCSV-only entities carry no Konami passcode."""
    Card.objects.create(name="Sheep Token")
    Card.objects.create(name="Ojama Token")

    assert Card.objects.filter(passcode__isnull=True).count() == 2


@pytest.mark.django_db
def test_save_derives_normalized_name() -> None:
    card = Card.objects.create(name="The Fallen &amp; The Virtuous")
    assert card.normalized_name == "the fallen & the virtuous"


@pytest.mark.django_db
def test_renaming_updates_normalized_name() -> None:
    card = Card.objects.create(name="Dark Magician")
    card.name = "Dark Magician Girl"
    card.save()
    card.refresh_from_db()
    assert card.normalized_name == "dark magician girl"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Dark Magician", "dark magician"),
        ("The Fallen &amp; The Virtuous", "the fallen & the virtuous"),
        ("Élégant Café", "elegant cafe"),
        ("Maxx  “C”", 'maxx "c"'),
        ("Number 39: Utopia", "number 39: utopia"),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected
