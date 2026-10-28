"""Tests for buggy_inventory, written and verified by testsmith.

Tests marked xfail(strict=True) document likely bugs: they fail today and will
start failing the suite (XPASS) once the bug is fixed, so remove the marker then.
"""


import pytest
from buggy_inventory import Inventory, Item, UnknownSkuError, needs_reorder
from buggy_inventory.models import validate_sku
from buggy_inventory.pricing import bulk_discount_percent, line_total_cents, order_total_cents


def make_inventory():
    inv = Inventory()
    inv.add_item(Item("BOLT-1001", "Hex bolt", 25, on_hand=40, reorder_point=10))
    inv.add_item(Item("NUT-2002", "Hex nut", 5, on_hand=3, reorder_point=5))
    return inv


def test_validate_sku_normalises_case():
    """validate_sku accepts lower-case input and upper-cases it."""
    assert validate_sku(" bolt-1001 ") == "BOLT-1001"


@pytest.mark.parametrize("bad", ["B-100", "BOLT1001", "TOOLONG-123", "AB-12", 1001])
def test_validate_sku_rejects_malformed(bad):
    """validate_sku raises ValueError for anything but 2-4 letters, a dash, 3-6 digits."""
    with pytest.raises(ValueError):
        validate_sku(bad)


def test_item_rejects_negative_price():
    """Item refuses a negative unit price."""
    with pytest.raises(ValueError):
        Item("BOLT-1001", "Hex bolt", -1)


def test_get_unknown_sku_raises():
    """Inventory.get raises UnknownSkuError for a SKU it does not stock."""
    inv = make_inventory()
    with pytest.raises(UnknownSkuError):
        inv.get("WASH-3003")


def test_receive_returns_new_quantity():
    """receive adds stock and returns the new on-hand quantity."""
    inv = make_inventory()
    assert inv.receive("BOLT-1001", 10) == 50


def test_remove_more_than_on_hand_raises_and_keeps_stock():
    """remove refuses to take more than is on hand and leaves stock unchanged."""
    inv = make_inventory()
    with pytest.raises(ValueError):
        inv.remove("NUT-2002", 4)
    assert inv.get("NUT-2002").on_hand == 3


@pytest.mark.xfail(strict=True, reason="testsmith: likely bug: Inventory.remove: remove only checks qty > on_hand, so a negative quantity silently adds stock instead of being rejected.")
def test_remove_rejects_negative_quantity():
    """remove raises ValueError when qty is not a positive integer."""
    inv = make_inventory()
    with pytest.raises(ValueError):
        inv.remove("BOLT-1001", -5)


@pytest.mark.xfail(strict=True, reason="testsmith: likely bug: needs_reorder: needs_reorder compares with < instead of <=, so an item sitting exactly at its reorder point is never flagged.")
def test_needs_reorder_at_reorder_point():
    """An item exactly at its reorder point needs reordering ("at or below")."""
    item = Item("NUT-2002", "Hex nut", 5, on_hand=5, reorder_point=5)
    assert needs_reorder(item) is True


def test_needs_reorder_disabled_when_point_is_zero():
    """A reorder point of 0 means never reorder automatically."""
    item = Item("NUT-2002", "Hex nut", 5, on_hand=0, reorder_point=0)
    assert needs_reorder(item) is False


def test_low_stock_lists_items_below_point():
    """low_stock returns the SKUs that need reordering, sorted."""
    inv = make_inventory()
    assert inv.low_stock() == ["NUT-2002"]


@pytest.mark.parametrize("qty,pct", [(9, 0), (10, 5), (49, 5), (50, 10), (99, 10), (100, 15)])
def test_bulk_discount_tiers(qty, pct):
    """bulk_discount_percent follows the documented tiers at every boundary."""
    assert bulk_discount_percent(qty) == pct


def test_line_total_applies_discount():
    """line_total_cents applies the 5% tier for 10 units and rounds half up."""
    # 999c x 10 = 9990c; 5% off = 9490.5c, half rounds up to 9491c
    assert line_total_cents(999, 10) == 9491


@pytest.mark.xfail(strict=True, reason="testsmith: likely bug: order_total_cents: order_total_cents truncates with int() instead of rounding, so any total with a fractional cent of .5 or more comes out one cent low.")
def test_order_total_rounds_to_nearest_cent():
    """order_total_cents rounds the taxed total to the nearest cent."""
    # 3 x 333c = 999c; 999 * 1.08875 = 1087.66c, which rounds to 1088c
    assert order_total_cents([(333, 3)], 0.08875) == 1088


def test_add_item_rejects_duplicate_sku():
    """add_item raises ValueError when the SKU already exists."""
    inv = make_inventory()
    with pytest.raises(ValueError):
        inv.add_item(Item("bolt-1001", "Another bolt", 30))


def test_contains_is_case_insensitive_and_safe():
    """`in` normalises SKUs and returns False for malformed or non-string keys."""
    inv = make_inventory()
    assert "bolt-1001" in inv
    assert "WASH-3003" not in inv
    assert "not a sku" not in inv
    assert 1001 not in inv
    assert len(inv) == 2


@pytest.mark.parametrize("qty", [0, -3, 2.5])
def test_receive_rejects_non_positive_or_non_integer(qty):
    """receive raises ValueError unless qty is a positive integer."""
    inv = make_inventory()
    with pytest.raises(ValueError):
        inv.receive("BOLT-1001", qty)


def test_stock_value_sums_price_times_on_hand():
    """stock_value_cents is the list value of everything on hand."""
    inv = make_inventory()
    assert inv.stock_value_cents() == 40 * 25 + 3 * 5


@pytest.mark.parametrize("field", ["on_hand", "reorder_point"])
def test_item_rejects_negative_counts(field):
    """Item refuses negative on_hand and reorder_point."""
    with pytest.raises(ValueError):
        Item("BOLT-1001", "Hex bolt", 25, **{field: -1})


def test_pricing_rejects_negative_inputs():
    """Negative quantities and tax rates are rejected."""
    with pytest.raises(ValueError):
        bulk_discount_percent(-1)
    with pytest.raises(ValueError):
        order_total_cents([(100, 1)], -0.1)
