"""Pull code and JSON out of free-form model replies.

Reasoning models may wrap their answer in ``<think>`` blocks, add prose before and
after, use several fenced blocks, or emit JSON with trailing commas. Everything here
is defensive and pure so it can be tested in isolation.
"""

from __future__ import annotations

import ast
import json
import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)


class ExtractionError(ValueError):
    """The reply did not contain the expected payload."""


def strip_reasoning(text: str) -> str:
    """Drop ``<think>...</think>`` spans, and an unterminated leading one."""
    text = _THINK_RE.sub("", text or "")
    lowered = text.lower()
    if "<think>" in lowered and "</think>" not in lowered:
        # Truncated reasoning with no answer after it.
        text = text[: lowered.index("<think>")]
    elif "</think>" in lowered:
        # Some servers strip the opening tag but keep the closing one.
        text = text[lowered.rindex("</think>") + len("</think>") :]
    return text.strip()


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """Return ``(language, body)`` for every fenced block, in order."""
    return [(lang.lower(), body) for lang, body in _FENCE_RE.findall(text)]


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def extract_python(text: str) -> str:
    """Return the best Python code payload in ``text``.

    Preference: the longest fenced ``python``/``py`` block that parses, then any
    fenced block that parses, then the whole reply if it parses as Python.
    """
    body = strip_reasoning(text)
    blocks = fenced_blocks(body)
    py_blocks = [b for lang, b in blocks if lang in ("python", "py", "python3")]
    other = [b for lang, b in blocks if lang not in ("python", "py", "python3", "json")]
    for group in (py_blocks, other):
        valid = [b for b in group if b.strip() and _parses(b)]
        if valid:
            return max(valid, key=len).strip() + "\n"
    if body and "```" not in body and _parses(body) and ("def " in body or "import " in body):
        return body.strip() + "\n"
    if py_blocks:
        raise ExtractionError("python block found but it does not parse")
    raise ExtractionError("no python code block in reply")


def _repair_json(candidate: str) -> str:
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)  # trailing commas
    return candidate


def _balanced_spans(text: str) -> list[str]:
    """Return every balanced top-level ``{...}`` or ``[...]`` span, string-aware."""
    spans = []
    i = 0
    while i < len(text):
        if text[i] in "{[":
            open_ch = text[i]
            close_ch = "}" if open_ch == "{" else "]"
            depth = 0
            in_str = False
            esc = False
            for j in range(i, len(text)):
                ch = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        spans.append(text[i : j + 1])
                        i = j
                        break
        i += 1
    return spans


def extract_json(text: str):
    """Return the first JSON object or array found in ``text``.

    Tries fenced ``json`` blocks first, then any balanced span in the prose.
    """
    body = strip_reasoning(text)
    candidates = [b for lang, b in fenced_blocks(body) if lang in ("json", "")]
    candidates += _balanced_spans(body)
    for cand in candidates:
        cand = cand.strip()
        for attempt in (cand, _repair_json(cand)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    raise ExtractionError("no JSON payload in reply")
