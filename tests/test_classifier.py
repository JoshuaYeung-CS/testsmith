import pytest

from testsmith.classifier import INCONCLUSIVE, REAL_BUG, TEST_WRONG, decide, index_judgements, read_signals
from testsmith.results import TestOutcome


def outcome(details, message="", collection=False):
    return TestOutcome("test_x", "test_x", "candidates.test_t", "failed", message, details, collection)


ASSERT_IN_TEST = """candidates/test_t.py:9: in test_x
    assert order_total_cents([(333, 3)], 0.08875) == 1088
E   assert 1087 == 1088"""

RAISE_IN_PACKAGE = """candidates/test_t.py:5: in test_x
    inv.get("WASH-3003")
buggy_inventory/stock.py:49: in get
    raise UnknownSkuError(key) from None
E   buggy_inventory.models.UnknownSkuError: 'WASH-3003'"""

WRONG_ARGS = """candidates/test_t.py:5: in test_x
    inv.receive("BOLT-1001", 10, price_cents=25)
E   TypeError: Inventory.receive() got an unexpected keyword argument 'price_cents'"""

NAME_ERROR = """candidates/test_t.py:3: in test_x
    assert helper() == 1
E   NameError: name 'helper' is not defined"""

DID_NOT_RAISE = """candidates/test_t.py:6: in test_x
    with pytest.raises(ValueError):
E   Failed: DID NOT RAISE <class 'ValueError'>"""

MODULE_ATTR = """candidates/test_t.py:3: in test_x
    pricing.round_cents(1)
E   AttributeError: module 'buggy_inventory.pricing' has no attribute 'round_cents'"""

BUG = {"verdict": "real_bug", "confidence": 0.9, "contract": "rounded to the nearest cent", "explanation": "truncates"}


def test_signals_origin_and_exception():
    s = read_signals(outcome(ASSERT_IN_TEST), "buggy_inventory")
    assert (s.origin, s.exception, s.hard_reason) == ("test", "AssertionError", None)
    s = read_signals(outcome(RAISE_IN_PACKAGE), "buggy_inventory")
    assert (s.origin, s.exception) == ("package", "UnknownSkuError")
    assert s.frames[-1] == ("buggy_inventory/stock.py", 49, "get")


@pytest.mark.parametrize(
    "details,collection,needle",
    [
        (WRONG_ARGS, False, "wrong arguments"),
        (NAME_ERROR, False, "NameError"),
        (MODULE_ATTR, False, "does not define"),
        ("E   ModuleNotFoundError: No module named 'nope'", True, "collect"),
        ("E       fixture 'inv' not found", False, "fixture"),
    ],
)
def test_test_only_failures_are_settled_by_rule(details, collection, needle):
    s = read_signals(outcome(details, collection=collection), "buggy_inventory")
    assert needle in s.hard_reason
    # even a confident "bug" verdict from the model cannot override the rule
    d = decide(s, dict(BUG), exercises_package=True)
    assert (d.verdict, d.source) == (TEST_WRONG, "rule")


def test_did_not_raise_is_left_to_the_judge():
    s = read_signals(outcome(DID_NOT_RAISE), "buggy_inventory")
    assert s.hard_reason is None and s.exception == "Failed"


def test_confident_quoted_bug_is_accepted():
    s = read_signals(outcome(ASSERT_IN_TEST), "buggy_inventory")
    d = decide(s, dict(BUG), exercises_package=True)
    assert (d.verdict, d.source, d.confidence) == (REAL_BUG, "model", 0.9)


@pytest.mark.parametrize(
    "judged,exercises,verdict",
    [
        ({**BUG, "confidence": 0.4}, True, INCONCLUSIVE),  # below the bar
        ({**BUG, "contract": ""}, True, INCONCLUSIVE),  # no quoted promise
        (dict(BUG), False, TEST_WRONG),  # test never touches package code
        ({"verdict": "maybe"}, True, INCONCLUSIVE),
        (None, True, INCONCLUSIVE),
        ({"verdict": "test_wrong", "confidence": "0.8", "fixed_test": "def test_x(): pass"}, True, TEST_WRONG),
        ({"verdict": "Real Bug", "confidence": 0.95, "contract": "x"}, True, REAL_BUG),
        ({**BUG, "confidence": "not a number"}, True, INCONCLUSIVE),
    ],
)
def test_guards(judged, exercises, verdict):
    s = read_signals(outcome(ASSERT_IN_TEST), "buggy_inventory")
    assert decide(s, judged, exercises_package=exercises).verdict == verdict


def test_repair_is_carried_through():
    s = read_signals(outcome(ASSERT_IN_TEST), "buggy_inventory")
    d = decide(s, {"verdict": "test_wrong", "confidence": 0.9, "fixed_test": "def test_x():\n    pass"}, exercises_package=True)
    assert d.fixed_test.startswith("def test_x")


def test_index_judgements_accepts_common_shapes():
    assert set(index_judgements([{"test": "a"}, {"test": "candidates/t.py::b[1]"}, "junk", {}])) == {"a", "b"}
    assert set(index_judgements({"results": [{"test": "a"}]})) == {"a"}
    assert set(index_judgements({"test": "solo", "verdict": "real_bug"})) == {"solo"}
    assert index_judgements("nonsense") == {}
