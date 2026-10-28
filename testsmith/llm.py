"""Chat-completion clients: the real Nebius Token Factory client and the usage ledger.

Every agent step goes through ``LLM.chat``. The real implementation uses the
``openai`` package pointed at the Token Factory base URL; tests and offline replays
inject ``testsmith.fake.ScriptedLLM`` instead, which has the same interface.
"""

from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from .config import Settings


@dataclass
class CallRecord:
    purpose: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    attempts: int
    error: str | None = None
    fallback_from: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class UsageLog:
    """Per-call token, latency and cost ledger for one run."""

    prices: dict[str, tuple[float, float]] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)
    echo: bool = False

    def add(self, record: CallRecord) -> None:
        self.calls.append(record)
        if self.echo:
            cost = self.cost_of(record)
            cost_s = f"${cost:.5f}" if cost is not None else "cost n/a"
            status = f" ERROR {record.error}" if record.error else ""
            print(
                f"  [model] {record.purpose:<10} {record.model}  "
                f"{record.prompt_tokens} in / {record.completion_tokens} out  "
                f"{record.latency_s:.1f}s  attempts={record.attempts}  {cost_s}{status}",
                file=sys.stderr,
            )

    def cost_of(self, record: CallRecord) -> float | None:
        price = self.prices.get(record.model)
        if price is None:
            return None
        pin, pout = price
        return (record.prompt_tokens * pin + record.completion_tokens * pout) / 1_000_000

    def totals(self) -> dict:
        prompt = sum(c.prompt_tokens for c in self.calls)
        completion = sum(c.completion_tokens for c in self.calls)
        costs = [self.cost_of(c) for c in self.calls]
        known = [c for c in costs if c is not None]
        return {
            "calls": len(self.calls),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "latency_s": round(sum(c.latency_s for c in self.calls), 2),
            "cost_usd": round(sum(known), 6) if known else None,
            "cost_complete": len(known) == len(costs),
            "errors": sum(1 for c in self.calls if c.error),
        }

    def to_dict(self) -> dict:
        return {"calls": [asdict(c) for c in self.calls], "totals": self.totals()}


class LLM(Protocol):
    usage: UsageLog

    def chat(self, messages: list[dict], *, model: str, purpose: str) -> str:
        """Return the assistant text for ``messages``."""
        ...


class ModelNotFound(RuntimeError):
    """The endpoint does not serve the requested model id."""


class NebiusLLM:
    """OpenAI-compatible client for Nebius Token Factory with retries and usage logging."""

    RETRYABLE = ("RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError")

    def __init__(
        self,
        settings: Settings,
        usage: UsageLog | None = None,
        *,
        max_attempts: int = 4,
        backoff_s: float = 1.5,
        sleep: Callable[[float], None] = time.sleep,
        client=None,
    ) -> None:
        if client is None:
            if not settings.api_key:
                raise RuntimeError(
                    "NEBIUS_API_KEY is not set. Create a key at https://tokenfactory.nebius.com "
                    "and put it in .env (see .env.example), or run with --replay for an offline demo."
                )
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=settings.request_timeout,
                max_retries=0,  # retries are handled here so they can be counted
            )
        self.client = client
        self.settings = settings
        self.usage = usage or UsageLog(prices=settings.prices)
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.sleep = sleep

    def chat(self, messages: list[dict], *, model: str, purpose: str) -> str:
        start = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=self.settings.max_tokens,
                    temperature=self.settings.temperature,
                )
            except Exception as exc:  # classified below
                name = type(exc).__name__
                if name == "NotFoundError":
                    self.usage.add(
                        CallRecord(purpose, model, 0, 0, time.monotonic() - start, attempt, f"{name}: {exc}")
                    )
                    raise ModelNotFound(f"model {model!r} not found at {self.settings.base_url}") from exc
                if name in self.RETRYABLE and attempt < self.max_attempts:
                    last_error = exc
                    self.sleep(self.backoff_s * (2 ** (attempt - 1)))
                    continue
                self.usage.add(
                    CallRecord(purpose, model, 0, 0, time.monotonic() - start, attempt, f"{name}: {exc}")
                )
                raise
            usage = getattr(resp, "usage", None)
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            choice = resp.choices[0]
            text = choice.message.content or ""
            self.usage.add(
                CallRecord(
                    purpose,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    time.monotonic() - start,
                    attempt,
                )
            )
            return text
        raise RuntimeError(f"model call failed after {self.max_attempts} attempts: {last_error}")

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client.models.list())
