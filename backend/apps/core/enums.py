from __future__ import annotations

from django.db import models


class Edition(models.TextChoices):
    """The print run a physical card came from.

    A dimension of both ``collection_items`` (part of ownership identity) and
    ``price_snapshots`` (pricing), and never part of card-printing identity,
    so it lives here in ``core`` as a shared enum rather than on either app's
    model. Dragon Shield's ``Printing`` column maps
    directly; TCGCSV's ``subTypeName`` maps after the sealed-product ``Normal``
    subtype is filtered out.
    """

    FIRST_EDITION = "first", "1st Edition"
    UNLIMITED = "unlimited", "Unlimited"
    LIMITED = "limited", "Limited"
