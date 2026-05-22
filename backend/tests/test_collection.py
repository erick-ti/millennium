import pytest
from django.db import IntegrityError, transaction

from apps.collection.models import StorageLocation


@pytest.mark.django_db
def test_storage_location_name_must_be_unique() -> None:
    """name is unique so two physical locations can't share a name. A
    single-column UNIQUE over a non-null column, so enforced on sqlite too."""
    StorageLocation.objects.create(name="Deck box #2")

    with pytest.raises(IntegrityError), transaction.atomic():
        StorageLocation.objects.create(name="Deck box #2")


@pytest.mark.django_db
def test_str_returns_name() -> None:
    assert str(StorageLocation.objects.create(name="Safe deposit box")) == "Safe deposit box"


def test_name_is_unique() -> None:
    """Intent check that runs on every backend, independent of DB enforcement."""
    assert StorageLocation._meta.get_field("name").unique is True
