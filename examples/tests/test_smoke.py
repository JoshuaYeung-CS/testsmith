"""The thin hand-written suite that ships with buggy_inventory."""

from buggy_inventory import Inventory, Item


def test_add_and_get():
    inv = Inventory()
    inv.add_item(Item("BOLT-1001", "Hex bolt", 25, on_hand=10))
    assert inv.get("bolt-1001").name == "Hex bolt"
