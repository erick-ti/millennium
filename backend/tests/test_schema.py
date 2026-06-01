"""Schema-validation gate: ``drf-spectacular`` must generate a clean OpenAPI schema
for the live URL tree, with no warnings. The frontend client (`@hey-api/openapi-ts`)
is generated from this schema (DECISIONS 2026-05-27 Phase 4 slice 1), so a warning
here would land in the generated client — a real regression, not a soft signal."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.core.management import call_command
from django.db import connection
from drf_spectacular.generators import SchemaGenerator
from rest_framework import serializers

# The committed frontend/openapi.json is snapshotted under config.settings.test_postgres
# (prod's backend). drf-spectacular derives a field's integer maximum/minimum/format from
# connection.ops.integer_field_range: sqlite reports int64 for EVERY integer, postgres
# reports true per-type ranges (smallint/int/bigint). So a sqlite-generated live schema
# can't equal the postgres-generated snapshot — gate the exact-equality check on postgres
# (the prod backend), the same posture nulls_distinct / advisory-lock tests take
# (CLAUDE.md; DECISIONS 2026-05-27 slice 2 round 8). Nullability / clean-schema checks are
# backend-independent and run everywhere.
postgres_only = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="integer field bounds in the schema are Postgres-specific; snapshot is generated under postgres",
)


@pytest.mark.django_db
def test_spectacular_generates_clean_schema() -> None:
    """Run ``manage.py spectacular --validate --fail-on-warn`` in-process. The
    command raises on validation errors or warnings (e.g. an un-typed
    ``SerializerMethodField`` would emit a warning that drf-spectacular elevates
    to an exception under ``--fail-on-warn``)."""
    out = StringIO()
    err = StringIO()
    # Discard the generated schema (we just want the validation pass). stderr
    # captures warnings that --fail-on-warn raised.
    call_command(
        "spectacular",
        "--validate",
        "--fail-on-warn",
        "--file=/dev/null",
        stdout=out,
        stderr=err,
    )


def _generate_schema() -> dict[str, Any]:
    """Build the OpenAPI schema in-process. Returns the same dict the
    ``spectacular`` command would write to ``frontend/openapi.json`` — but
    asserting against the LIVE URL conf, not the (potentially stale) committed
    snapshot, so a regression here fails before the snapshot is regenerated.

    drf-spectacular ships no type stubs (the ``test_settings.py`` CSRF test
    treats Django's private `_origin_verified` the same way); these calls
    return ``Any`` at the type-check layer but a fully-typed dict at runtime.
    """
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    schema: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return schema


def _is_nullable(field_schema: dict[str, Any]) -> bool:
    """True if the schema fragment is nullable in OAS 3.0 (`nullable: true`) OR
    OAS 3.1 form (`oneOf`/`anyOf` includes `{type: "null"}`). Accept both so a
    future drf-spectacular upgrade from 3.0 to 3.1 doesn't silently re-break
    this assertion."""
    if field_schema.get("nullable") is True:
        return True
    for shape in field_schema.get("oneOf", []) + field_schema.get("anyOf", []):
        if shape.get("type") == "null":
            return True
    return False


@postgres_only
def test_committed_openapi_snapshot_matches_live_schema() -> None:
    """The committed ``frontend/openapi.json`` is what the TS client is
    generated from (DECISIONS 2026-05-27 Phase 4 slice 2). A serializer / view
    change that doesn't trigger ``make frontend-snapshot-schema`` drifts the
    snapshot from the live schema — backend tests + frontend build both still
    pass while slice 3+ UI compiles against the stale contract.

    This gates the schema → snapshot half of drift coverage. The client →
    snapshot half is gated by the snapshot→client step in the required
    ``tests.yml`` job (regenerate the client + ``git status --porcelain`` on
    ``src/lib/api/generated/``). Both gates exist because they cover orthogonal
    failure modes: a dev who refreshes the snapshot but forgets
    ``make frontend-gen-api`` slips past this test but is caught by that step.

    Postgres-only: the snapshot is generated under ``test_postgres`` (prod's
    backend) for correct integer field bounds; on sqlite the live schema would
    report int64 for every integer and never match (round 8). The required CI
    ``tests`` job runs on postgres, so the gate is enforced there; ``make test``
    (sqlite) skips it. Codex adversarial review of slice 2, round 2 (+ round 8),
    2026-05-27.
    """
    live = _generate_schema()
    snapshot_path = Path(settings.BASE_DIR).parent / "frontend" / "openapi.json"
    snapshot = json.loads(snapshot_path.read_text())
    assert live == snapshot, (
        "frontend/openapi.json is stale relative to the live schema — run "
        "`make frontend-snapshot-schema && make frontend-gen-api` to refresh "
        "the committed contract, then commit the diff."
    )


def _iter_modelserializers_in_local_apps() -> Iterator[type[serializers.ModelSerializer[Any]]]:
    """Yield every ``ModelSerializer`` subclass defined in this project's apps.
    Imports each app's ``serializers`` module first so subclass discovery is
    deterministic regardless of which other modules pytest has loaded by the
    time this test runs."""
    for app_config in apps.get_app_configs():
        if not app_config.name.startswith("apps."):
            continue
        try:
            importlib.import_module(f"{app_config.name}.serializers")
        except ModuleNotFoundError:
            continue

    seen: set[type[Any]] = set()

    def walk(cls: type[Any]) -> Iterator[type[serializers.ModelSerializer[Any]]]:
        for sub in cls.__subclasses__():
            if sub in seen:
                continue
            seen.add(sub)
            # Only yield serializers defined in this project (skip DRF / 3rd-party
            # subclasses that the import chain might pull in).
            if sub.__module__.startswith("apps."):
                yield sub
            yield from walk(sub)

    yield from walk(serializers.ModelSerializer)


def _component_name(serializer_cls: type[serializers.ModelSerializer[Any]]) -> str:
    """drf-spectacular component naming convention: strip the ``Serializer``
    suffix. ``ImportRowSerializer`` → ``ImportRow``, ``CardListSerializer`` →
    ``CardList``."""
    name = serializer_cls.__name__
    return name.removesuffix("Serializer")


def test_explicit_serializer_fields_on_nullable_model_fields_carry_allow_null() -> None:
    """Class-level gate for the bug class Codex caught in rounds 1 + 5: an
    EXPLICITLY-declared serializer field that backs a nullable model field
    defaults to a non-null OpenAPI contract unless the declaration carries
    ``allow_null=True``. ``ModelSerializer``'s auto-derivation reads
    ``field.null`` from the model and handles IMPLICIT fields correctly; only
    the human-overridden EXPLICIT declarations (nested serializers, computed
    fields with ``source=``) drop the nullability signal.

    The check walks every project ``ModelSerializer`` subclass and, for each
    explicitly-declared field whose name maps 1:1 to a same-named field on
    ``Meta.model``, asserts the schema component property is nullable iff the
    model field is. Edge cases (``source="related.attr"``, computed fields
    with no model backing, ``SerializerMethodField``) are skipped by the
    ``FieldDoesNotExist`` branch — they fall through to per-site coverage
    (``test_portfolio_latest_snapshot_is_nullable_in_schema`` for the
    ``SerializerMethodField`` class) or are unaffected by this bug pattern.

    Anchored on STABLE APIs only: Django's ``model._meta.get_field()`` (stable
    public API since Django 1.8) and the live schema dict. The R1 follow-up
    rejected an automated gate as "introspection is brittle" — that concern
    was valid for a drf-spectacular-internals path, NOT for a
    ``model._meta``-anchored path. Sidesteps the brittleness entirely.
    See DECISIONS 2026-05-27 round 5 (the R1 rejection is superseded).
    """
    schema = _generate_schema()
    components = schema["components"]["schemas"]

    failures: list[str] = []
    for serializer_cls in _iter_modelserializers_in_local_apps():
        component = components.get(_component_name(serializer_cls))
        if component is None:
            # Serializer isn't surfaced as a schema component (e.g. internal
            # helper, or only used in a writable variant). The committed-client
            # strategy only enforces contracts on what the schema actually
            # publishes; skip.
            continue
        properties: dict[str, Any] = component.get("properties", {})
        try:
            model = serializer_cls.Meta.model
        except AttributeError:
            continue

        for field_name in serializer_cls._declared_fields:
            try:
                model_field = model._meta.get_field(field_name)  # type: ignore[attr-defined]
            except FieldDoesNotExist:
                # No 1:1 model-field mapping (source="related.attr", a computed
                # field, a SerializerMethodField, or an annotation). Skip; these
                # are covered by per-site tests or are unaffected by the
                # nullable-model-field bug class entirely.
                continue
            # Skip collection-shaped relations (reverse FKs + M2M): the schema
            # property is an array type, always present (possibly empty), NOT
            # nullable in the single-value sense. Django marks reverse FKs as
            # `null=True` in metadata, but the serializer field is `many=True`
            # and the TS type is `T[]`, not `T | null` — so this whole check
            # doesn't apply. Skipping these is what keeps the auto-mapping
            # accurate (the brittleness Feedback 1 warned about).
            if getattr(model_field, "one_to_many", False) or getattr(
                model_field, "many_to_many", False
            ):
                continue
            if not getattr(model_field, "null", False):
                continue
            prop = properties.get(field_name)
            if prop is None or not _is_nullable(prop):
                failures.append(
                    f"{serializer_cls.__module__}.{serializer_cls.__name__}.{field_name} "
                    f"backs nullable {model.__name__}.{field_name} but the OpenAPI "
                    f"schema declares the property non-null. Add `allow_null=True` "
                    f"to the field declaration. Same bug class as Codex slice 2 round 5; "
                    f"property got: {prop!r}."
                )

    assert not failures, (
        "Found {n} explicitly-declared serializer field(s) that drop nullability "
        "from a nullable model field:\n  {failures}".format(
            n=len(failures), failures="\n  ".join(failures)
        )
    )


def test_portfolio_latest_snapshot_is_nullable_in_schema() -> None:
    """``PortfolioSerializer.get_latest_snapshot`` returns ``None`` for
    never-valued portfolios (verified by ``test_portfolio_api.py::test_portfolio_
    without_snapshot_returns_null_latest`` — a normal first-import-before-04:00-
    beat state). The OpenAPI contract MUST agree, or the generated TS client
    types ``latest_snapshot`` as non-null and the slice-5 portfolio UI
    runtime-crashes on dereference.

    The trap is that ``@extend_schema_field(PortfolioValueSnapshotSerializer)``
    — passing the *class* — declares the value type without nullability;
    ``PortfolioValueSnapshotSerializer(allow_null=True)`` (an *instance* with
    ``allow_null=True``) is the fix. Pinned here as a regression so any future
    refactor that re-removes ``allow_null`` fails CI before the snapshot /
    client are regenerated. Codex adversarial review of slice 2, round 1,
    2026-05-27.
    """
    schema = _generate_schema()
    portfolio = schema["components"]["schemas"]["Portfolio"]
    field = portfolio["properties"]["latest_snapshot"]
    assert _is_nullable(field), f"latest_snapshot must be nullable in schema, got: {field}"


def test_mover_row_nullable_fields_are_nullable_in_schema() -> None:
    """``MoverRowSerializer`` (Phase 5 "biggest movers") is a plain ``Serializer``,
    not a ``ModelSerializer`` — so the class-level nullable-field gate above does
    NOT walk it. Its two nullable fields must therefore be pinned by hand: a
    sub-floor older-anchor base leaves ``pct_change`` null (the dollar move is still
    real; DECISIONS 2026-05-31) and a no-variant printing has a null
    ``variant_label``. Without ``allow_null=True`` the generated TS client types
    them non-null and the /movers UI crashes on a normal null. Backend-independent
    (a structural assertion), so it runs under ``make test`` too — unlike the
    @postgres_only snapshot-equality gate that's the only other coverage here."""
    schema = _generate_schema()
    mover = schema["components"]["schemas"]["MoverRow"]
    for name in ("pct_change", "variant_label"):
        field = mover["properties"][name]
        assert _is_nullable(field), f"MoverRow.{name} must be nullable in schema, got: {field}"


