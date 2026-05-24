import pytest
from django.db import IntegrityError, connection, models, transaction

from apps.cards.models import Card, CardPrinting, MetadataSource, PrintingAlias
from apps.cards.normalization import normalize_name

# The CardPrinting natural-key UniqueConstraint sets nulls_distinct=False, which
# sqlite can't honor — so Django skips creating the *entire* constraint there
# (confirmed via `manage.py sqlmigrate`). DB-level uniqueness therefore exists
# only on PostgreSQL; tests that assert enforcement are gated to it. The intent
# is still checked everywhere by test_printing_constraint_uses_nulls_not_distinct.
postgres_only = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="CardPrinting unique constraint (nulls_distinct=False) is created only on PostgreSQL 15+.",
)


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


@pytest.mark.django_db
def test_partial_update_preserves_normalized_name() -> None:
    """A save(update_fields=["name"]) must still persist the derived
    normalized_name. update_or_create takes this partial-update path, so without
    it the derived field silently desyncs in the DB (DECISIONS 2026-05-20)."""
    card = Card.objects.create(name="Dark Magician")
    card.name = "Dark Magician Girl"
    card.save(update_fields=["name"])
    card.refresh_from_db()
    assert card.normalized_name == "dark magician girl"


@pytest.mark.django_db
def test_printing_linked_to_card() -> None:
    card = Card.objects.create(name="Dark Magician")
    printing = CardPrinting.objects.create(
        card=card,
        set_code="LOB-005",
        set_rarity="Ultra Rare",
        set_name="Legend of Blue Eyes White Dragon",
    )

    assert printing.card == card
    assert list(card.printings.all()) == [printing]


