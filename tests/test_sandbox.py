import os

import pytest

from testsmith.results import parse_junit
from testsmith.sandbox import Sandbox

from .conftest import EXISTING, PACKAGE, tree_digest


@pytest.fixture
def sb():
    box = Sandbox(PACKAGE, EXISTING, timeout_s=60, per_test_s=3)
    yield box
    box.close()


def run(sb, code, **kw):
    sb.write_test_file("test_t.py", code)
    return sb.run_pytest(["candidates/test_t.py"], **kw)


def status(result):
    return {o.case: o.status for o in result.outcomes}


def test_runs_on_a_copy_and_never_touches_the_original(sb):
    before = tree_digest(PACKAGE)
    r = run(
        sb,
        "from pathlib import Path\nimport buggy_inventory\n\n"
        "def test_vandal():\n    Path(buggy_inventory.__file__).write_text('raise SystemExit')\n",
    )
    assert status(r) == {"test_vandal": "passed"}
    assert tree_digest(PACKAGE) == before
    assert str(sb.root) in str(sb.source_path)


def test_reports_each_status(sb):
    r = run(
        sb,
        "import pytest\n\n"
        "def test_pass():\n    assert True\n\n"
        "def test_fail():\n    assert 1 == 2\n\n"
        "@pytest.mark.xfail(strict=True)\ndef test_xfail():\n    assert False\n\n"
        "@pytest.mark.xfail(strict=True)\ndef test_xpass():\n    assert True\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_param(n):\n    assert n == 1\n\n"
        "@pytest.fixture\ndef broken():\n    raise RuntimeError('setup')\n\n"
        "def test_error(broken):\n    pass\n",
        coverage=False,
    )
    got = status(r)
    assert got["test_pass"] == "passed"
    assert got["test_fail"] == "failed"
    assert got["test_xfail"] == "xfailed"
    assert got["test_xpass"] == "failed"  # strict XPASS fails the run
    assert got["test_param[1]"] == "passed" and got["test_param[2]"] == "failed"
    assert got["test_error"] == "error"
    fail = next(o for o in r.outcomes if o.case == "test_fail")
    assert "assert 1 == 2" in fail.signature


def test_collection_error_is_flagged(sb):
    r = run(sb, "import buggy_inventory.nope\n\ndef test_x():\n    pass\n", coverage=False)
    assert r.collection_errors


def test_network_is_blocked(sb):
    r = run(
        sb,
        "import socket, urllib.request\nimport pytest\n\n"
        "def test_socket():\n    with pytest.raises(OSError, match='disabled'):\n        socket.create_connection(('example.com', 80), timeout=2)\n\n"
        "def test_urllib():\n    with pytest.raises(OSError):\n        urllib.request.urlopen('http://example.com', timeout=2)\n",
        coverage=False,
    )
    assert set(status(r).values()) == {"passed"}


def test_secrets_are_not_passed_to_tests(sb, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "super-secret")
    r = run(sb, "import os\n\ndef test_env():\n    assert 'NEBIUS_API_KEY' not in os.environ\n", coverage=False)
    assert status(r) == {"test_env": "passed"}


def test_per_test_time_limit(sb):
    r = run(sb, "def test_hang():\n    while True:\n        pass\n\n\ndef test_ok():\n    assert True\n", coverage=False)
    assert status(r) == {"test_hang": "failed", "test_ok": "passed"}
    assert "per-test limit" in next(o for o in r.outcomes if o.name == "test_hang").details


@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_whole_run_timeout_kills_the_process():
    box = Sandbox(PACKAGE, timeout_s=3, per_test_s=60)
    try:
        r = run(box, "import time\n\ndef test_sleep():\n    time.sleep(30)\n", coverage=False)
        assert r.timed_out and r.duration_s < 15
    finally:
        box.close()


def test_baseline_uses_existing_tests_then_import_only():
    with Sandbox(PACKAGE, EXISTING) as box:
        with_tests = box.baseline().coverage
    with Sandbox(PACKAGE) as box:
        import_only = box.baseline().coverage
    assert with_tests.percent > import_only.percent > 0
    assert "buggy_inventory/stock.py" in import_only.missing


def test_single_module_target(tmp_path):
    mod = tmp_path / "solo.py"
    mod.write_text("def double(x):\n    return 2 * x\n")
    with Sandbox(mod) as box:
        r = run(box, "from solo import double\n\ndef test_d():\n    assert double(2) == 4\n")
    assert status(r) == {"test_d": "passed"}
    assert r.coverage.percent == 100.0


def test_snippet_runs_in_sandbox(sb):
    rc, out = sb.run_snippet("from buggy_inventory import bulk_discount_percent\nprint(bulk_discount_percent(50))")
    assert rc == 0 and out.strip() == "10"


def test_parse_junit_missing_or_corrupt(tmp_path):
    assert parse_junit(tmp_path / "missing.xml") == []
    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuite><testcase")
    assert parse_junit(bad) == []
