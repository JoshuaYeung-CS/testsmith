"""Core data types for the inventory."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SKU_PATTERN = re.compile(r"^[A-Z]{2,4}-\d{3,6}$")


class UnknownSkuError(KeyError):
    """Raised when an operation names a SKU the inventory does not hold."""


def validate_sku(sku: str) -> str:
    """Return the SKU upper-cased if it is well formed.

    A SKU is 2-4 letters, a dash, then 3-6 digits, e.g. ``"BOLT-1001"``.
    Lower-case input is accepted and normalised. Raises ``ValueError`` for
    anything else.
    """
    if not isinstance(sku, str):
        raise ValueError(f"SKU must be a string, got {type(sku).__name__}")
    normalised = sku.strip().upper()
    if not _SKU_PATTERN.match(normalised):
        raise ValueError(f"malformed SKU: {sku!r}")
    return normalised


@dataclass
class Item:
    """One stocked product.

    ``unit_price_cents`` is the list price of one unit in integer cents.
    ``reorder_point`` is the on-hand level at which more stock should be ordered.
    """

    sku: str
    name: str
    unit_price_cents: int
    on_hand: int = 0
    reorder_point: int = 0

    def __post_init__(self) -> None:
        self.sku = validate_sku(self.sku)
        if self.unit_price_cents < 0:
            raise ValueError("unit_price_cents must be >= 0")
        if self.on_hand < 0:
            raise ValueError("on_hand must be >= 0")
        if self.reorder_point < 0:
            raise ValueError("reorder_point must be >= 0")
