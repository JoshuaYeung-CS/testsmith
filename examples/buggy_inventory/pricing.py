"""Order pricing: bulk discounts and tax."""

from __future__ import annotations

from typing import Iterable


def bulk_discount_percent(qty: int) -> int:
    """Return the bulk discount, in whole percent, for a line of ``qty`` units.

    Tiers: fewer than 10 units get 0%, 10-49 units get 5%, 50-99 units get
    10%, and 100 or more units get 15%.
    """
    if qty < 0:
        raise ValueError("qty must be >= 0")
    if qty >= 100:
        return 15
    if qty >= 50:
        return 10
    if qty >= 10:
        return 5
    return 0


def line_total_cents(unit_price_cents: int, qty: int) -> int:
    """Return the price of one order line in cents, after the bulk discount.

    The discounted amount is rounded to the nearest cent, with halves rounded
    up.
    """
    gross = unit_price_cents * qty
    pct = bulk_discount_percent(qty)
    # integer arithmetic: gross * (100 - pct) / 100, rounded half up
    return (gross * (100 - pct) + 50) // 100


def order_total_cents(lines: Iterable[tuple[int, int]], tax_rate: float) -> int:
    """Return the order total in cents for ``(unit_price_cents, qty)`` lines.

    Each line gets its bulk discount first, the discounted subtotal is then
    taxed at ``tax_rate`` (e.g. ``0.08875`` for 8.875%), and the final total is
    rounded to the nearest cent.
    """
    if tax_rate < 0:
        raise ValueError("tax_rate must be >= 0")
    subtotal = sum(line_total_cents(price, qty) for price, qty in lines)
    return int(subtotal * (1 + tax_rate))
