"""Prompt builders. Kept in one place so the contract with the model is easy to audit."""

from __future__ import annotations

from .codeinfo import Contract, numbered

SYSTEM = """You are testsmith, a senior Python test engineer who writes pytest suites that find real bugs.

Ground rules:
- A test encodes the CONTRACT: what the docstring, the function name, type hints and plain domain sense promise. It does not encode whatever the code happens to do. When code and docstring disagree, test the docstring.
- Top-level `def test_...` functions only (no classes). Plain `assert`, `pytest.raises`, `pytest.mark.parametrize` and the `tmp_path` fixture are fine.
- One behaviour per test. The first line of every test is a one-line docstring naming the promise it checks.
- Probe the places bugs hide: boundaries and thresholds (at, just below, just above), invalid input and the exact exception type, rounding of money, state after a failed operation.
- Deterministic and hermetic: no network, no sleep, no unseeded randomness, no files outside tmp_path.
- Import the code by the import path shown in each file header."""


def write_tests(listing: str, api_hint: str) -> list[dict]:
    user = f"""Write a pytest file for this package.

{listing}

{api_hint}
Reply with ONE ```python fenced block containing the complete test file and nothing else after it."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def repair_file(listing: str, current: str, problem: str) -> list[dict]:
    user = f"""The test file below does not run. Fix it so pytest can collect and run every test.
Keep each test's intent; only fix imports, syntax, names and API usage.

Package under test:
{listing}

Current test file:
```python
{current}
```

What pytest reported:
```
{problem[-6000:]}
```

Reply with ONE ```python fenced block containing the complete corrected test file."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


TRIAGE_SYSTEM = """You are the reviewer in a test-writing loop. For each failing test you decide which side is wrong.

- "real_bug": the package breaks a promise it makes. You must quote the promise (docstring sentence, or the name/type contract) in "contract", and show the expected vs actual value. Only choose this when the test's expectation follows from that promise.
- "test_wrong": the test assumed something the package never promised, misused the API (wrong argument names, wrong exception type when the docstring names another, wrong arithmetic), or is itself buggy. Supply "fixed_test": the complete corrected test function (plus any import lines it needs). Never "fix" a test by weakening it to match code that contradicts its own docstring.

Be calibrated: "confidence" is your probability (0-1) that the verdict is right."""


def triage(cases: list[dict]) -> list[dict]:
    parts = []
    for c in cases:
        contracts: list[Contract] = c["contracts"]
        ctx = "\n\n".join(
            f"`{k.qualname}` in {k.path} line {k.lineno}:\n```python\n{numbered(k.source)}\n```" for k in contracts
        ) or "(the test calls no package function directly)"
        parts.append(
            f"""## {c['name']}
Test:
```python
{c['source']}
```
Failure:
```
{c['failure'][-2500:]}
```
Package code it exercises:
{ctx}"""
        )
    body = "\n\n".join(parts)
    user = f"""{body}

Reply with a ```json fenced block holding a JSON array, one object per test above:
[{{"test": "<name>", "verdict": "real_bug" | "test_wrong", "confidence": 0.0-1.0,
  "contract": "<quoted promise>", "expected": "<value or behaviour>", "actual": "<value or behaviour>",
  "explanation": "<one or two sentences>",
  "fixed_test": "<full corrected test function, test_wrong only, else empty>",
  "repro": "<3-6 line Python script that prints the wrong result, real_bug only, else empty>"}}]"""
    return [{"role": "system", "content": TRIAGE_SYSTEM}, {"role": "user", "content": user}]


def more_tests(listing: str, current: str, missing: dict[str, list[int]], percent: float) -> list[dict]:
    gaps = "\n".join(f"- {path}: lines {_ranges(lines)}" for path, lines in missing.items())
    user = f"""The current suite covers {percent:.1f}% of the package lines. These lines never run:
{gaps}

Package (line numbers match the list above):
{listing}

Current tests (do not repeat them):
```python
{current}
```

Write NEW tests that execute the uncovered lines and check the behaviour the docstrings promise there.
Reply with ONE ```python fenced block containing only the new test functions and any imports they need."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _ranges(lines: list[int]) -> str:
    out = []
    start = prev = None
    for n in lines:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)


def correction(reason: str, want: str) -> dict:
    return {
        "role": "user",
        "content": f"I could not use that reply ({reason}). Reply again with only {want}.",
    }
