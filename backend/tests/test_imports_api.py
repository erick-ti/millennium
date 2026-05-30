from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.cards.models import Card, CardPrinting
from apps.collection.models import CollectionItem, CollectionLot, Condition, Language
from apps.core.enums import Edition
from apps.imports.models import ImportBatch, ImportRow, ImportStatus, MatchConfidence, RowStatus
from apps.imports.sync import ImportRowNotActionable, approve_row

# --- fixtures -------------------------------------------------------------------


@pytest.fixture
def client() -> APIClient:
    """An authenticated APIClient — every imports endpoint requires auth (the DRF default;
    the schema/docs are gated the same way per Invariant 7)."""
    user = get_user_model().objects.create_user("reviewer", "r@example.com", "x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _printing(
    *,
    name: str = "Ash Blossom & Joyous Spring",
    set_code: str = "L5DD-ENC09",
    set_rarity: str = "Common",
    is_multi_variant: bool = False,
) -> CardPrinting:
    card = Card.objects.create(name=name)
    return CardPrinting.objects.create(
        card=card,
        set_code=set_code,
        set_rarity=set_rarity,
        set_name="Some Set",
        is_multi_variant=is_multi_variant,
    )


def _normalized(**overrides: Any) -> dict[str, Any]:
    """A clean ``normalized_data`` dict (the slice-2 shape, JSON-native) that ``_materialize``
    can consume — every identity field present, cost/date as strings."""
    data: dict[str, Any] = {
        "portfolio_name": "Yubel Deck",
        "set_code": "L5DD-ENC09",
        "set_rarity": "Common",
        "card_name": "Ash Blossom & Joyous Spring",
        "variant_label": None,
        "condition": Condition.NEAR_MINT.value,
        "edition": Edition.FIRST_EDITION.value,
        "language": Language.ENGLISH.value,
        "quantity": 3,
        "unit_cost": "0.68",
        "acquired_at": "2024-01-15",
    }
    data.update(overrides)
    return data


def _batch(status: ImportStatus = ImportStatus.REVIEW) -> ImportBatch:
    return ImportBatch.objects.create(
        source_format="dragon_shield", original_filename="ds.csv", status=status
    )


def _row(
    batch: ImportBatch,
    *,
    row_number: int = 1,
    status: RowStatus = RowStatus.PENDING,
    confidence: MatchConfidence = MatchConfidence.EXACT,
    printing: CardPrinting | None = None,
    normalized: dict[str, Any] | None = None,
) -> ImportRow:
    return ImportRow.objects.create(
        batch=batch,
        row_number=row_number,
        raw_data={},
        normalized_data=_normalized() if normalized is None else normalized,
        matched_printing=printing,
        match_confidence=confidence,
        status=status,
    )


def _approve(client: APIClient, row: ImportRow) -> Any:
    return client.post(reverse("imports:importrow-approve", args=[row.pk]))


def _reject(client: APIClient, row: ImportRow) -> Any:
    return client.post(reverse("imports:importrow-reject", args=[row.pk]))


def _override(client: APIClient, row: ImportRow, printing_id: int) -> Any:
    return client.post(
        reverse("imports:importrow-override", args=[row.pk]), {"printing": printing_id}
    )


# --- auth -----------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_require_authentication() -> None:
    """No anonymous access — the import data + the OpenAPI schema describing it are
    reconnaissance material for a private app (Invariant 7's posture, here on the data)."""
    anon = APIClient()
    assert anon.get(reverse("imports:importbatch-list")).status_code == status.HTTP_403_FORBIDDEN
    assert anon.get(reverse("imports:importrow-list")).status_code == status.HTTP_403_FORBIDDEN
    assert (
        anon.post(reverse("imports:importrow-approve", args=[1])).status_code
        == status.HTTP_403_FORBIDDEN
    )


# --- list / filter / retrieve ---------------------------------------------------


