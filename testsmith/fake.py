"""A scripted stand-in for the model, used by the test suite and ``--replay`` runs.

A script is a JSON file shaped like::

    {"responses": {"write": ["...reply 1..."], "triage": ["...", "..."], ...}}

Each call pops the next reply queued for its ``purpose``. Token counts are estimated
from character length (about 4 characters per token) so the usage ledger and report
are exercised exactly as in a live run.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path

from .llm import CallRecord, ModelNotFound, UsageLog


class ScriptExhausted(RuntimeError):
    """The agent asked for more replies than the script holds."""


class ScriptedLLM:
    def __init__(
        self,
        responses: dict[str, list[str]],
        usage: UsageLog | None = None,
        *,
        missing_models: set[str] | None = None,
    ) -> None:
        self.queues = {k: deque(v) for k, v in responses.items()}
        self.usage = usage or UsageLog()
        self.calls: list[dict] = []
        self.missing_models = missing_models or set()

    @classmethod
    def from_file(cls, path: Path, usage: UsageLog | None = None) -> "ScriptedLLM":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data["responses"], usage)

    def chat(self, messages: list[dict], *, model: str, purpose: str) -> str:
        start = time.monotonic()
        self.calls.append({"purpose": purpose, "model": model, "messages": messages})
        if model in self.missing_models:
            self.usage.add(CallRecord(purpose, model, 0, 0, 0.0, 1, "NotFoundError: scripted"))
            raise ModelNotFound(model)
        queue = self.queues.get(purpose)
        if not queue:
            raise ScriptExhausted(f"no scripted reply left for purpose {purpose!r}")
        reply = queue.popleft()
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        self.usage.add(
            CallRecord(
                purpose,
                model,
                max(1, prompt_chars // 4),
                max(1, len(reply) // 4),
                time.monotonic() - start,
                1,
            )
        )
        return reply

    def remaining(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.queues.items() if v}
