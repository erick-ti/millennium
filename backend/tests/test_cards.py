import pytest
from django.db import IntegrityError, transaction

from apps.cards.models import Card


@pytest.mark.django_db
def test_passcode_is_unique_when_present() -> None:
    Card.objects.create(passcode=46986414, name="Dark Magician", normalized_name="dark magician")

    with pytest.raises(IntegrityError), transaction.atomic():
        Card.objects.create(
            passcode=46986414,
            name="Dark Magician (alt art)",
            normalized_name="dark magician alt art",
        )


@pytest.mark.django_db
def test_multiple_cards_may_have_null_passcode() -> None:
    """Tokens and other TCGCSV-only entities carry no Konami passcode."""
    Card.objects.create(name="Sheep Token", normalized_name="sheep token")
    Card.objects.create(name="Ojama Token", normalized_name="ojama token")

    assert Card.objects.filter(passcode__isnull=True).count() == 2
