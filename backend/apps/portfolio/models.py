from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class Portfolio(TimeStampedModel):
    """A logical grouping of holdings — the investment-account analogue.

    Dragon Shield's ``Folder Name`` find-or-creates a portfolio by name on
    import (DECISIONS 2026-05-18), so ``name`` is unique: ``get_or_create``
    resolves a folder to exactly one portfolio. This is a single-column UNIQUE
    over a non-null text column, so unlike the ``CardPrinting`` natural key it
    IS created and exercised on sqlite under ``make test``. Distinct from
    ``storage_location`` (physical whereabouts), which a portfolio does not own.

    Name canonicalization (trim / case-fold for matching) is deliberately
    deferred to the Phase 3 DS-import boundary — the single-function approach
    taken for ``set_code`` / ``external_id`` (DECISIONS 2026-05-21), not a
    per-field ``save()`` coercion or CHECK here. That boundary must trim
    ``Folder Name`` before ``get_or_create``, since unique-on-raw-name shares
    ``external_id``'s dirty-alias gap (``"Yubel Deck "`` vs ``"Yubel Deck"``).
    """

    name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
