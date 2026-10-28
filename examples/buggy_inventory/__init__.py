"""A small warehouse inventory library used to demo testsmith.

It looks like ordinary business code: stock levels, reorder checks and order
pricing. A few of its functions do not do what their docstrings promise.
"""

from .models import Item, UnknownSkuError
from .pricing import bulk_discount_percent, line_total_cents, order_total_cents
from .stock import Inventory, needs_reorder

__all__ = [
    "Item",
    "UnknownSkuError",
    "Inventory",
    "needs_reorder",
    "bulk_discount_percent",
    "line_total_cents",
    "order_total_cents",
]
