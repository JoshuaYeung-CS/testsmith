# testsmith report: `buggy_inventory`

> Mode: **replay of buggy_inventory.json**. Model replies in this run came from a recorded script, not from a live endpoint. Everything else (sandboxed runs, coverage, triage rules, repros) executed for real.

- Target: `examples/buggy_inventory`  
- Run at: 2026-10-28 07:33 UTC  
- Writer model: `nvidia/Nemotron-3_5-Lightning`  
- Judge model: `nvidia/Nemotron-3-Ultra-550b-a55b`  
- Endpoint: `https://api.tokenfactory.nebius.com/v1/` (not contacted in a replay)

## Summary

| | |
|---|---|
| Line coverage before | 52.1% (existing tests) |
| Line coverage after | 100.0% |
| Change | +47.9 pts |
| Tests written by the model | 13 (+ coverage follow-ups) |
| Kept, passing (test functions) | 16 |
| Kept, exposing a likely bug (strict xfail) | 3 |
| Repaired after triage | 2 |
| Dropped | 0 |
| Rounds | 3 |
| Final suite run | 28 passed, 3 xfailed |
| Model calls | 3 calls, 7,318 in / 2,337 out tokens, 0.0s |
| Estimated cost | $0.0046 (partial: some models unpriced) |

Tests written to `docs/sample-run/test_buggy_inventory_testsmith.py`.

## Likely bugs (3)

### 1. `Inventory.remove` (buggy_inventory/stock.py:62)

**Promise:** Raises ValueError if qty is not a positive integer or if it is larger than the quantity on hand. Stock never goes negative.

**Expected:** ValueError for qty=-5  
**Actual:** no exception; on_hand rises from 40 to 45

remove only checks qty > on_hand, so a negative quantity silently adds stock instead of being rejected.

Evidence: failed identically on 5 separate runs; judge confidence 93%; kept as `test_remove_rejects_negative_quantity` with a strict xfail marker.

Failing assertion:

```
E   Failed: DID NOT RAISE ValueError
```

Minimal repro (the test):

```python
def test_remove_rejects_negative_quantity():
    """remove raises ValueError when qty is not a positive integer."""
    inv = make_inventory()
    with pytest.raises(ValueError):
        inv.remove("BOLT-1001", -5)
```

Standalone repro, executed in the sandbox (exit 0):

```python
from buggy_inventory import Inventory, Item
inv = Inventory()
inv.add_item(Item("BOLT-1001", "Hex bolt", 25, on_hand=40))
print("on hand after remove(-5):", inv.remove("BOLT-1001", -5))  # expected ValueError
```

```text
on hand after remove(-5): 45
```

### 2. `needs_reorder` (buggy_inventory/stock.py:8)

**Promise:** An item needs reordering when its on-hand quantity is at or below its reorder point.

**Expected:** True when on_hand == reorder_point  
**Actual:** False

needs_reorder compares with < instead of <=, so an item sitting exactly at its reorder point is never flagged.

Evidence: failed identically on 5 separate runs; judge confidence 95%; kept as `test_needs_reorder_at_reorder_point` with a strict xfail marker.

Failing assertion:

```
E   AssertionError: assert False is True
E    +  where False = needs_reorder(Item(sku='NUT-2002', name='Hex nut', unit_price_cents=5, on_hand=5, reorder_point=5))
```

Minimal repro (the test):

```python
def test_needs_reorder_at_reorder_point():
    """An item exactly at its reorder point needs reordering ("at or below")."""
    item = Item("NUT-2002", "Hex nut", 5, on_hand=5, reorder_point=5)
    assert needs_reorder(item) is True
```

Standalone repro, executed in the sandbox (exit 0):

```python
from buggy_inventory import Item, needs_reorder
item = Item("NUT-2002", "Hex nut", 5, on_hand=5, reorder_point=5)
print("needs_reorder at the reorder point:", needs_reorder(item))  # expected True
```

```text
needs_reorder at the reorder point: False
```

### 3. `order_total_cents` (buggy_inventory/pricing.py:37)

**Promise:** the final total is rounded to the nearest cent.

**Expected:** 1088  
**Actual:** 1087

order_total_cents truncates with int() instead of rounding, so any total with a fractional cent of .5 or more comes out one cent low.

Evidence: failed identically on 5 separate runs; judge confidence 91%; kept as `test_order_total_rounds_to_nearest_cent` with a strict xfail marker.

Failing assertion:

```
E   assert 1087 == 1088
E    +  where 1087 = order_total_cents([(333, 3)], 0.08875)
```

Minimal repro (the test):

```python
def test_order_total_rounds_to_nearest_cent():
    """order_total_cents rounds the taxed total to the nearest cent."""
    # 3 x 333c = 999c; 999 * 1.08875 = 1087.66c, which rounds to 1088c
    assert order_total_cents([(333, 3)], 0.08875) == 1088
```

Standalone repro, executed in the sandbox (exit 0):

```python
from buggy_inventory import order_total_cents
print("total for 3 x 333c at 8.875% tax:", order_total_cents([(333, 3)], 0.08875), "cents (999 x 1.08875 = 1087.66)")  # expected 1088
```

```text
total for 3 x 333c at 8.875% tax: 1087 cents (999 x 1.08875 = 1087.66)
```

## Rounds

| Round | Suite under test | Passed | Failing | Coverage | Then |
|---|---|---|---|---|---|
| 1 | initial tests | 17 | 5 | 81.2% | `test_receive_returns_new_quantity` -> test_wrong (rule); `test_remove_rejects_negative_quantity` -> real_bug (model); `test_needs_reorder_at_reorder_point` -> real_bug (model); `test_line_total_applies_discount` -> test_wrong (model); `test_order_total_rounds_to_nearest_cent` -> real_bug (model) |
| 2 | repairs applied | 19 | 3 | 85.4% | coverage below target: asked for tests on uncovered lines, merged 6 |
| 3 | coverage tests added | 28 | 3 | 100.0% | converged |

## Repaired tests

- `test_receive_returns_new_quantity` (round 1, rule): the test calls the API with the wrong arguments
- `test_line_total_applies_discount` (round 1, model): The test applied a 10% discount; 10 units fall in the 5% tier, and 9490.5 rounds half up to 9491.

## Model usage

| # | Step | Model | Tokens in | Tokens out | Latency | Attempts | Cost |
|---|---|---|---|---|---|---|---|
| 1 | write | `nvidia/Nemotron-3_5-Lightning` | 2,314 | 933 | 0.00s | 1 | n/a |
| 2 | triage | `nvidia/Nemotron-3-Ultra-550b-a55b` | 1,764 | 932 | 0.00s | 1 | $0.00456 |
| 3 | coverage | `nvidia/Nemotron-3_5-Lightning` | 3,240 | 472 | 0.00s | 1 | n/a |

## How this was checked

Every model-written test ran in a fresh subprocess on a temp-dir copy of the package, with a scrubbed environment, a per-test time limit and outbound network blocked. A failing test is only kept as a bug when the judge quotes the promise the code breaks, clears the confidence bar, the test calls package code, and the failure repeats identically on a rerun. Failures that can only be the test's fault (imports, wrong arguments, undefined names) are settled by rule and repaired. The final file was run once more with the existing suite to measure coverage.