@pytest.mark.django_db
def test_batch_list_carries_derived_row_counts(client: APIClient) -> None:
    batch = _batch()
    printing = _printing()
    _row(batch, row_number=1, status=RowStatus.MATERIALIZED, printing=printing)
    _row(batch, row_number=2, status=RowStatus.PENDING, confidence=MatchConfidence.MEDIUM,
         printing=printing)
    _row(batch, row_number=3, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT,
         printing=printing)
    _row(batch, row_number=4, status=RowStatus.ERROR, confidence=MatchConfidence.UNMATCHED)

    resp = client.get(reverse("imports:importbatch-list"))

    assert resp.status_code == status.HTTP_200_OK
    (data,) = resp.data["results"]
    assert data["rows_total"] == 4
    assert data["rows_materialized"] == 1
    assert data["rows_pending"] == 2
    assert data["rows_error"] == 1
    # needs_review counts every still-PENDING row (round 2): the MEDIUM *and* the gate-held EXACT.
    assert data["rows_needs_review"] == 2


@pytest.mark.django_db
def test_row_list_filters(client: APIClient) -> None:
    batch = _batch()
    printing = _printing()
    materialized = _row(batch, row_number=1, status=RowStatus.MATERIALIZED, printing=printing)
    medium = _row(batch, row_number=2, status=RowStatus.PENDING,
                  confidence=MatchConfidence.MEDIUM, printing=printing)
    exact = _row(batch, row_number=3, status=RowStatus.PENDING,
                 confidence=MatchConfidence.EXACT, printing=printing)
    url = reverse("imports:importrow-list")

    def ids(resp: Any) -> set[int]:
        return {r["id"] for r in resp.data["results"]}

    assert ids(client.get(url, {"batch": batch.pk})) == {materialized.pk, medium.pk, exact.pk}
    assert ids(client.get(url, {"status": RowStatus.PENDING.value})) == {medium.pk, exact.pk}
    assert ids(client.get(url, {"match_confidence": MatchConfidence.MEDIUM.value})) == {medium.pk}
    # needs_review == still-PENDING (round 2): both the MEDIUM and the gate-held EXACT row.
    assert ids(client.get(url, {"needs_review": "true"})) == {medium.pk, exact.pk}
    assert ids(client.get(url, {"needs_review": "false"})) == {materialized.pk}


@pytest.mark.django_db
def test_row_list_rejects_invalid_filter_value(client: APIClient) -> None:
    resp = client.get(reverse("imports:importrow-list"), {"status": "bogus"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_row_detail_nests_printing_with_multi_variant_flag(client: APIClient) -> None:
    batch = _batch()
    printing = _printing(is_multi_variant=True)
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.MEDIUM, printing=printing)

    resp = client.get(reverse("imports:importrow-detail", args=[row.pk]))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["matched_printing"]["is_multi_variant"] is True
    assert resp.data["matched_printing"]["card_name"] == "Ash Blossom & Joyous Spring"
    assert resp.data["needs_review"] is True


# --- approve: overrides the freshness gate (Fork B) -----------------------------


@pytest.mark.django_db
def test_approve_materializes_without_any_reconciliation(client: APIClient) -> None:
    """The headline slice-5 decision: a human approval overrides the auto-materialization
    freshness gate. No TCGCSV reconciliation is recorded here, so run_import would have STAGED
    this EXACT row — but an explicit approval materializes it (DECISIONS 2026-05-27)."""
    batch = _batch()
    printing = _printing()
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT, printing=printing)

    resp = _approve(client, row)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["status"] == RowStatus.MATERIALIZED.value
    item = CollectionItem.objects.get(printing=printing)
    lot = item.lots.get()
    assert lot.import_source_ref == f"dragon_shield:item:{item.pk}"
    row.refresh_from_db()
    assert row.status == RowStatus.MATERIALIZED
    # last open row resolved -> batch auto-completes.
    batch.refresh_from_db()
    assert batch.status == ImportStatus.COMPLETED


@pytest.mark.django_db
def test_approve_medium_name_mismatch_materializes(client: APIClient) -> None:
    """A MEDIUM row (printing found, card name disagreed) is the reviewer's to accept — approve
    materializes the best candidate."""
    batch = _batch()
    printing = _printing(name="A Different Card")
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.MEDIUM, printing=printing)

    resp = _approve(client, row)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["status"] == RowStatus.MATERIALIZED.value
    assert CollectionItem.objects.filter(printing=printing).exists()


