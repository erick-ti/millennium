from __future__ import annotations

import json

import pytest
from celery.schedules import crontab
from django.conf import settings

from apps.alerts.evaluation import AlertEvaluationResult
from apps.alerts.tasks import compute_alerts
from apps.cards.sync import SyncResult
from apps.cards.tasks import sync_ygoprodeck_metadata
from apps.pricing.ingestion import IngestResult
from apps.pricing.reconciliation import ReconcileResult
from apps.pricing.tasks import sync_tcgcsv_pricing
from apps.valuation.engine import ValuationResult
from apps.valuation.tasks import value_portfolios

# --- CELERY_BEAT_SCHEDULE ---------------------------------------------------


def test_beat_schedule_runs_all_daily_jobs() -> None:
    schedule = settings.CELERY_BEAT_SCHEDULE

    assert set(schedule) == {
        "ygoprodeck-metadata-daily",
        "tcgcsv-pricing-daily",
        "valuation-daily",
        "alerts-daily",
    }
    meta = schedule["ygoprodeck-metadata-daily"]
    price = schedule["tcgcsv-pricing-daily"]
    valuation = schedule["valuation-daily"]
    alerts = schedule["alerts-daily"]
    assert meta["schedule"] == crontab(hour=2, minute=0)
    assert price["schedule"] == crontab(hour=3, minute=0)
    assert valuation["schedule"] == crontab(hour=4, minute=0)
    assert alerts["schedule"] == crontab(hour=5, minute=0)
    # Ordering: metadata seeds the printings pricing reconciles against; valuation reads the
    # prices pricing writes; alerts reads the same prices for its move delta -- so each is
    # slotted after the one it depends on (valuation's + alerts' pricing dependency is also
    # enforced *hard* in run_valuation / run_alerts, not just here).
    assert (
        min(meta["schedule"].hour)
        < min(price["schedule"].hour)
        < min(valuation["schedule"].hour)
        < min(alerts["schedule"].hour)
    )


def test_beat_schedule_task_names_match_registered_tasks() -> None:
    """A scheduled task name that doesn't match a defined task would make beat fail at
    runtime ("task not registered"); pin the names to the actual task objects."""
    scheduled = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}

    assert sync_ygoprodeck_metadata.name == "cards.sync_ygoprodeck_metadata"
    assert sync_tcgcsv_pricing.name == "pricing.sync_tcgcsv_pricing"
    assert value_portfolios.name == "valuation.value_portfolios"
    assert compute_alerts.name == "alerts.compute_alerts"
    assert scheduled == {
        sync_ygoprodeck_metadata.name,
        sync_tcgcsv_pricing.name,
        value_portfolios.name,
        compute_alerts.name,
    }


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
    """When the orchestration skips (advisory lock held → None, or valuation's pricing
    dependency unmet → None), the task returns a JSON-serializable skip marker rather
    than crashing on None. (run_valuation takes no kwargs, unlike the fetch-injecting syncs.)"""
    monkeypatch.setattr("apps.cards.tasks.run_ygoprodeck_sync", lambda **_kw: None)
    monkeypatch.setattr("apps.pricing.tasks.run_tcgcsv_sync", lambda **_kw: None)
    monkeypatch.setattr("apps.valuation.tasks.run_valuation", lambda: None)
    monkeypatch.setattr("apps.alerts.tasks.run_alerts", lambda: None)

    assert sync_ygoprodeck_metadata() == {"skipped": True}
    assert sync_tcgcsv_pricing() == {"skipped": True}
    assert value_portfolios() == {"skipped": True}
    assert compute_alerts() == {"skipped": True}


def test_valuation_task_returns_run_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.valuation.tasks.run_valuation",
        lambda: ValuationResult(portfolios_seen=2, snapshots_created=2, holdings_valued=3),
    )

    result = value_portfolios()

    assert result["portfolios_seen"] == 2
    assert result["snapshots_created"] == 2
    assert result["holdings_valued"] == 3
    json.dumps(result)  # the result backend serializes as JSON


def test_alerts_task_returns_run_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.alerts.tasks.run_alerts",
        lambda: AlertEvaluationResult(rules_evaluated=2, events_created=4, events_existing=1),
    )

    result = compute_alerts()

    assert result["rules_evaluated"] == 2
    assert result["events_created"] == 4
    assert result["events_existing"] == 1
    json.dumps(result)  # the result backend serializes as JSON
