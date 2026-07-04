import pytest
from django.db import IntegrityError, models, transaction

from apps.cards.models import Card, CardPrinting
from apps.imports.models import (
    ImportBatch,
    ImportRow,
    ImportStatus,
    MatchConfidence,
    RowStatus,
    SourceFormat,
)

# --- ImportBatch -----------------------------------------------------------


@pytest.mark.django_db
def test_import_batch_defaults_to_pending() -> None:
    batch = ImportBatch.objects.create(
        source_format=SourceFormat.DRAGON_SHIELD, original_filename="yubel.csv"
    )

    assert batch.status == ImportStatus.PENDING
    assert batch.error == ""


@pytest.mark.django_db
def test_import_batch_invalid_source_format_rejected_by_db() -> None:
    """choices is form-layer validation only; .create() bypasses full_clean(), so a
    CHECK is what actually keeps an unknown format out (enforced on sqlite too)."""
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportBatch.objects.create(source_format="tcgplayer", original_filename="x.csv")


@pytest.mark.django_db
def test_import_batch_invalid_status_rejected_by_db() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportBatch.objects.create(
            source_format=SourceFormat.DRAGON_SHIELD,
            original_filename="x.csv",
            status="done",
        )


@pytest.mark.django_db
def test_import_batch_str() -> None:
    batch = ImportBatch.objects.create(
        source_format=SourceFormat.DRAGON_SHIELD, original_filename="yubel.csv"
    )

    assert str(batch) == "Dragon Shield yubel.csv (Pending)"


# --- ImportRow -------------------------------------------------------------


def _batch() -> ImportBatch:
    return ImportBatch.objects.create(
        source_format=SourceFormat.DRAGON_SHIELD, original_filename="yubel.csv"
    )


def _printing() -> CardPrinting:
    card = Card.objects.create(name="Ash Blossom & Joyous Spring")
    return CardPrinting.objects.create(
        card=card, set_code="L5DD-ENC09", set_rarity="Common", set_name="Legendary Decks"
    )


@pytest.mark.django_db
def test_import_row_defaults_to_pending_and_unmatched() -> None:
    """A freshly parsed row carries no match yet: unmatched, pending, no printing,
    and normalized_data NULL until the normalization step populates it."""
    row = ImportRow.objects.create(batch=_batch(), row_number=1, raw_data={"Card Name": "Ash"})

    assert row.status == RowStatus.PENDING
    assert row.match_confidence == MatchConfidence.UNMATCHED
    assert row.matched_printing is None
    assert row.normalized_data is None


@pytest.mark.django_db
def test_import_row_json_columns_round_trip() -> None:
    raw = {"Card Name": "Ash Blossom & Joyous Spring", "Set Code": "L5DD", "LOW": "0.50"}
    normalized = {"set_code": "L5DD-ENC09", "set_rarity": "Common", "quantity": 3}
    row = ImportRow.objects.create(
        batch=_batch(), row_number=1, raw_data=raw, normalized_data=normalized
    )
    row.refresh_from_db()

    assert row.raw_data == raw
    assert row.normalized_data == normalized


@pytest.mark.django_db
def test_import_row_unique_per_batch() -> None:
    """One row per source line within a batch. Both columns non-null, so this plain
    UNIQUE is enforced on sqlite too."""
    batch = _batch()
    ImportRow.objects.create(batch=batch, row_number=1, raw_data={})

    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRow.objects.create(batch=batch, row_number=1, raw_data={})


@pytest.mark.django_db
def test_same_row_number_in_different_batches_is_allowed() -> None:
    """The uniqueness is per batch: line 1 of two different imports don't collide."""
    ImportRow.objects.create(batch=_batch(), row_number=1, raw_data={})
    ImportRow.objects.create(batch=_batch(), row_number=1, raw_data={})

    assert ImportRow.objects.count() == 2


@pytest.mark.django_db
def test_deleting_batch_cascades_its_rows() -> None:
    """batch FK is CASCADE: rows are composition of their batch."""
    batch = _batch()
    ImportRow.objects.create(batch=batch, row_number=1, raw_data={})
    ImportRow.objects.create(batch=batch, row_number=2, raw_data={})

    batch.delete()

    assert ImportRow.objects.count() == 0


@pytest.mark.django_db
def test_deleting_matched_printing_nulls_the_fk_but_not_the_confidence() -> None:
    """matched_printing FK is SET_NULL: a printing delete nulls the staging pointer
    (rather than being blocked or cascading the row away). SET_NULL bypasses save(),
    so match_confidence is deliberately left as-is: matched_printing is the
    authoritative match signal, and a NULL printing means unmatched regardless of the
    stale tier (slices 3-4 honor that; a re-match overwrites both). Pinning this keeps
    the stale tier from being mistaken for a live 'exact' match."""
    printing = _printing()
    row = ImportRow.objects.create(
        batch=_batch(),
        row_number=1,
        raw_data={},
        matched_printing=printing,
        match_confidence=MatchConfidence.EXACT,
    )

    printing.delete()
    row.refresh_from_db()

    assert row.matched_printing is None
    # Only the FK is nulled; the tier is not reset (no save() runs on the SET_NULL
    # path). Consumers key on matched_printing, never this stale value.
    assert row.match_confidence == MatchConfidence.EXACT


@pytest.mark.django_db
def test_import_row_invalid_confidence_rejected_by_db() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRow.objects.create(batch=_batch(), row_number=1, raw_data={}, match_confidence="maybe")


@pytest.mark.django_db
def test_import_row_invalid_status_rejected_by_db() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRow.objects.create(batch=_batch(), row_number=1, raw_data={}, status="reviewing")


@pytest.mark.django_db
def test_import_row_str() -> None:
    batch = _batch()
    row = ImportRow.objects.create(batch=batch, row_number=7, raw_data={})

    assert str(row) == f"Row 7 of batch {batch.pk} (Pending)"


# --- intent checks (run on every backend, independent of DB enforcement) ---


def test_import_row_unique_constraint_fields() -> None:
    constraint = next(
        c for c in ImportRow._meta.constraints if isinstance(c, models.UniqueConstraint)
    )

    assert constraint.fields == ("batch", "row_number")


def test_matched_printing_is_set_null_on_delete() -> None:
    """Intent check: the staging pointer nulls rather than protecting/cascading."""
    field = ImportRow._meta.get_field("matched_printing")
    assert field.remote_field.on_delete is models.SET_NULL


def test_batch_is_cascade_on_delete() -> None:
    field = ImportRow._meta.get_field("batch")
    assert field.remote_field.on_delete is models.CASCADE