@pytest.mark.django_db
def test_approve_multi_variant_placeholder_materializes(client: APIClient) -> None:
    """A known multi-variant placeholder is downgraded to MEDIUM by the matcher (never
    auto-materialized), but a reviewer can still approve it — accepting the generic placeholder
    (v1 has no per-variant rows to pick). The flag is surfaced, not a hard block (Fork B)."""
    batch = _batch()
    printing = _printing(is_multi_variant=True)
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.MEDIUM, printing=printing)

    resp = _approve(client, row)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["status"] == RowStatus.MATERIALIZED.value
    assert CollectionLot.objects.count() == 1


@pytest.mark.django_db
def test_approve_unmatched_without_printing_is_400(client: APIClient) -> None:
    batch = _batch()
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.UNMATCHED, printing=None)

    resp = _approve(client, row)

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "override" in resp.data["detail"].lower()
    assert CollectionLot.objects.count() == 0


@pytest.mark.django_db
def test_approve_on_non_pending_row_is_400(client: APIClient) -> None:
    # REVIEW batch (passes the batch-phase guard) so this isolates the row-status guard.
    batch = _batch()
    printing = _printing()
    row = _row(batch, status=RowStatus.MATERIALIZED, printing=printing)

    assert _approve(client, row).status_code == status.HTTP_400_BAD_REQUEST
    assert _reject(client, row).status_code == status.HTTP_400_BAD_REQUEST
    assert _override(client, row, printing.pk).status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
@pytest.mark.parametrize("batch_status", [ImportStatus.PROCESSING, ImportStatus.FAILED])
def test_actions_refused_on_a_non_review_batch(
    client: APIClient, batch_status: ImportStatus
) -> None:
    """A PENDING row can live in a PROCESSING batch (run_import commits rows before it finalizes)
    or a FAILED batch (a partial run's committed leftovers — a reachable steady state, no race
    needed). Review actions must refuse it: only a REVIEW batch is reviewable — PROCESSING is
    run_import's to finalize, FAILED should be re-imported (Codex adversarial review 2026-05-27
    round 3). Without the guard the action would mutate the row while the batch stayed
    PROCESSING/FAILED (the recompute no-ops there), desyncing the audit trail."""
    batch = _batch(status=batch_status)
    printing = _printing()
    other = _printing(name="Other Card", set_code="OTH-EN001")
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT, printing=printing)

    assert _approve(client, row).status_code == status.HTTP_400_BAD_REQUEST
    assert _reject(client, row).status_code == status.HTTP_400_BAD_REQUEST
    assert _override(client, row, other.pk).status_code == status.HTTP_400_BAD_REQUEST

    # Nothing mutated: no collection writes, the row unchanged (incl. its printing), batch as-was.
    assert CollectionLot.objects.count() == 0
    assert CollectionItem.objects.count() == 0
    row.refresh_from_db()
    assert row.status == RowStatus.PENDING
    assert row.matched_printing_id == printing.pk  # override refused, not applied
    batch.refresh_from_db()
    assert batch.status == batch_status


# --- approve: dedup outcomes (SKIPPED / changed-duplicate conflict) --------------


@pytest.mark.django_db
def test_approve_unchanged_duplicate_skips(client: APIClient) -> None:
    batch = _batch()
    printing = _printing()
    # Both rows exist up front (as run_import stages them), so the batch stays REVIEW while one
    # is still pending — the realistic review workflow. An identical second row for the same
    # holding (same normalized identity) -> dedup SKIP.
    first = _row(batch, row_number=1, status=RowStatus.PENDING,
                 confidence=MatchConfidence.EXACT, printing=printing)
    second = _row(batch, row_number=2, status=RowStatus.PENDING,
                  confidence=MatchConfidence.EXACT, printing=printing)
    _approve(client, first)

    resp = _approve(client, second)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["status"] == RowStatus.SKIPPED.value
    assert CollectionLot.objects.count() == 1  # no duplicate lot


