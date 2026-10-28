"""The full agent loop on the bundled buggy package, with scripted model replies."""

import pytest

from testsmith.agent import Agent, AgentConfig
from testsmith.fake import ScriptedLLM, ScriptExhausted
from testsmith.sandbox import Sandbox

from .conftest import EXISTING, PACKAGE, json_reply, py_reply, tree_digest

WRITER = "nvidia/Nemotron-3_5-Lightning"
JUDGE = "nvidia/Nemotron-3-Ultra-550b-a55b"


def config(**kw):
    base = dict(target=PACKAGE, existing_tests=EXISTING, writer_model=WRITER, judge_model=JUDGE, per_test_s=5)
    base.update(kw)
    return AgentConfig(**base)


def quiet(_msg):
    pass


def run_agent(responses, **kw):
    llm = ScriptedLLM(responses, missing_models=kw.pop("missing_models", None))
    outcome, code = Agent(llm, config(**kw), log=quiet).run()
    return outcome, code, llm


def run_final_file(code):
    """Run the emitted test file against a fresh copy of the package."""
    with Sandbox(PACKAGE) as box:
        box.write_test_file("test_final.py", code)
        result = box.run_pytest(["candidates/test_final.py"], coverage=False)
    return {o.status for o in result.outcomes}


# -- the recorded demo ------------------------------------------------------------------
@pytest.fixture(scope="module")
def demo():
    import json

    from .conftest import REPLAY

    before = tree_digest(PACKAGE)
    outcome, code, llm = run_agent(json.loads(REPLAY.read_text())["responses"])
    return outcome, code, llm, before


def test_demo_finds_the_three_planted_bugs(demo):
    outcome, *_ = demo
    assert {b.function for b in outcome.bugs} == {"Inventory.remove", "needs_reorder", "order_total_cents"}
    for bug in outcome.bugs:
        assert bug.contract and bug.reproduced >= 3
        assert bug.repro_exit == 0 and bug.repro_output.strip()


def test_demo_repairs_the_wrong_tests_instead_of_reporting_them(demo):
    outcome, *_ = demo
    assert set(outcome.repaired) == {"test_receive_returns_new_quantity", "test_line_total_applies_discount"}
    verdicts = {v.test: (v.verdict, v.source) for v in outcome.rounds[0].verdicts}
    assert verdicts["test_receive_returns_new_quantity"] == ("test_wrong", "rule")
    assert verdicts["test_line_total_applies_discount"] == ("test_wrong", "model")
    assert not outcome.dropped


def test_demo_raises_coverage_and_ends_green(demo):
    outcome, code, *_ = demo
    assert outcome.final.percent > outcome.baseline.percent
    assert outcome.final.percent >= 90
    assert "failed" not in outcome.final_status and "error" not in outcome.final_status
    assert outcome.final_status["xfailed"] == 3
    assert run_final_file(code) == {"passed", "xfailed"}
    assert code.count("@pytest.mark.xfail(strict=True") == 3


def test_demo_uses_both_models_and_logs_usage(demo):
    outcome, _code, llm, _ = demo
    assert [(c.purpose, c.model) for c in llm.usage.calls] == [("write", WRITER), ("triage", JUDGE), ("coverage", WRITER)]
    assert llm.usage.totals()["total_tokens"] > 0
    assert llm.remaining() == {}


def test_demo_leaves_the_package_untouched(demo):
    *_, before = demo
    assert tree_digest(PACKAGE) == before


# -- branches ---------------------------------------------------------------------------
GOOD = """
from buggy_inventory.pricing import bulk_discount_percent


def test_tier():
    \"\"\"10 units get 5%.\"\"\"
    assert bulk_discount_percent(10) == 5
"""


def test_unrunnable_file_is_sent_back_for_a_fix():
    bad = "from buggy_inventory import does_not_exist\n\n\ndef test_x():\n    assert does_not_exist\n"
    outcome, code, llm = run_agent({"write": [py_reply(bad)], "repair": [py_reply(GOOD)]}, target_coverage=0)
    assert "did not run" in outcome.rounds[0].note
    assert outcome.kept_passing == ["test_tier"]
    assert [c["purpose"] for c in llm.calls] == ["write", "repair"]


def test_unusable_reply_gets_one_corrective_retry():
    outcome, _, llm = run_agent({"write": ["I would test the tiers first.", py_reply(GOOD)]}, target_coverage=0)
    assert outcome.kept_passing == ["test_tier"]
    second = llm.calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and "could not use that reply" in second[-1]["content"]