def test_alert_event_variant_label_is_nullable_in_schema() -> None:
    """``AlertEventSerializer.variant_label`` uses ``source="printing.variant_label"``, so
    the class-level gate above SKIPS it (``AlertEvent._meta.get_field("variant_label")``
    raises FieldDoesNotExist — the field lives on ``CardPrinting``). A no-variant printing
    has a null ``variant_label``, so the schema must declare it nullable or the generated TS
    client types it non-null and the /alerts feed crashes on a normal null. Pinned by hand
    here (the ``MoverRow.variant_label`` precedent) since the auto-gate can't reach it; only
    ``allow_null=True`` on the serializer field otherwise protects it (Phase 5 slice 4
    adversarial review, the Codex slice-2 round-5 bug class)."""
    schema = _generate_schema()
    event = schema["components"]["schemas"]["AlertEvent"]
    field = event["properties"]["variant_label"]
    assert _is_nullable(field), f"AlertEvent.variant_label must be nullable, got: {field}"


def test_deck_membership_variant_label_is_nullable_in_schema() -> None:
    """``DeckMembershipSerializer.variant_label`` uses
    ``source="collection_item.printing.variant_label"``, so the class-level gate SKIPS it
    (``DeckMembership._meta.get_field("variant_label")`` raises FieldDoesNotExist — the
    field lives on ``CardPrinting``). A no-variant printing has a null ``variant_label``,
    so the schema must declare it nullable or the generated TS client types it non-null
    and the deck-detail member table crashes on a normal null. Pinned by hand here (the
    ``AlertEvent.variant_label`` precedent) since the auto-gate can't reach it."""
    schema = _generate_schema()
    membership = schema["components"]["schemas"]["DeckMembership"]
    field = membership["properties"]["variant_label"]
    assert _is_nullable(field), f"DeckMembership.variant_label must be nullable, got: {field}"