@pytest.mark.django_db
def test_approve_changed_duplicate_is_409_and_leaves_lot_untouched(client: APIClient) -> None:
    """Approving a row whose holding was already imported with a different quantity/cost/date
    surfaces a 409 conflict: the row stays PENDING and the existing lot is NOT overwritten
    (overwriting historical cost basis is out of v1 scope; the human decides)."""
    batch = _batch()
    printing = _printing()
    # Both rows staged up front (batch stays REVIEW while one is pending — the real workflow).
    first = _row(batch, row_number=1, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT,
                 printing=printing, normalized=_normalized(quantity=3))
    changed = _row(batch, row_number=2, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT,
                   printing=printing, normalized=_normalized(quantity=5))
    _approve(client, first)

    resp = _approve(client, changed)

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert resp.data["status"] == RowStatus.PENDING.value
    lot = CollectionLot.objects.get()
    assert lot.quantity == 3  # original kept, not overwritten
    changed.refresh_from_db()
    assert changed.status == RowStatus.PENDING


@pytest.mark.django_db
def test_changed_duplicate_conflict_is_surfaced_in_the_review_queue(client: APIClient) -> None:
    """A changed-duplicate conflict is PENDING *with* match_confidence=EXACT, yet genuinely needs
    a human decision (re-approving just 409s). It must appear in the review surface — a
    `PENDING && != EXACT` rule would hide it, leaving the batch in REVIEW with rows_needs_review=0
    (Codex adversarial review 2026-05-27, round 2)."""
    batch = _batch()
    printing = _printing()
    # Both rows staged up front (batch stays REVIEW while one is pending — the real workflow).
    first = _row(batch, row_number=1, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT,
                 printing=printing, normalized=_normalized(quantity=3))
    conflict = _row(batch, row_number=2, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT,
                    printing=printing, normalized=_normalized(quantity=5))
    _approve(client, first)
    assert _approve(client, conflict).status_code == status.HTTP_409_CONFLICT

    conflict.refresh_from_db()
    assert (conflict.status, conflict.match_confidence) == (
        RowStatus.PENDING,
        MatchConfidence.EXACT,
    )

    # ...and it is visible everywhere the review surface looks: the row filter, the per-row flag,
    # and the batch count (all == still-PENDING now, not the old sub-EXACT subset).
    review = client.get(reverse("imports:importrow-list"), {"needs_review": "true"})
    assert conflict.pk in {r["id"] for r in review.data["results"]}
    detail = client.get(reverse("imports:importrow-detail", args=[conflict.pk]))
    assert detail.data["needs_review"] is True
    (batch_data,) = client.get(reverse("imports:importbatch-list")).data["results"]
    assert batch_data["rows_needs_review"] == 1  # the conflict; was 0 before round 2


# --- override -------------------------------------------------------------------


@pytest.mark.django_db
def test_override_then_approve_materializes(client: APIClient) -> None:
    """An UNMATCHED row has no printing to approve; override picks one (row stays PENDING),
    then approve materializes it. match_confidence is left at the matcher's verdict — override
    keys the workflow off the now-present matched_printing, not a forged tier."""
    batch = _batch()
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.UNMATCHED, printing=None)
    chosen = _printing()

    override_resp = _override(client, row, chosen.pk)

    assert override_resp.status_code == status.HTTP_200_OK
    assert override_resp.data["matched_printing"]["id"] == chosen.pk
    assert override_resp.data["status"] == RowStatus.PENDING.value
    assert override_resp.data["match_confidence"] == MatchConfidence.UNMATCHED.value

    approve_resp = _approve(client, row)

    assert approve_resp.status_code == status.HTTP_200_OK
    assert approve_resp.data["status"] == RowStatus.MATERIALIZED.value
    assert CollectionItem.objects.filter(printing=chosen).exists()


@pytest.mark.django_db
def test_override_with_unknown_printing_is_400(client: APIClient) -> None:
    batch = _batch()
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.UNMATCHED, printing=None)

    resp = _override(client, row, 999_999)

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "printing" in resp.data


# --- reject + batch-status recompute --------------------------------------------