def test_judge_falls_back_to_writer_when_not_served():
    failing = GOOD.replace("== 5", "== 7")
    verdict = [{"test": "test_tier", "verdict": "test_wrong", "confidence": 0.9, "explanation": "10 units is the 5% tier",
                "fixed_test": "def test_tier():\n    assert bulk_discount_percent(10) == 5"}]
    outcome, _, llm = run_agent(
        {"write": [py_reply(failing)], "triage": [json_reply(verdict)]}, target_coverage=0, missing_models={JUDGE}
    )
    assert outcome.judge_model == WRITER
    assert any("fell back" in n for n in outcome.notes)
    assert outcome.repaired == ["test_tier"]


def test_weak_bug_verdict_is_dropped_as_a_suspicion():
    failing = GOOD.replace("== 5", "== 7")
    verdict = [{"test": "test_tier", "verdict": "real_bug", "confidence": 0.3, "contract": "10-49 units get 5%"}]
    outcome, code, _ = run_agent({"write": [py_reply(failing)], "triage": [json_reply(verdict)]}, target_coverage=0)
    assert not outcome.bugs
    assert "test_tier" in outcome.dropped
    assert outcome.suspicions[0]["test"] == "test_tier"
    assert "def test_tier" not in code


def test_test_that_stays_wrong_after_repairs_is_dropped():
    failing = GOOD.replace("== 5", "== 7")
    replies = [
        json_reply([{"test": "test_tier", "verdict": "test_wrong", "confidence": 0.8,
                     "fixed_test": f"def test_tier():\n    assert bulk_discount_percent(10) == {n}"}])
        for n in (6, 8, 9)
    ]
    outcome, code, llm = run_agent({"write": [py_reply(failing)], "triage": replies}, target_coverage=0, max_rounds=3)
    assert "test_tier" in outcome.dropped and not outcome.bugs
    assert "def test_tier" not in code
    assert sum(1 for c in llm.calls if c["purpose"] == "triage") == 3


def test_flaky_bug_is_not_reported():
    flaky = """
from pathlib import Path

from buggy_inventory.pricing import bulk_discount_percent


def test_flaky():
    marker = Path("flaky-marker")
    first = not marker.exists()
    marker.write_text("seen")
    assert bulk_discount_percent(10) == (99 if first else 5)
"""
    verdict = [{"test": "test_flaky", "verdict": "real_bug", "confidence": 0.9, "contract": "10-49 units get 5%"}]
    outcome, _, _ = run_agent({"write": [py_reply(flaky)], "triage": [json_reply(verdict)]}, target_coverage=0)
    assert not outcome.bugs
    assert "flaky" in outcome.dropped["test_flaky"]


def test_focus_on_one_function_reaches_the_prompt():
    _, _, llm = run_agent({"write": [py_reply(GOOD)]}, function="bulk_discount_percent", module="pricing", target_coverage=0)
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "Focus: write tests for `bulk_discount_percent` only" in prompt
    assert "buggy_inventory/pricing.py" in prompt and "buggy_inventory/stock.py" not in prompt


def test_unknown_function_is_rejected():
    with pytest.raises(ValueError, match="not found"):
        run_agent({"write": [py_reply(GOOD)]}, function="does_not_exist")


def test_two_unusable_replies_abort_the_run():
    from testsmith.extract import ExtractionError

    with pytest.raises(ExtractionError):
        run_agent({"write": ["no code", "still no code"]})


def test_script_exhaustion_surfaces():
    with pytest.raises(ScriptExhausted):
        run_agent({"write": ["no code"]})


def test_identical_repair_is_not_accepted():
    failing = GOOD.replace("== 5", "== 7")
    same = [{"test": "test_tier", "verdict": "test_wrong", "confidence": 0.8, "fixed_test": failing.split("\n\n\n", 1)[1]}]
    outcome, _, _ = run_agent({"write": [py_reply(failing)], "triage": [json_reply(same)]}, target_coverage=0)
    assert "no usable repair" in outcome.dropped["test_tier"]


def test_full_run_over_http_through_the_real_client():
    """Same demo, but every model call goes through NebiusLLM and the openai client to a local stub server."""
    import json

    from testsmith.config import Settings
    from testsmith.llm import NebiusLLM, UsageLog

    from .conftest import REPLAY
    from .stub_server import StubServer

    r = json.loads(REPLAY.read_text())["responses"]
    plan = [r["write"][0], r["triage"][0], r["coverage"][0]]  # the order the agent asks
    with StubServer(plan, models=[WRITER, JUDGE]) as srv:
        settings = Settings.from_env(env={"NEBIUS_API_KEY": "test-key", "NEBIUS_BASE_URL": srv.base_url})
        llm = NebiusLLM(settings, UsageLog(prices=settings.prices), sleep=lambda s: None)
        outcome, code = Agent(llm, config(), log=quiet).run()
    assert [req["body"]["model"] for req in srv.requests] == [WRITER, JUDGE, WRITER]
    assert len(outcome.bugs) == 3
    assert llm.usage.totals()["prompt_tokens"] == 3 * 120
