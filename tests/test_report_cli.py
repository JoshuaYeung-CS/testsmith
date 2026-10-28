import json
from datetime import datetime, timezone

import pytest

from testsmith import cli
from testsmith.agent import BugFinding, RoundLog, RunOutcome, Verdict
from testsmith.llm import CallRecord, UsageLog
from testsmith.report import render_markdown, to_json
from testsmith.results import CoverageResult

from .conftest import PACKAGE, REPLAY


def sample_run():
    bug = BugFinding(
        test="test_round",
        function="order_total_cents",
        location="buggy_inventory/pricing.py:37",
        contract="the final total is rounded to the nearest cent.",
        expected="1088",
        actual="1087",
        explanation="truncates instead of rounding",
        confidence=0.91,
        failure="E   assert 1087 == 1088",
        test_source="def test_round():\n    assert order_total_cents([(333, 3)], 0.08875) == 1088",
        repro="print(1)",
        repro_output="1\n",
        repro_exit=0,
        reproduced=4,
    )
    return RunOutcome(
        target="examples/buggy_inventory",
        package="buggy_inventory",
        writer_model="nvidia/Nemotron-3_5-Lightning",
        judge_model="nvidia/Nemotron-3-Ultra-550b-a55b",
        baseline=CoverageResult(50.0, 24, 48),
        final=CoverageResult(97.9, 47, 48, {"buggy_inventory/stock.py": [27]}),
        test_file="test_buggy_inventory_testsmith.py",
        test_count_written=10,
        kept_passing=["test_a", "test_b"],
        bugs=[bug],
        repaired=["test_a"],
        dropped={"test_c": "unclear"},
        suspicions=[{"test": "test_c", "reason": "below bar", "failure": "E assert 1", "judged": {}}],
        rounds=[RoundLog(1, "initial tests", 2, 2, 80.0, [Verdict("test_a", "test_wrong", "rule", "wrong args", 1.0)])],
        final_status={"passed": 2, "xfailed": 1},
        notes=["a note"],
        existing_tests="examples/tests",
    )


def sample_usage():
    usage = UsageLog(prices={"nvidia/Nemotron-3-Ultra-550b-a55b": (1.0, 3.0)})
    usage.add(CallRecord("write", "nvidia/Nemotron-3_5-Lightning", 1000, 500, 1.2, 1))
    usage.add(CallRecord("triage", "nvidia/Nemotron-3-Ultra-550b-a55b", 2000, 400, 3.4, 2))
    return usage


def test_markdown_report_has_every_section():
    md = render_markdown(
        sample_run(), sample_usage(), base_url="https://api.tokenfactory.nebius.com/v1/",
        run_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
    )
    for needle in [
        "# testsmith report: `buggy_inventory`",
        "| Line coverage before | 50.0% (existing tests) |",
        "| Line coverage after | 97.9% |",
        "| Change | +47.9 pts |",
        "### 1. `order_total_cents` (buggy_inventory/pricing.py:37)",
        "**Promise:** the final total is rounded to the nearest cent.",
        "failed identically on 4 separate runs",
        "## Unconfirmed suspicions",
        "## Rounds",
        "## Repaired tests",
        "## Dropped tests",
        "| 2 | triage | `nvidia/Nemotron-3-Ultra-550b-a55b` | 2,000 | 400 | 3.40s | 2 | $0.00320 |",
        "(partial: some models unpriced)",
        "- a note",
    ]:
        assert needle in md, needle
    assert "recorded script" not in md  # live mode has no replay banner


def test_replay_mode_is_labelled():
    md = render_markdown(sample_run(), sample_usage(), base_url="x", mode="replay of demo.json")
    assert "replay of demo.json" in md and "recorded script" in md


def test_json_record_round_trips():
    data = json.loads(to_json(sample_run(), sample_usage(), mode="live"))
    assert data["bugs"][0]["function"] == "order_total_cents"
    assert data["final"]["percent"] == 97.9
    assert data["usage"]["totals"]["calls"] == 2
    assert data["mode"] == "live"


def test_cli_replay_writes_tests_report_and_record(tmp_path, capsys):
    out = tmp_path / "out"
    rc = cli.main([str(PACKAGE), "--replay", str(REPLAY), "--out", str(out), "--env-file", str(tmp_path / "none")])
    assert rc == 0
    assert (out / "test_buggy_inventory_testsmith.py").is_file()
    report = (out / "report.md").read_text()
    assert "## Likely bugs (3)" in report
    record = json.loads((out / "run.json").read_text())
    assert len(record["bugs"]) == 3
    printed = capsys.readouterr().out
    assert "3 likely bugs" in printed and "BUG needs_reorder" in printed


def test_cli_without_key_explains_what_to_do(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    rc = cli.main([str(PACKAGE), "--env-file", str(tmp_path / "none"), "--out", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "NEBIUS_API_KEY" in err and "--replay" in err


def test_cli_rejects_missing_target(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope"), "--env-file", str(tmp_path / "none")]) == 2
    assert cli.main(["--env-file", str(tmp_path / "none")]) == 2


def test_cli_check_lists_nemotron_models(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    served = ["nvidia/Nemotron-3_5-Lightning", "nvidia/Nemotron-3-Ultra-550b-a55b", "other/x"]
    monkeypatch.setattr(cli.NebiusLLM, "list_models", lambda self: served)
    assert cli.main(["--check", "--env-file", str(tmp_path / "none")]) == 0
    out = capsys.readouterr().out
    assert "2 from NVIDIA" in out and "writer: nvidia/Nemotron-3_5-Lightning  OK" in out
    monkeypatch.setattr(cli.NebiusLLM, "list_models", lambda self: ["other/x"])
    assert cli.main(["--check", "--env-file", str(tmp_path / "none")]) == 1


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_cli_help_and_version(flag):
    with pytest.raises(SystemExit) as exc:
        cli.main([flag])
    assert exc.value.code == 0
