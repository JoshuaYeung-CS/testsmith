"""The testsmith loop: read -> write tests -> run -> triage failures -> repair -> repeat."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import prompts
from .classifier import REAL_BUG, TEST_WRONG, decide, index_judgements, read_signals
from .codeinfo import collect_sources, contracts, contracts_for_test, render_sources, select_files
from .extract import ExtractionError, extract_json, extract_python
from .llm import LLM, ModelNotFound
from .results import CoverageResult, TestOutcome
from .sandbox import CANDIDATE_DIR, RunResult, Sandbox
from .testfile import TestFile

MAX_REPAIRS_PER_TEST = 2


@dataclass
class AgentConfig:
    target: Path
    existing_tests: Path | None = None
    module: str | None = None
    function: str | None = None
    max_rounds: int = 3
    target_coverage: float = 90.0
    timeout_s: float = 120.0
    per_test_s: int = 10
    min_confidence: float = 0.6
    writer_model: str = ""
    judge_model: str = ""


@dataclass
class BugFinding:
    test: str
    function: str
    location: str
    contract: str
    expected: str
    actual: str
    explanation: str
    confidence: float
    failure: str
    test_source: str
    repro: str = ""
    repro_output: str = ""
    repro_exit: int | None = None
    reproduced: int = 1  # runs in which this test failed the same way
    signature: str = ""


@dataclass
class Verdict:
    test: str
    verdict: str
    source: str
    reason: str
    confidence: float


@dataclass
class RoundLog:
    index: int
    action: str  # what the model was asked to do before this run
    passed: int = 0
    failed: int = 0
    coverage: float | None = None
    verdicts: list[Verdict] = field(default_factory=list)
    note: str = ""


@dataclass
class RunOutcome:
    target: str
    package: str
    writer_model: str
    judge_model: str
    baseline: CoverageResult | None
    final: CoverageResult | None
    test_file: str
    test_count_written: int
    kept_passing: list[str]
    bugs: list[BugFinding]
    repaired: list[str]
    dropped: dict[str, str]
    suspicions: list[dict]
    rounds: list[RoundLog]
    final_status: dict[str, int]
    notes: list[str]
    existing_tests: str | None


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


class Agent:
    def __init__(self, llm: LLM, config: AgentConfig, log: Callable[[str], None] = _log) -> None:
        self.llm = llm
        self.cfg = config
        self.log = log
        self.judge_model = config.judge_model or config.writer_model
        self.notes: list[str] = []

    # -- model helpers -----------------------------------------------------------------
    def _ask(self, purpose: str, model: str, messages: list[dict], parse, want: str):
        reply = self.llm.chat(messages, model=model, purpose=purpose)
        try:
            return parse(reply)
        except (ExtractionError, ValueError, SyntaxError) as exc:
            self.log(f"  reply unusable ({exc}); asking again")
            retry = messages + [{"role": "assistant", "content": reply[-6000:]}, prompts.correction(str(exc), want)]
            return parse(self.llm.chat(retry, model=model, purpose=purpose))

    def _ask_judge(self, messages: list[dict]):
        try:
            return self._ask("triage", self.judge_model, messages, extract_json, "the JSON array in a ```json block")
        except ModelNotFound:
            if self.judge_model == self.cfg.writer_model:
                raise
            self.notes.append(
                f"Judge model `{self.judge_model}` is not served at this endpoint; triage fell back to `{self.cfg.writer_model}`."
            )
            self.log(f"  judge model {self.judge_model} unavailable, falling back to {self.cfg.writer_model}")
            self.judge_model = self.cfg.writer_model
            return self._ask("triage", self.judge_model, messages, extract_json, "the JSON array in a ```json block")

    @staticmethod
    def _parse_tests(reply: str) -> TestFile:
        tf = TestFile.parse(extract_python(reply))
        if not tf.tests:
            raise ExtractionError("no top-level test_ functions in the file")
        return tf

    # -- main loop ---------------------------------------------------------------------
    def run(self) -> tuple[RunOutcome, str]:
        cfg = self.cfg
        with Sandbox(cfg.target, cfg.existing_tests, timeout_s=cfg.timeout_s, per_test_s=cfg.per_test_s) as sb:
            return self._run(sb)

    def _run(self, sb: Sandbox) -> tuple[RunOutcome, str]:
        cfg = self.cfg
        pkg = sb.name
        test_name = f"test_{pkg}_testsmith.py"
        rel_test = f"{CANDIDATE_DIR}/{test_name}"
        existing = sb.existing_test_paths()

        self.log(f"[1/5] reading {pkg} and measuring baseline coverage")
        files = collect_sources(sb.root, pkg, sb.is_module)
        focus = select_files(files, cfg.module)
        table = contracts(files)
        if cfg.function and cfg.function not in table:
            raise ValueError(f"function {cfg.function!r} not found in {pkg}")
        listing = render_sources(focus, cfg.function)
        focus_paths = {f.path for f in focus}
        baseline = sb.baseline().coverage
        if baseline:
            self.log(f"      baseline line coverage {baseline.percent:.1f}% ({'existing tests' if existing else 'import only'})")

        self.log(f"[2/5] writing tests with {cfg.writer_model}")
        hint = f"Existing tests already cover {baseline.percent:.0f}% of lines." if baseline else ""
        tf = self._ask(
            "write", cfg.writer_model, prompts.write_tests(listing, hint), self._parse_tests, "the complete test file in one ```python block"
        )
        written = len(tf.tests)
        self.log(f"      {written} tests written")

        bugs: dict[str, BugFinding] = {}
        dropped: dict[str, str] = {}
        repaired: list[str] = []
        suspicions: list[dict] = []
        rounds: list[RoundLog] = []
        action = "initial tests"
        coverage_asks = 0

        self.log(f"[3/5] run / triage / repair loop (up to {cfg.max_rounds} rounds)")
        for r in range(1, cfg.max_rounds + 1):
            sb.write_test_file(test_name, tf.render())
            run = sb.run_pytest(existing + [rel_test], tag=f"round{r}")
            mine = [o for o in run.outcomes if o.module.startswith(CANDIDATE_DIR) or o.collection_error]
            log = RoundLog(r, action, coverage=run.coverage.percent if run.coverage else None)
            rounds.append(log)

            if run.timed_out or any(o.collection_error for o in mine) or (not mine and run.returncode not in (0, 5)):
                problem = run.output if not run.timed_out else "The whole run hit the time limit.\n" + run.output
                log.note = "test file did not run; asked for a corrected file"
                self.log(f"      round {r}: test file did not run, asking for a fix")
                fixed = self._ask(
                    "repair", cfg.writer_model, prompts.repair_file(listing, tf.render(), problem), self._parse_tests,
                    "the complete corrected test file in one ```python block",
                )
                for name, finding in bugs.items():
                    if name in fixed.tests:
                        fixed.tests[name].bug_reason = None
                tf = fixed
                action = "corrected file"
                continue

            failing = self._failing(mine)
            log.passed = sum(1 for o in mine if o.status == "passed")
            log.failed = len(failing)
            fresh: dict[str, TestOutcome] = {}
            for name, outcome in failing.items():
                known = bugs.get(name)
                if known and known.signature == outcome.signature:
                    known.reproduced += 1
                    continue
                if known:  # failure changed shape: re-judge
                    bugs.pop(name)
                fresh[name] = outcome
            for name in list(bugs):
                if name not in failing:  # a "bug" test now passes: it was not a bug
                    bugs.pop(name)
                    log.note = (log.note + f" {name} stopped failing;").strip()

            self.log(
                f"      round {r}: {log.passed} passed, {log.failed} failing ({len(fresh)} new), "
                f"coverage {log.coverage if log.coverage is not None else 0:.1f}%"
            )

            if not fresh:
                cov = run.coverage
                gaps = {p: ls for p, ls in (cov.missing if cov else {}).items() if p in focus_paths}
                if cov and cov.percent < cfg.target_coverage and gaps and r < cfg.max_rounds and coverage_asks < 2:
                    coverage_asks += 1
                    self.log(f"      coverage {cov.percent:.1f}% < {cfg.target_coverage:.0f}% target, asking for tests on uncovered lines")
                    reply_tf = self._ask(
                        "coverage", cfg.writer_model,
                        prompts.more_tests(listing, tf.render(), gaps, cov.percent),
                        self._parse_tests, "new test functions in one ```python block",
                    )
                    added = tf.merge(reply_tf.render(), origin="coverage")
                    log.note = f"coverage below target: asked for tests on uncovered lines, merged {len(added)}"
                    action = "coverage tests added"
                    continue
                break

            cases = []
            sigs = {}
            for name, outcome in fresh.items():
                src = tf.source_of(name)
                sigs[name] = read_signals(outcome, pkg)
                cases.append(
                    {
                        "name": name,
                        "source": src,
                        "failure": (outcome.details or outcome.message),
                        "contracts": contracts_for_test(src, table),
                    }
                )
            # Every fresh failure goes to the judge, even rule-settled ones: the reply
            # carries the repaired test, and the rule still overrides its verdict.
            judged = {}
            if cases:
                try:
                    judged = index_judgements(self._ask_judge(prompts.triage(cases)))
                except (ExtractionError, ValueError) as exc:
                    self.notes.append(f"Round {r}: triage reply could not be parsed ({exc}).")

            for c in cases:
                name = c["name"]
                outcome = fresh[name]
                d = decide(
                    sigs[name], judged.get(name), exercises_package=bool(c["contracts"]), min_confidence=cfg.min_confidence
                )
                log.verdicts.append(Verdict(name, d.verdict, d.source, d.reason, d.confidence))
                if d.verdict == TEST_WRONG:
                    test = tf.tests.get(name)
                    if d.fixed_test and test and test.repairs < MAX_REPAIRS_PER_TEST:
                        try:
                            tf.merge(extract_python(d.fixed_test) if "```" in d.fixed_test else d.fixed_test, replace=True)
                            if name not in tf.tests or tf.tests[name].source == c["source"]:
                                raise ExtractionError("repair did not redefine the test")
                            repaired.append(name)
                            continue
                        except (ExtractionError, SyntaxError) as exc:
                            self.log(f"      repair for {name} unusable ({exc})")
                    tf.drop(name)
                    dropped[name] = f"test was wrong ({d.reason}); no usable repair"
                elif d.verdict == REAL_BUG:
                    confirm = sb.run_pytest([f"{rel_test}::{name}"], coverage=False, tag=f"confirm-{name}")
                    again = self._failing([o for o in confirm.outcomes if o.name == name]).get(name)
                    if again is None or again.signature != outcome.signature:
                        tf.drop(name)
                        dropped[name] = "flagged as a bug but did not fail the same way on rerun (flaky)"
                        continue
                    bugs[name] = self._finding(name, c, d, outcome, table)
                else:
                    tf.drop(name)
                    dropped[name] = f"unclear whether test or code is wrong ({d.reason})"
                    if d.judged and str(d.judged.get("verdict", "")).lower().startswith("real"):
                        suspicions.append({"test": name, "reason": d.reason, "failure": outcome.signature, "judged": d.judged})
            action = "repairs applied"

        self.log("[4/5] finalising: keep passing tests and confirmed bugs")
        final_run, final_status = self._finalise(sb, tf, bugs, dropped, existing, rel_test, test_name)

        self.log("[5/5] running bug repros")
        for finding in bugs.values():
            if finding.repro.strip():
                finding.repro_exit, finding.repro_output = sb.run_snippet(finding.repro)

        header = (
            f'"""Tests for {pkg}, written and verified by testsmith.\n\n'
            f"Tests marked xfail(strict=True) document likely bugs: they fail today and will\n"
            f'start failing the suite (XPASS) once the bug is fixed, so remove the marker then.\n"""'
        )
        final_code = tf.render(header)
        outcome = RunOutcome(
            target=str(cfg.target),
            package=pkg,
            writer_model=cfg.writer_model,
            judge_model=self.judge_model,
            baseline=baseline,
            final=final_run.coverage,
            test_file=test_name,
            test_count_written=written,
            kept_passing=[n for n, t in tf.tests.items() if not t.bug_reason],
            bugs=list(bugs.values()),
            repaired=sorted(set(repaired) & set(tf.tests)),
            dropped=dropped,
            suspicions=suspicions,
            rounds=rounds,
            final_status=final_status,
            notes=self.notes + ([f"Dropped test classes that use self: {', '.join(tf.dropped_classes)}"] if tf.dropped_classes else []),
            existing_tests=str(cfg.existing_tests) if existing else None,
        )
        return outcome, final_code

    # -- pieces ------------------------------------------------------------------------
    @staticmethod
    def _failing(outcomes: list[TestOutcome]) -> dict[str, TestOutcome]:
        failing: dict[str, TestOutcome] = {}
        for o in outcomes:
            if o.failed and not o.collection_error and o.name not in failing:
                failing[o.name] = o
        return failing

    def _finding(self, name, case, d, outcome, table) -> BugFinding:
        j = d.judged or {}
        target = case["contracts"][0] if case["contracts"] else None
        e_lines = [ln for ln in (outcome.details or "").splitlines() if ln.startswith("E ")]
        return BugFinding(
            test=name,
            function=target.qualname if target else "?",
            location=f"{target.path}:{target.lineno}" if target else "?",
            contract=str(j.get("contract", "")).strip(),
            expected=str(j.get("expected", "")).strip(),
            actual=str(j.get("actual", "")).strip(),
            explanation=str(j.get("explanation", "")).strip(),
            confidence=d.confidence,
            failure="\n".join(e_lines[:6]) or outcome.message,
            test_source=case["source"],
            repro=str(j.get("repro", "") or "").strip(),
            reproduced=2,  # the round run plus the confirmation rerun
            signature=outcome.signature,
        )

    def _finalise(self, sb, tf, bugs, dropped, existing, rel_test, test_name) -> tuple[RunResult, dict[str, int]]:
        for name, finding in bugs.items():
            tf.mark_bug(name, f"{finding.function}: {finding.explanation or finding.contract}")
        run = None
        for attempt in range(3):
            sb.write_test_file(test_name, tf.render())
            run = sb.run_pytest(existing + [rel_test], tag=f"final{attempt}")
            mine = [o for o in run.outcomes if o.module.startswith(CANDIDATE_DIR)]
            bad = self._failing(mine)
            if not bad and not run.timed_out:
                break
            for name, o in bad.items():
                if name in bugs:
                    bugs.pop(name)
                    dropped[name] = "marked as a bug but passed in the final run (XPASS): not reproducible"
                else:
                    dropped[name] = f"still failing in the final run ({o.signature[:120]})"
                tf.drop(name)
        mine = [o for o in run.outcomes if o.module.startswith(CANDIDATE_DIR)]
        status: dict[str, int] = {}
        for o in mine:
            status[o.status] = status.get(o.status, 0) + 1
            if o.status == "xfailed" and o.name in bugs:
                bugs[o.name].reproduced += 1
        return run, status
