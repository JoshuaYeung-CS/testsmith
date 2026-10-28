import ast

from testsmith.testfile import TestFile

BASE = '''"""module doc"""
import pytest
from pkg import a, b

LIMIT = 3


@pytest.fixture
def thing():
    return 1


def helper():
    return 2


def test_one(thing):
    """one"""
    assert a(thing) == 1


@pytest.mark.parametrize("n", [1, 2])
def test_two(n):
    assert b(n)
'''


def test_parse_splits_header_and_tests():
    tf = TestFile.parse(BASE)
    assert list(tf.tests) == ["test_one", "test_two"]
    assert tf.imports == ["import pytest", "from pkg import a, b"]
    assert [h[0] for h in tf.helpers] == ["LIMIT = 3", "thing", "helper"]
    assert tf.tests["test_two"].source.startswith("@pytest.mark.parametrize")


def test_render_round_trips_to_valid_python():
    code = TestFile.parse(BASE).render('"""header"""')
    tree = ast.parse(code)
    names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert names == ["thing", "helper", "test_one", "test_two"]


def test_replace_merges_imports_without_duplicates():
    tf = TestFile.parse(BASE)
    tf.merge("from pkg import a, c\nimport math\n\ndef test_one():\n    assert a(1) == math.floor(1.2)\n", replace=True)
    assert tf.tests["test_one"].origin == "repair" and tf.tests["test_one"].repairs == 1
    assert "math.floor" in tf.tests["test_one"].source
    assert tf.imports == ["import pytest", "import math", "from pkg import a, b, c"]


def test_merge_without_replace_renames_clashes():
    tf = TestFile.parse(BASE)
    added = tf.merge("def test_one():\n    assert True\n", origin="coverage")
    assert added == ["test_one_2"]
    assert "def test_one_2(" in tf.tests["test_one_2"].source
    assert tf.tests["test_one_2"].origin == "coverage"


def test_mark_bug_adds_strict_xfail_and_pytest_import():
    tf = TestFile.parse("from pkg import a\n\ndef test_x():\n    assert a() == 1\n")
    tf.mark_bug("test_x", 'a: returns "0" instead of 1')
    code = tf.render()
    assert code.splitlines()[0] == "import pytest"
    assert '@pytest.mark.xfail(strict=True, reason="testsmith: likely bug: a: returns \\"0\\" instead of 1")' in code
    ast.parse(code)


def test_drop_removes_a_test():
    tf = TestFile.parse(BASE)
    tf.drop("test_one")
    tf.drop("missing")
    assert list(tf.tests) == ["test_two"]


def test_classes_without_self_are_flattened_others_dropped():
    tf = TestFile.parse(
        "class TestA:\n    def test_a(self):\n        assert 1\n\n"
        "class TestB:\n    def test_b(self):\n        assert self.x\n"
    )
    assert list(tf.tests) == ["test_a"]
    assert tf.tests["test_a"].source.startswith("def test_a():")
    assert tf.dropped_classes == ["TestB"]
