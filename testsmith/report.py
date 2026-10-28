"""Render a run as a Markdown report and a JSON record."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from .agent import RunOutcome
from .llm import UsageLog


def _pct(cov) -> str:
    return f"{cov.percent:.1f}%" if cov else "n/a"


def _fence(text: str, lang: str = "") -> str:
    text = text.rstrip()
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text}\n{fence}"


def render_markdown(
    run: RunOutcome,
    usage: UsageLog,
    *,
    base_url: str,
    mode: str = "live",
    test_path: str = "",
    run_at: datetime | None = None,
) -> str:
    when = (run_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    b, f = run.baseline, run.final
    delta = f"{f.percent - b.percent:+.1f} pts" if (b and f) else "n/a"
    t = usage.totals()
    out: list[str] = []
    out.append(f"# testsmith report: `{run.package}`")
    out.append("")
    if mode != "live":
        out.append(
            f"> Mode: **{mode}**. Model replies in this run came from a recorded script, not from a live "
            "endpoint. Everything else (sandboxed runs, coverage, triage rules, repros) executed for real."
        )
        out.append("")
    out.append(f"- Target: `{run.target}`  ")
    out.append(f"- Run at: {when}  ")
    out.append(f"- Writer model: `{run.writer_model}`  ")
    out.append(f"- Judge model: `{run.judge_model}`  ")
    out.append(f"- Endpoint: `{base_url}`" + (" (not contacted in a replay)" if mode != "live" else ""))
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append(f"| Line coverage before | {_pct(b)}{' (existing tests)' if run.existing_tests else ' (import only)'} |")
    out.append(f"| Line coverage after | {_pct(f)} |")
    out.append(f"| Change | {delta} |")
    out.append(f"| Tests written by the model | {run.test_count_written} (+ coverage follow-ups) |")
    out.append(f"| Kept, passing (test functions) | {len(run.kept_passing)} |")
    out.append(f"| Kept, exposing a likely bug (strict xfail) | {len(run.bugs)} |")
    out.append(f"| Repaired after triage | {len(run.repaired)} |")
    out.append(f"| Dropped | {len(run.dropped)} |")
    out.append(f"| Rounds | {len(run.rounds)} |")
    status = ", ".join(f"{v} {k}" for k, v in sorted(run.final_status.items())) or "n/a"
    out.append(f"| Final suite run | {status} |")
    cost = f"${t['cost_usd']:.4f}" + ("" if t["cost_complete"] else " (partial: some models unpriced)") if t["cost_usd"] is not None else "unknown (no published price for these models)"
    out.append(f"| Model calls | {t['calls']} calls, {t['prompt_tokens']:,} in / {t['completion_tokens']:,} out tokens, {t['latency_s']}s |")
    out.append(f"| Estimated cost | {cost} |")
    out.append("")
    if test_path:
        out.append(f"Tests written to `{test_path}`.")
        out.append("")

    out.append(f"## Likely bugs ({len(run.bugs)})")
    out.append("")
    if not run.bugs:
        out.append("No failing test survived triage as a likely bug.")
        out.append("")
    for i, bug in enumerate(run.bugs, 1):
        out.append(f"### {i}. `{bug.function}` ({bug.location})")
        out.append("")
        if bug.contract:
            out.append(f"**Promise:** {bug.contract}")
            out.append("")
        if bug.expected or bug.actual:
            out.append(f"**Expected:** {bug.expected or '?'}  ")
            out.append(f"**Actual:** {bug.actual or '?'}")
            out.append("")
        if bug.explanation:
            out.append(bug.explanation)
            out.append("")
        out.append(
            f"Evidence: failed identically on {bug.reproduced} separate runs; judge confidence {bug.confidence:.0%}; "
            f"kept as `{bug.test}` with a strict xfail marker."
        )
        out.append("")
        out.append("Failing assertion:")
        out.append("")
        out.append(_fence(bug.failure))
        out.append("")
        out.append("Minimal repro (the test):")
        out.append("")
        out.append(_fence(bug.test_source, "python"))
        out.append("")
        if bug.repro:
            out.append(f"Standalone repro, executed in the sandbox (exit {bug.repro_exit}):")
            out.append("")
            out.append(_fence(bug.repro, "python"))
            out.append("")
            if bug.repro_output:
                out.append(_fence(bug.repro_output, "text"))
                out.append("")

    if run.suspicions:
        out.append("## Unconfirmed suspicions")
        out.append("")
        out.append("The judge leaned towards a bug here but the evidence did not clear the bar, so these tests were dropped.")
        out.append("")
        for s in run.suspicions:
            out.append(f"- `{s['test']}`: {s['reason']}. Failure: `{s['failure']}`")
        out.append("")

    out.append("## Rounds")
    out.append("")
    out.append("| Round | Suite under test | Passed | Failing | Coverage | Then |")
    out.append("|---|---|---|---|---|---|")
    for r in run.rounds:
        tri = "; ".join(f"`{v.test}` -> {v.verdict} ({v.source})" for v in r.verdicts) or (r.note or "converged")
        cov = f"{r.coverage:.1f}%" if r.coverage is not None else "-"
        out.append(f"| {r.index} | {r.action} | {r.passed} | {r.failed} | {cov} | {tri} |")
    out.append("")

    if run.repaired:
        out.append("## Repaired tests")
        out.append("")
        for r in run.rounds:
            for v in r.verdicts:
                if v.verdict == "test_wrong" and v.test in run.repaired:
                    out.append(f"- `{v.test}` (round {r.index}, {v.source}): {v.reason}")
        out.append("")
    if run.dropped:
        out.append("## Dropped tests")
        out.append("")
        for name, why in run.dropped.items():
            out.append(f"- `{name}`: {why}")
        out.append("")

    out.append("## Model usage")
    out.append("")
    out.append("| # | Step | Model | Tokens in | Tokens out | Latency | Attempts | Cost |")
    out.append("|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(usage.calls, 1):
        cc = usage.cost_of(c)
        cs = f"${cc:.5f}" if cc is not None else "n/a"
        err = f" (error: {c.error[:60]})" if c.error else ""
        out.append(
            f"| {i} | {c.purpose}{err} | `{c.model}` | {c.prompt_tokens:,} | {c.completion_tokens:,} | {c.latency_s:.2f}s | {c.attempts} | {cs} |"
        )
    out.append("")
    if run.notes:
        out.append("## Notes")
        out.append("")
        for n in run.notes:
            out.append(f"- {n}")
        out.append("")
    out.append("## How this was checked")
    out.append("")
    out.append(
        "Every model-written test ran in a fresh subprocess on a temp-dir copy of the package, with a scrubbed "
        "environment, a per-test time limit and outbound network blocked. A failing test is only kept as a bug when "
        "the judge quotes the promise the code breaks, clears the confidence bar, the test calls package code, and the "
        "failure repeats identically on a rerun. Failures that can only be the test's fault (imports, wrong arguments, "
        "undefined names) are settled by rule and repaired. The final file was run once more with the existing suite "
        "to measure coverage."
    )
    out.append("")
    return "\n".join(out)


def to_json(run: RunOutcome, usage: UsageLog, **extra) -> str:
    data = asdict(run)
    data["baseline"] = run.baseline.to_dict() if run.baseline else None
    data["final"] = run.final.to_dict() if run.final else None
    data["usage"] = usage.to_dict()
    data.update(extra)
    return json.dumps(data, indent=2, default=str)
