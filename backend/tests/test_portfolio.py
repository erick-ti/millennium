import pytest
from django.db import IntegrityError, transaction

from apps.portfolio.models import Portfolio


@pytest.mark.django_db
def test_portfolio_name_must_be_unique() -> None:
    """name is unique so a folder resolves to one portfolio. A single-column
    UNIQUE over a non-null column, so this is enforced on sqlite too."""
    Portfolio.objects.create(name="Yubel Deck")

    with pytest.raises(IntegrityError), transaction.atomic():
        Portfolio.objects.create(name="Yubel Deck")


@pytest.mark.django_db
def test_get_or_create_resolves_folder_to_one_portfolio() -> None:
    """The DS-import path (DECISIONS 2026-05-18): Folder Name find-or-creates a
    portfolio by name. A repeat import of the same folder reuses the row."""
    first, created_first = Portfolio.objects.get_or_create(name="Long-term hold")
    second, created_second = Portfolio.objects.get_or_create(name="Long-term hold")

    assert created_first is True
    assert created_second is False
    assert first == second
    assert Portfolio.objects.count() == 1


@pytest.mark.django_db
def test_str_returns_name() -> None:
    assert str(Portfolio.objects.create(name="Trade binder")) == "Trade binder"


def test_name_is_unique() -> None:
    """Intent check that runs on every backend, independent of DB enforcement."""
    assert Portfolio._meta.get_field("name").unique is True
