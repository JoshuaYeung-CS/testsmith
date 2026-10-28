"""Decide, for each failing test, whether the test or the code is wrong.

Two layers:

1. Deterministic signals read from the traceback. Some failures can only be the
   test's fault (it does not import, calls a function with the wrong arguments, uses
   an undefined name). Those are settled by rule and never reach the model.
2. The model's verdict (Nemotron, via the judge model) for everything else, accepted as
   a real bug only if it is confident, quotes the promise being broken, and the test
   actually exercises package code. Anything weaker is not reported as a bug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .results import TestOutcome

TEST_WRONG = "test_wrong"
REAL_BUG = "real_bug"
INCONCLUSIVE = "inconclusive"

_FRAME_RE = re.compile(r"^(?P<file>[^\s:]+\.py):(?P<line>\d+): in (?P<func>\S+)", re.MULTILINE)
_EXC_RE = re.compile(r"^E\s+(?P<exc>[A-Za-z_][\w.]*)(?::|\s*$)", re.MULTILINE)
_TEST_ONLY_EXC = {"NameError", "SyntaxError", "IndentationError", "ImportError", "ModuleNotFoundError", "UnboundLocalError"}


@dataclass
class Signals:
    origin: str  # "test" | "package" | "unknown"
    exception: str  # "AssertionError", "ValueError", "Failed", ...
    frames: list[tuple[str, int, str]]
    hard_reason: str | None = None  # set => test_wrong by rule


@dataclass
class Decision:
    verdict: str
    source: str  # "rule" | "model" | "guard"
    reason: str
    confidence: float = 0.0
    fixed_test: str = ""
    judged: dict | None = None


def read_signals(outcome: TestOutcome, package: str) -> Signals:
    text = outcome.details or ""
    frames = [(m["file"], int(m["line"]), m["func"]) for m in _FRAME_RE.finditer(text)]
    exc_match = _EXC_RE.search(text)
    exception = exc_match["exc"] if exc_match else ""
    if exception in ("assert", "") and re.search(r"^E\s+assert ", text, re.MULTILINE):
        exception = "AssertionError"
    exception = exception.rsplit(".", 1)[-1]

    def in_package(path: str) -> bool:
        p = path.replace("\\", "/")
        return p.startswith(f"{package}/") or p == f"{package}.py" or f"/{package}/" in p

    origin = "unknown"
    if frames:
        origin = "package" if in_package(frames[-1][0]) else "test"
    sig = Signals(origin=origin, exception=exception, frames=frames)

    message = f"{outcome.message}\n{text}"
    if outcome.collection_error:
        sig.hard_reason = "the test file does not import or collect"
    elif "fixture '" in message and "not found" in message:
        sig.hard_reason = "the test asks for a fixture that does not exist"
    elif exception in _TEST_ONLY_EXC and origin != "package":
        sig.hard_reason = f"{exception} raised in the test itself"
    elif exception == "TypeError" and origin == "test" and re.search(
        r"(positional argument|keyword argument|takes \d+|missing \d+ required|unexpected keyword)", message
    ):
        sig.hard_reason = "the test calls the API with the wrong arguments"
    elif exception == "AttributeError" and re.search(r"(module|type object) '[\w.]+' has no attribute", message):
        sig.hard_reason = "the test uses a name the package does not define"
    return sig


def decide(
    signals: Signals,
    judged: dict | None,
    *,
    exercises_package: bool,
    min_confidence: float = 0.6,
) -> Decision:
    if signals.hard_reason:
        fixed = (judged or {}).get("fixed_test") or ""
        return Decision(TEST_WRONG, "rule", signals.hard_reason, 1.0, fixed, judged)
    if not judged:
        return Decision(INCONCLUSIVE, "guard", "no verdict returned for this test")
    verdict = str(judged.get("verdict", "")).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        confidence = float(judged.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    explanation = str(judged.get("explanation", "")).strip()
    if verdict in ("real_bug", "bug", "code_wrong"):
        if not exercises_package:
            return Decision(TEST_WRONG, "guard", "a bug verdict, but the test never calls package code", confidence, judged=judged)
        if not str(judged.get("contract", "")).strip():
            return Decision(INCONCLUSIVE, "guard", "a bug verdict with no quoted promise", confidence, judged=judged)
        if confidence < min_confidence:
            return Decision(INCONCLUSIVE, "guard", f"a bug verdict below the {min_confidence:.0%} confidence bar", confidence, judged=judged)
        return Decision(REAL_BUG, "model", explanation, confidence, judged=judged)
    if verdict in ("test_wrong", "test", "bad_test"):
        return Decision(TEST_WRONG, "model", explanation, confidence, str(judged.get("fixed_test", "") or ""), judged)
    return Decision(INCONCLUSIVE, "guard", f"unrecognised verdict {verdict!r}", confidence, judged=judged)


def index_judgements(payload) -> dict[str, dict]:
    """Normalise the triage reply (list, or {"results": [...]}, or a single object)."""
    if isinstance(payload, dict):
        for key in ("results", "tests", "verdicts"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    out: dict[str, dict] = {}
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("test"):
                name = str(item["test"]).split("::")[-1].split("[")[0].strip()
                out[name] = item
    return out
