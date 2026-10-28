# buggy_inventory: the demo package

A small warehouse library: SKU validation, stock movements, reorder checks, bulk
discounts and tax. `tests/test_smoke.py` is the thin hand-written suite it ships with
(about 52% line coverage).

## Answer key (spoilers)

Three bugs are planted. Each one contradicts the function's own docstring:

| Function | Docstring promise | What the code does |
|---|---|---|
| `stock.needs_reorder` | reorder when on-hand is "at or below" the reorder point | uses `<`, so an item exactly at the point is never flagged |
| `stock.Inventory.remove` | "Raises ValueError if qty is not a positive integer ... Stock never goes negative." | only checks `qty > on_hand`; `remove(sku, -5)` adds 5 units |
| `pricing.order_total_cents` | "the final total is rounded to the nearest cent" | truncates with `int()`, so 1087.66 cents becomes 1087 |

`line_total_cents` and `bulk_discount_percent` are correct, which is what makes them
useful: a test that gets their arithmetic wrong must be repaired, not reported.

`replay/buggy_inventory.json` holds recorded model replies for an offline run
(`testsmith examples/buggy_inventory --replay examples/replay/buggy_inventory.json`);
`replay/make_script.py` rebuilds it.
