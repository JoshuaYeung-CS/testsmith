"""Command line entry point: ``testsmith PATH [options]``."""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

from . import __version__
from .agent import Agent, AgentConfig
from .config import Settings
from .llm import NebiusLLM, UsageLog
from .report import render_markdown, to_json


def _guess_existing_tests(target: Path) -> Path | None:
    for cand in (target.parent / "tests", target.parent / "test"):
        if cand.is_dir():
            return cand
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="testsmith",
        description="Write, run and repair pytest tests for a Python package with NVIDIA Nemotron on Nebius Token Factory, "
        "and report the real bugs the tests expose.",
    )
    p.add_argument("target", nargs="?", help="path to a package directory (or a single module file)")
    p.add_argument("--module", help="limit to one module, e.g. stock or buggy_inventory.stock")
    p.add_argument("--function", help="limit to one function or method, e.g. needs_reorder or Inventory.remove")
    p.add_argument("--tests", type=Path, help="existing tests directory (default: a tests/ folder next to the package)")
    p.add_argument("--no-existing", action="store_true", help="ignore existing tests; baseline is a bare import")
    p.add_argument("--rounds", type=int, default=3, help="max run/triage/repair rounds (default 3)")
    p.add_argument("--target-coverage", type=float, default=90.0, help="ask for more tests below this line coverage (default 90)")
    p.add_argument("--timeout", type=float, default=120.0, help="wall-clock limit per pytest run, seconds (default 120)")
    p.add_argument("--per-test-timeout", type=int, default=10, help="limit per test, seconds (default 10)")
    p.add_argument("--min-confidence", type=float, default=0.6, help="judge confidence needed to report a bug (default 0.6)")
    p.add_argument("--out", type=Path, default=Path("testsmith-out"), help="output directory (default ./testsmith-out)")
    p.add_argument("--model", help="writer model id (default $TESTSMITH_MODEL or nvidia/Nemotron-3_5-Lightning)")
    p.add_argument("--judge-model", help="triage model id (default $TESTSMITH_JUDGE_MODEL or nvidia/Nemotron-3-Ultra-550b-a55b)")
    p.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file to read (default ./.env)")
    p.add_argument("--replay", type=Path, help="offline: answer model calls from a recorded script instead of the API")
    p.add_argument("--check", action="store_true", help="check the API key and that the configured Nemotron models are served")
    p.add_argument("--version", action="version", version=f"testsmith {__version__}")
    return p


def check(settings: Settings) -> int:
    llm = NebiusLLM(settings)
    ids = llm.list_models()
    nvidia = [m for m in ids if m.lower().startswith("nvidia/")]
    print(f"Endpoint {settings.base_url}: {len(ids)} models, {len(nvidia)} from NVIDIA")
    for m in nvidia:
        print(f"  {m}")
    ok = True
    for label, mid in (("writer", settings.model), ("judge", settings.judge_model)):
        present = mid in ids
        ok &= present or label == "judge"
        print(f"{label:>6}: {mid}  {'OK' if present else 'NOT SERVED'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env(dotenv=args.env_file)
    if args.model:
        settings.model = args.model
    if args.judge_model:
        settings.judge_model = args.judge_model

    if args.check:
        return check(settings)
    if not args.target:
        build_parser().print_usage(sys.stderr)
        print("testsmith: error: a target path is required", file=sys.stderr)
        return 2

    target = Path(args.target)
    if not target.exists():
        print(f"testsmith: error: {target} does not exist", file=sys.stderr)
        return 2
    existing = None if args.no_existing else (args.tests or _guess_existing_tests(Path(os.path.normpath(target.absolute()))))
    if existing is not None and existing.is_absolute() and existing.is_relative_to(Path.cwd()):
        existing = existing.relative_to(Path.cwd())

    usage = UsageLog(prices=settings.prices, echo=True)
    if args.replay:
        from .fake import ScriptedLLM

        llm = ScriptedLLM.from_file(args.replay, usage)
        mode = f"replay of {args.replay.name}"
    else:
        try:
            llm = NebiusLLM(settings, usage)
        except RuntimeError as exc:
            print(f"testsmith: error: {exc}", file=sys.stderr)
            return 2
        mode = "live"

    cfg = AgentConfig(
        target=target,
        existing_tests=existing,
        module=args.module,
        function=args.function,
        max_rounds=args.rounds,
        target_coverage=args.target_coverage,
        timeout_s=args.timeout,
        per_test_s=args.per_test_timeout,
        min_confidence=args.min_confidence,
        writer_model=settings.model,
        judge_model=settings.judge_model,
    )
    try:
        run, code = Agent(llm, cfg).run()
    except Exception as exc:  # surface a clean message; the traceback is not useful to users
        print(f"testsmith: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    test_path = args.out / run.test_file
    test_path.write_text(code, encoding="utf-8")
    report = render_markdown(run, usage, base_url=settings.base_url, mode=mode, test_path=str(test_path))
    (args.out / "report.md").write_text(report, encoding="utf-8")
    (args.out / "run.json").write_text(to_json(run, usage, base_url=settings.base_url, mode=mode), encoding="utf-8")

    b, f = run.baseline, run.final
    cov = f"{b.percent:.1f}% -> {f.percent:.1f}%" if b and f else "n/a"
    print(f"\ncoverage {cov} | {len(run.kept_passing)} passing tests kept | {len(run.bugs)} likely bugs")
    for bug in run.bugs:
        print(f"  BUG {bug.function} ({bug.location})")
        for line in textwrap.wrap(bug.explanation or bug.contract, 92):
            print(f"      {line}")
    print(f"tests:  {test_path}\nreport: {args.out / 'report.md'}")
    t = usage.totals()
    print(f"model:  {t['calls']} calls, {t['total_tokens']:,} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