@pytest.mark.django_db
def test_reject_marks_skipped_and_completes_batch(client: APIClient) -> None:
    batch = _batch()
    printing = _printing()
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.MEDIUM, printing=printing)

    resp = _reject(client, row)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["status"] == RowStatus.SKIPPED.value
    assert CollectionLot.objects.count() == 0
    batch.refresh_from_db()
    assert batch.status == ImportStatus.COMPLETED


@pytest.mark.django_db
def test_batch_stays_in_review_while_an_error_row_remains(client: APIClient) -> None:
    """An ERROR row is terminal (fix the source + re-import); it keeps the batch in REVIEW even
    after every PENDING row is resolved — _recompute_batch_status mirrors run_import (PENDING
    OR ERROR -> REVIEW)."""
    batch = _batch()
    printing = _printing()
    pending = _row(batch, row_number=1, status=RowStatus.PENDING,
                   confidence=MatchConfidence.MEDIUM, printing=printing)
    _row(batch, row_number=2, status=RowStatus.ERROR, confidence=MatchConfidence.UNMATCHED)

    _reject(client, pending)

    batch.refresh_from_db()
    assert batch.status == ImportStatus.REVIEW


# --- concurrency: reload + re-check under lock (Codex adversarial review 2026-05-27) --------
# The actions reload the row under a lock and re-check status on the FRESH instance, so a stale
# instance fetched while PENDING can't act after a concurrent action resolved/changed the row.
# A real two-connection race can't run deterministically on sqlite (writes serialize, the lock
# no-ops); these prove the re-check by mutating the DB row after the "caller" fetched it — the
# exact stale-instance condition the lock+re-check defeats. Called directly (no HTTP) so the
# stale instance is explicit.


@pytest.mark.django_db
def test_approve_rechecks_status_under_lock_against_a_stale_instance() -> None:
    batch = _batch()
    printing = _printing()
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT, printing=printing)
    stale = ImportRow.objects.get(pk=row.pk)  # the request's fetched instance (PENDING)
    # A concurrent action resolved the same row after that fetch (bypasses save, like a committed
    # sibling transaction would look on reload):
    ImportRow.objects.filter(pk=row.pk).update(status=RowStatus.SKIPPED)

    with pytest.raises(ImportRowNotActionable):
        approve_row(stale)

    assert CollectionLot.objects.count() == 0  # the stale PENDING instance did not materialize


@pytest.mark.django_db
def test_approve_materializes_the_current_printing_not_a_stale_override() -> None:
    """A stale approve must not clobber a concurrent override: approve reloads under the lock and
    materializes the row's *current* matched_printing, not the one the caller fetched."""
    batch = _batch()
    p_old = _printing(name="Old Card", set_code="AAA-EN001")
    p_new = _printing(name="New Card", set_code="BBB-EN002")
    row = _row(batch, status=RowStatus.PENDING, confidence=MatchConfidence.EXACT, printing=p_old)
    stale = ImportRow.objects.select_related("matched_printing").get(pk=row.pk)  # fetched w/ p_old
    ImportRow.objects.filter(pk=row.pk).update(matched_printing=p_new)  # concurrent override -> p_new

    fresh, outcome = approve_row(stale)

    assert outcome == RowStatus.MATERIALIZED
    assert CollectionItem.objects.filter(printing=p_new).exists()
    assert not CollectionItem.objects.filter(printing=p_old).exists()
    assert fresh.matched_printing_id == p_new.pk


# --- upload (slice 6: POST /api/imports/batches/) -------------------------------

_DS_HEADER = (
    "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,"
    "Rarity,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET"
)
_DS_ROW = (
    "Yubel Deck,3,0,Ash Blossom & Joyous Spring,L5DD,\"Legendary 5D's Decks\","
    "L5DD-ENC09,C,NearMint,1st Edition,English,0.68,2024-01-15,0.50,0.60,0.68"
)