@postgres_only
@pytest.mark.django_db
def test_duplicate_printing_with_same_variant_rejected() -> None:
    """The natural key (card, set_code, set_rarity, variant_label) is unique
    (DB-enforced on PostgreSQL only; see the postgres_only note above)."""
    card = Card.objects.create(name="Blue-Eyes White Dragon")
    CardPrinting.objects.create(
        card=card,
        set_code="RA03-EN056",
        set_rarity="Platinum Secret Rare",
        variant_label="alt art",
        set_name="Quarter Century Stampede",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        CardPrinting.objects.create(
            card=card,
            set_code="RA03-EN056",
            set_rarity="Platinum Secret Rare",
            variant_label="alt art",
            set_name="Quarter Century Stampede",
        )


@pytest.mark.django_db
def test_distinct_variant_labels_coexist() -> None:
    """Same set/rarity, different artworks — disambiguated by variant_label."""
    card = Card.objects.create(name="Blue-Eyes White Dragon")
    for label in ("Version 1", "Version 2", "Version 3"):
        CardPrinting.objects.create(
            card=card,
            set_code="LDK2-ENK01",
            set_rarity="Common",
            variant_label=label,
            set_name="Legendary Decks II",
        )

    assert card.printings.count() == 3


@pytest.mark.django_db
def test_null_and_labeled_variant_coexist() -> None:
    """A NULL variant and a labeled variant are distinct under either NULL mode."""
    card = Card.objects.create(name="Blue-Eyes White Dragon")
    CardPrinting.objects.create(
        card=card, set_code="LDK2-ENK01", set_rarity="Common", set_name="Legendary Decks II"
    )
    CardPrinting.objects.create(
        card=card,
        set_code="LDK2-ENK01",
        set_rarity="Common",
        variant_label="Version 1",
        set_name="Legendary Decks II",
    )

    assert card.printings.count() == 2


@pytest.mark.django_db
def test_blank_variant_label_coerced_to_none() -> None:
    """Whitespace-only labels collapse to NULL so "no variant" has one form."""
    card = Card.objects.create(name="Aqua Madoor")
    printing = CardPrinting.objects.create(
        card=card,
        set_code="LOB-040",
        set_rarity="Common",
        variant_label="   ",
        set_name="Legend of Blue Eyes White Dragon",
    )
    printing.refresh_from_db()

    assert printing.variant_label is None


def test_printing_constraint_uses_nulls_not_distinct() -> None:
    """Intent check that runs on every backend (sqlite can't enforce the NULL
    semantics, so assert the constraint is *defined* with NULLS NOT DISTINCT)."""
    constraint = next(
        c for c in CardPrinting._meta.constraints if isinstance(c, models.UniqueConstraint)
    )

    assert constraint.fields == ("card", "set_code", "set_rarity", "variant_label")
    assert constraint.nulls_distinct is False


@postgres_only
@pytest.mark.django_db
def test_null_variant_duplicates_rejected_on_postgres() -> None:
    """Two NULL-variant printings of the same (card, set, rarity) collide under
    NULLS NOT DISTINCT. Skipped on sqlite, which treats each NULL as distinct."""
    card = Card.objects.create(name="Aqua Madoor")
    CardPrinting.objects.create(
        card=card,
        set_code="LOB-040",
        set_rarity="Common",
        set_name="Legend of Blue Eyes White Dragon",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        CardPrinting.objects.create(
            card=card,
            set_code="LOB-040",
            set_rarity="Common",
            set_name="Legend of Blue Eyes White Dragon",
        )


@pytest.mark.django_db
def test_bulk_create_rejects_blank_variant_label() -> None:
    """bulk_create bypasses save()'s ""→NULL coercion, so the CheckConstraint is
    the real guard: a blank "no variant" value must be rejected, else it coexists
    with NULL and defeats the natural key. The CHECK runs on sqlite too, so here."""
    card = Card.objects.create(name="Blue-Eyes White Dragon")

    with pytest.raises(IntegrityError), transaction.atomic():
        CardPrinting.objects.bulk_create(
            [
                CardPrinting(
                    card=card,
                    set_code="LOB-040",
                    set_rarity="Common",
                    variant_label="",
                    set_name="Legend of Blue Eyes White Dragon",
                )
            ]
        )


@pytest.mark.django_db
def test_bulk_create_rejects_untrimmed_variant_label() -> None:
    """The CheckConstraint demands a canonical (trimmed) label, not merely non-empty."""
    card = Card.objects.create(name="Blue-Eyes White Dragon")

    with pytest.raises(IntegrityError), transaction.atomic():
        CardPrinting.objects.bulk_create(
            [
                CardPrinting(
                    card=card,
                    set_code="LDK2-ENK01",
                    set_rarity="Common",
                    variant_label=" Version 1 ",
                    set_name="Legendary Decks II",
                )
            ]
        )


@pytest.mark.django_db
def test_bulk_create_allows_canonical_variant_labels() -> None:
    """NULL and trimmed non-empty labels pass the CheckConstraint via the bulk path."""
    card = Card.objects.create(name="Blue-Eyes White Dragon")
    CardPrinting.objects.bulk_create(
        [
            CardPrinting(
                card=card, set_code="LDK2-ENK01", set_rarity="Common", set_name="Legendary Decks II"
            ),
            CardPrinting(
                card=card,
                set_code="LDK2-ENK01",
                set_rarity="Common",
                variant_label="Version 1",
                set_name="Legendary Decks II",
            ),
        ]
    )

    assert CardPrinting.objects.filter(card=card).count() == 2


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


# --- PrintingAlias ----------------------------------------------------------


def _canonical_printing(card: Card) -> CardPrinting:
    return CardPrinting.objects.create(
        card=card,
        set_code="RA03-EN053",
        set_rarity="Prismatic Ultimate Rare",
        set_name="Quarter Century Stampede",
    )


@pytest.mark.django_db
def test_printing_alias_resolves_provisional_key_to_canonical() -> None:
    card = Card.objects.create(name="Super Polymerization")
    printing = _canonical_printing(card)
    alias = PrintingAlias.objects.create(
        source=MetadataSource.YGOPRODECK,
        card=card,
        set_code="RA03-EN053",
        set_rarity="Ultimate Rare",  # the provisional value YGOPRODeck seeded
        printing=printing,
    )

    assert alias.printing == printing
    assert list(printing.aliases.all()) == [alias]


@pytest.mark.django_db
def test_printing_alias_provisional_key_is_unique() -> None:
    """(source, card, set_code, set_rarity) is unique — all non-null, so enforced on
    sqlite too (unlike the CardPrinting natural key)."""
    card = Card.objects.create(name="Super Polymerization")
    fields = dict(
        source=MetadataSource.YGOPRODECK,
        card=card,
        set_code="RA03-EN053",
        set_rarity="Ultimate Rare",
        printing=_canonical_printing(card),
    )
    PrintingAlias.objects.create(**fields)

    with pytest.raises(IntegrityError), transaction.atomic():
        PrintingAlias.objects.create(**fields)


@pytest.mark.django_db
def test_printing_alias_invalid_source_rejected_by_db() -> None:
    """choices is form-layer only; the CHECK keeps an out-of-vocabulary source out
    (e.g. the pricing 'tcgcsv' slug is not a metadata source)."""
    card = Card.objects.create(name="Super Polymerization")

    with pytest.raises(IntegrityError), transaction.atomic():
        PrintingAlias.objects.create(
            source="tcgcsv",
            card=card,
            set_code="RA03-EN053",
            set_rarity="Ultimate Rare",
            printing=_canonical_printing(card),
        )


@pytest.mark.django_db
def test_deleting_printing_cascades_aliases() -> None:
    """on_delete=CASCADE — the alias is a re-derivable leaf, meaningless without its
    printing (the external_price_ids pattern)."""
    card = Card.objects.create(name="Super Polymerization")
    printing = _canonical_printing(card)
    PrintingAlias.objects.create(
        source=MetadataSource.YGOPRODECK,
        card=card,
        set_code="RA03-EN053",
        set_rarity="Ultimate Rare",
        printing=printing,
    )

    printing.delete()

    assert PrintingAlias.objects.count() == 0
