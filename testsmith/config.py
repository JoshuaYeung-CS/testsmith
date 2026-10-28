"""Configuration from environment variables and an optional ``.env`` file.

Defaults come from the Nebius Token Factory documentation:

* Base URL ``https://api.tokenfactory.nebius.com/v1/`` - Token Factory quickstart,
  https://docs.tokenfactory.nebius.com/quickstart
* ``nvidia/Nemotron-3_5-Lightning`` and ``nvidia/Nemotron-3-Ultra-550b-a55b`` -
  the Nemotron table in the official cookbook,
  https://github.com/nebius/token-factory-cookbook/blob/main/models/nemotron/README.md
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
# Fast, low-cost Nemotron MoE (30B total / 3B active, 1M context). Writes and repairs tests.
DEFAULT_MODEL = "nvidia/Nemotron-3_5-Lightning"
# Flagship Nemotron (550B total / 55B active). Judges "bad test or real bug".
DEFAULT_JUDGE_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"

# USD per 1M tokens (input, output). Only prices published in Nebius material are listed;
# anything else is reported as "unknown" unless TESTSMITH_PRICES supplies it.
# Ultra: cookbook models/nemotron/nemotron3-ultra-550b-a55b.md ("$1.00 / 1M input, $3.00 / 1M output").
KNOWN_PRICES: dict[str, tuple[float, float]] = {
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
}


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=value`` file. Lines starting with ``#`` are ignored."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def parse_prices(spec: str) -> dict[str, tuple[float, float]]:
    """Parse ``model=in/out,model2=in/out`` (USD per 1M tokens)."""
    prices: dict[str, tuple[float, float]] = {}
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        model, _, pair = chunk.rpartition("=")
        try:
            pin, pout = pair.split("/", 1)
            prices[model.strip()] = (float(pin), float(pout))
        except ValueError:
            continue
    return prices


@dataclass
class Settings:
    api_key: str | None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    max_tokens: int = 8192
    temperature: float = 0.2
    request_timeout: float = 120.0
    prices: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(KNOWN_PRICES))

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, dotenv: Path | None = None) -> "Settings":
        merged: dict[str, str] = {}
        if dotenv is not None:
            merged.update(load_dotenv(dotenv))
        merged.update(os.environ if env is None else env)

        def get(name: str, default: str) -> str:
            value = merged.get(name, "").strip()
            return value or default

        prices = dict(KNOWN_PRICES)
        prices.update(parse_prices(merged.get("TESTSMITH_PRICES", "")))
        model = get("TESTSMITH_MODEL", DEFAULT_MODEL)
        return cls(
            api_key=merged.get("NEBIUS_API_KEY", "").strip() or None,
            base_url=get("NEBIUS_BASE_URL", DEFAULT_BASE_URL),
            model=model,
            judge_model=get("TESTSMITH_JUDGE_MODEL", DEFAULT_JUDGE_MODEL),
            max_tokens=int(get("TESTSMITH_MAX_TOKENS", "8192")),
            temperature=float(get("TESTSMITH_TEMPERATURE", "0.2")),
            request_timeout=float(get("TESTSMITH_REQUEST_TIMEOUT", "120")),
            prices=prices,
        )
