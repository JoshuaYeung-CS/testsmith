"""Stock levels: receiving, picking and reorder checks."""

from __future__ import annotations

from .models import Item, UnknownSkuError, validate_sku


def needs_reorder(item: Item) -> bool:
    """Return True when the item should be reordered.

    An item needs reordering when its on-hand quantity is at or below its
    reorder point. Items with a reorder point of 0 are never reordered
    automatically.
    """
    if item.reorder_point == 0:
        return False
    return item.on_hand < item.reorder_point


class Inventory:
    """An in-memory stock ledger keyed by SKU."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, sku: object) -> bool:
        if not isinstance(sku, str):
            return False
        try:
            return validate_sku(sku) in self._items
        except ValueError:
            return False

    def add_item(self, item: Item) -> None:
        """Register a new product. Raises ``ValueError`` if the SKU already exists."""
        if item.sku in self._items:
            raise ValueError(f"duplicate SKU: {item.sku}")
        self._items[item.sku] = item

    def get(self, sku: str) -> Item:
        """Return the item for ``sku``. Raises ``UnknownSkuError`` if it is not stocked."""
        key = validate_sku(sku)
        try:
            return self._items[key]
        except KeyError:
            raise UnknownSkuError(key) from None

    def receive(self, sku: str, qty: int) -> int:
        """Add ``qty`` units of ``sku`` to stock and return the new on-hand quantity.

        Raises ``ValueError`` if ``qty`` is not a positive integer.
        """
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError("qty must be a positive integer")
        item = self.get(sku)
        item.on_hand += qty
        return item.on_hand

    def remove(self, sku: str, qty: int) -> int:
        """Pick ``qty`` units of ``sku`` out of stock and return the new on-hand quantity.

        Raises ``ValueError`` if ``qty`` is not a positive integer or if it is
        larger than the quantity on hand. Stock never goes negative.
        """
        item = self.get(sku)
        if qty > item.on_hand:
            raise ValueError(
                f"cannot remove {qty} of {item.sku}: only {item.on_hand} on hand"
            )
        item.on_hand -= qty
        return item.on_hand

    def low_stock(self) -> list[str]:
        """Return the SKUs that need reordering, sorted alphabetically."""
        return sorted(sku for sku, item in self._items.items() if needs_reorder(item))

    def stock_value_cents(self) -> int:
        """Return the total list value of everything on hand, in cents."""
        return sum(item.unit_price_cents * item.on_hand for item in self._items.values())
