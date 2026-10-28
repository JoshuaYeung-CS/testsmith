"""Build ``buggy_inventory.json``: a recorded set of model replies for an offline run.

The replies are written in the shape Nemotron returns through Token Factory (a
reasoning span, some prose, then a fenced payload) so the offline run exercises the
same extraction, triage and repair code paths as a live run. Run:

    python examples/replay/make_script.py
"""

import json
from pathlib import Path

WRITE = '''<think>
The package has three modules. Docstrings define the contracts: SKU format, stock never
negative, reorder "at or below", discount tiers, rounding to the nearest cent. I will
test the boundaries of each.
</think>
Here is a pytest suite for `buggy_inventory`. It targets the documented contracts,
with extra attention on thresholds and rounding.

```python
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
    assert inv.receive("BOLT-1001", 10, price_cents=25) == 50


def test_remove_more_than_on_hand_raises_and_keeps_stock():
    """remove refuses to take more than is on hand and leaves stock unchanged."""
    inv = make_inventory()
    with pytest.raises(ValueError):
        inv.remove("NUT-2002", 4)
    assert inv.get("NUT-2002").on_hand == 3


def test_remove_rejects_negative_quantity():
    """remove raises ValueError when qty is not a positive integer."""
    inv = make_inventory()
    with pytest.raises(ValueError):
        inv.remove("BOLT-1001", -5)


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
    """line_total_cents applies the bulk discount for 10 units."""
    assert line_total_cents(999, 10) == 8991


def test_order_total_rounds_to_nearest_cent():
    """order_total_cents rounds the taxed total to the nearest cent."""
    # 3 x 333c = 999c; 999 * 1.08875 = 1087.66c, which rounds to 1088c
    assert order_total_cents([(333, 3)], 0.08875) == 1088
```

These cover validation, stock movement, reorder logic and pricing.'''

TRIAGE = '''<think>
Five failures. receive: the signature is receive(sku, qty); price_cents does not exist,
so the test is wrong. remove(-5): the docstring says ValueError if qty is not a positive
integer and "stock never goes negative"; the code only checks qty > on_hand, so a
negative qty adds stock. Real bug. needs_reorder: docstring says "at or below", code
uses <. Real bug. line_total: 10 units is the 5% tier, not 10%; 9990 * 0.95 = 9490.5,
half up gives 9491. Test is wrong. order_total: docstring says rounded to nearest cent,
code truncates with int(). Real bug.
</think>
```json
[
  {"test": "test_receive_returns_new_quantity", "verdict": "test_wrong", "confidence": 0.97,
   "contract": "receive(sku, qty) -> int", "expected": "50", "actual": "TypeError: unexpected keyword argument 'price_cents'",
   "explanation": "receive takes only sku and qty; the test passed a price_cents argument that does not exist.",
   "fixed_test": "def test_receive_returns_new_quantity():\\n    \\"\\"\\"receive adds stock and returns the new on-hand quantity.\\"\\"\\"\\n    inv = make_inventory()\\n    assert inv.receive(\\"BOLT-1001\\", 10) == 50",
   "repro": ""},
  {"test": "test_remove_rejects_negative_quantity", "verdict": "real_bug", "confidence": 0.93,
   "contract": "Raises ValueError if qty is not a positive integer or if it is larger than the quantity on hand. Stock never goes negative.",
   "expected": "ValueError for qty=-5", "actual": "no exception; on_hand rises from 40 to 45",
   "explanation": "remove only checks qty > on_hand, so a negative quantity silently adds stock instead of being rejected.",
   "fixed_test": "",
   "repro": "from buggy_inventory import Inventory, Item\\ninv = Inventory()\\ninv.add_item(Item(\\"BOLT-1001\\", \\"Hex bolt\\", 25, on_hand=40))\\nprint(\\"on hand after remove(-5):\\", inv.remove(\\"BOLT-1001\\", -5))  # expected ValueError"},
  {"test": "test_needs_reorder_at_reorder_point", "verdict": "real_bug", "confidence": 0.95,
   "contract": "An item needs reordering when its on-hand quantity is at or below its reorder point.",
   "expected": "True when on_hand == reorder_point", "actual": "False",
   "explanation": "needs_reorder compares with < instead of <=, so an item sitting exactly at its reorder point is never flagged.",
   "fixed_test": "",
   "repro": "from buggy_inventory import Item, needs_reorder\\nitem = Item(\\"NUT-2002\\", \\"Hex nut\\", 5, on_hand=5, reorder_point=5)\\nprint(\\"needs_reorder at the reorder point:\\", needs_reorder(item))  # expected True"},
  {"test": "test_line_total_applies_discount", "verdict": "test_wrong", "confidence": 0.9,
   "contract": "10-49 units get 5%. The discounted amount is rounded to the nearest cent, with halves rounded up.",
   "expected": "9491", "actual": "9491",
   "explanation": "The test applied a 10% discount; 10 units fall in the 5% tier, and 9490.5 rounds half up to 9491.",
   "fixed_test": "def test_line_total_applies_discount():\\n    \\"\\"\\"line_total_cents applies the 5% tier for 10 units and rounds half up.\\"\\"\\"\\n    # 999c x 10 = 9990c; 5% off = 9490.5c, half rounds up to 9491c\\n    assert line_total_cents(999, 10) == 9491",
   "repro": ""},
  {"test": "test_order_total_rounds_to_nearest_cent", "verdict": "real_bug", "confidence": 0.91,
   "contract": "the final total is rounded to the nearest cent.",
   "expected": "1088", "actual": "1087",
   "explanation": "order_total_cents truncates with int() instead of rounding, so any total with a fractional cent of .5 or more comes out one cent low.",
   "fixed_test": "",
   "repro": "from buggy_inventory import order_total_cents\\nprint(\\"total for 3 x 333c at 8.875% tax:\\", order_total_cents([(333, 3)], 0.08875), \\"cents (999 x 1.08875 = 1087.66)\\")  # expected 1088"}
]
```'''

COVERAGE = '''<think>
Uncovered: __len__, __contains__, the duplicate-SKU branch of add_item, the receive
validation, stock_value_cents, the Item field checks, and the negative-input guards in
pricing.
</think>
```python
import pytest

from buggy_inventory import Inventory, Item
from buggy_inventory.pricing import bulk_discount_percent, order_total_cents


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
```'''

script = {
    "description": "Recorded replies for an offline testsmith run on examples/buggy_inventory.",
    "responses": {"write": [WRITE], "triage": [TRIAGE], "coverage": [COVERAGE]},
}

out = Path(__file__).with_name("buggy_inventory.json")
out.write_text(json.dumps(script, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out}")