def _ds_upload(name: str = "collection.csv") -> SimpleUploadedFile:
    # utf-8-sig encoding prepends a BOM (Excel "CSV UTF-8" saves do this) so the upload
    # exercises the view's utf-8-sig decode, not just plain utf-8.
    body = f'"sep=,"\n{_DS_HEADER}\n{_DS_ROW}\n'.encode("utf-8-sig")
    return SimpleUploadedFile(name, body, content_type="text/csv")


@pytest.mark.django_db
def test_upload_runs_import_and_returns_batch_with_counts(client: APIClient) -> None:
    """A valid DS CSV is parsed, staged, and returned as the created batch with derived
    counts. No printings/reconciliation exist, so the lone row is UNMATCHED → PENDING →
    the batch lands in REVIEW (not COMPLETED)."""
    resp = client.post(
        reverse("imports:importbatch-list"), {"file": _ds_upload()}, format="multipart"
    )

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.data["status"] == ImportStatus.REVIEW.value
    assert resp.data["original_filename"] == "collection.csv"
    assert resp.data["rows_total"] == 1
    assert resp.data["rows_pending"] == 1
    assert resp.data["rows_needs_review"] == 1
    assert ImportBatch.objects.count() == 1
    assert ImportRow.objects.count() == 1


@pytest.mark.django_db
def test_upload_of_non_dragon_shield_file_records_a_failed_batch(client: APIClient) -> None:
    """A file that isn't a DS export is a recorded outcome, not a request error: run_import
    writes a FAILED batch (durable history), so the upload returns 201 with status=failed and
    the UI branches on it — rather than discarding the attempt with a 4xx."""
    bad = SimpleUploadedFile("notes.csv", b"alpha,beta\n1,2\n", content_type="text/csv")

    resp = client.post(reverse("imports:importbatch-list"), {"file": bad}, format="multipart")

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.data["status"] == ImportStatus.FAILED.value
    assert resp.data["rows_total"] == 0


@pytest.mark.django_db
def test_upload_without_a_file_is_400(client: APIClient) -> None:
    resp = client.post(reverse("imports:importbatch-list"), {}, format="multipart")

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "file" in resp.data
    assert ImportBatch.objects.count() == 0


@pytest.mark.django_db
def test_upload_over_the_size_cap_is_400(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oversized files are rejected before run_import runs (bounding sync request time).
    Patch the cap tiny so a normal fixture exceeds it without allocating megabytes."""
    monkeypatch.setattr("apps.imports.views.MAX_UPLOAD_BYTES", 8)

    resp = client.post(
        reverse("imports:importbatch-list"), {"file": _ds_upload()}, format="multipart"
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "file" in resp.data
    assert ImportBatch.objects.count() == 0


@pytest.mark.django_db
def test_upload_over_the_row_cap_is_400(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows are bounded (not just bytes) so a pathologically large export can't outlast the
    request worker mid-import. Patch the cap to 1 so the fixture (sep hint + header + a data
    row) exceeds it, and assert no batch is written."""
    monkeypatch.setattr("apps.imports.views.MAX_UPLOAD_ROWS", 1)

    resp = client.post(
        reverse("imports:importbatch-list"), {"file": _ds_upload()}, format="multipart"
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "file" in resp.data
    assert ImportBatch.objects.count() == 0


@pytest.mark.django_db
def test_upload_requires_authentication() -> None:
    anon = APIClient()
    resp = anon.post(
        reverse("imports:importbatch-list"), {"file": _ds_upload()}, format="multipart"
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert ImportBatch.objects.count() == 0


@pytest.mark.django_db
def test_upload_truncates_an_overlong_filename(client: APIClient) -> None:
    """A crafted >255-char filename must not 500 (original_filename is CharField(255); on
    Postgres an unbounded name would DataError inside run_import before the audit row exists).
    The view truncates at the import boundary, so the batch is recorded with a <=255 name.
    Truncation is in app code (not the DB column), so this holds on sqlite too."""
    long_name = "A" * 300 + ".csv"
    upload = _ds_upload(long_name)

    resp = client.post(reverse("imports:importbatch-list"), {"file": upload}, format="multipart")

    assert resp.status_code == status.HTTP_201_CREATED
    assert len(resp.data["original_filename"]) <= 255
