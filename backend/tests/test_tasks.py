from __future__ import annotations

import json

import pytest
from celery.schedules import crontab
from django.conf import settings

from apps.cards.sync import SyncResult
from apps.cards.tasks import sync_ygoprodeck_metadata
from apps.pricing.ingestion import IngestResult
from apps.pricing.reconciliation import ReconcileResult
from apps.pricing.tasks import sync_tcgcsv_pricing

# --- CELERY_BEAT_SCHEDULE ---------------------------------------------------


def test_beat_schedule_runs_both_syncs_daily() -> None:
    schedule = settings.CELERY_BEAT_SCHEDULE

    assert set(schedule) == {"ygoprodeck-metadata-daily", "tcgcsv-pricing-daily"}
    meta = schedule["ygoprodeck-metadata-daily"]
    price = schedule["tcgcsv-pricing-daily"]
    assert meta["schedule"] == crontab(hour=2, minute=0)
    assert price["schedule"] == crontab(hour=3, minute=0)
    # Metadata seeds the printings pricing reconciles against, so it must run first.
    assert min(meta["schedule"].hour) < min(price["schedule"].hour)


def test_beat_schedule_task_names_match_registered_tasks() -> None:
    """A scheduled task name that doesn't match a defined task would make beat fail at
    runtime ("task not registered"); pin the names to the actual task objects."""
    scheduled = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}

    assert sync_ygoprodeck_metadata.name == "cards.sync_ygoprodeck_metadata"
    assert sync_tcgcsv_pricing.name == "pricing.sync_tcgcsv_pricing"
    assert scheduled == {sync_ygoprodeck_metadata.name, sync_tcgcsv_pricing.name}


# --- task wrappers delegate to the orchestration ----------------------------


def test_ygoprodeck_task_returns_run_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.cards.tasks.run_ygoprodeck_sync",
        lambda **_kw: SyncResult(cards_created=5, printings_created=9),
    )

    result = sync_ygoprodeck_metadata()

    assert result["cards_created"] == 5
    assert result["printings_created"] == 9
    json.dumps(result)  # the result backend serializes as JSON


def test_tcgcsv_task_returns_json_serializable_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reconcile result carries a frozenset (conflicted_external_ids) that plain
    JSON can't serialize; the task must render it (sorted list) so the result backend
    doesn't choke."""
    rec = ReconcileResult(exact_matched=3, conflicted_external_ids=frozenset({"99"}))
    ing = IngestResult(snapshots_created=3)
    monkeypatch.setattr("apps.pricing.tasks.run_tcgcsv_sync", lambda **_kw: (rec, ing))

    result = sync_tcgcsv_pricing()

    assert result["reconcile"]["exact_matched"] == 3
    assert result["reconcile"]["conflicted_external_ids"] == ["99"]
    assert result["ingest"]["snapshots_created"] == 3
    json.dumps(result)  # must not raise — the unconverted frozenset would


def test_tasks_report_skip_when_orchestration_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the orchestration skips (advisory lock held → None), the task returns a
    JSON-serializable skip marker rather than crashing on None."""
    monkeypatch.setattr("apps.cards.tasks.run_ygoprodeck_sync", lambda **_kw: None)
    monkeypatch.setattr("apps.pricing.tasks.run_tcgcsv_sync", lambda **_kw: None)

    assert sync_ygoprodeck_metadata() == {"skipped": True}
    assert sync_tcgcsv_pricing() == {"skipped": True}
