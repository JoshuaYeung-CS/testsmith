import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
PACKAGE = EXAMPLES / "buggy_inventory"
EXISTING = EXAMPLES / "tests"
REPLAY = EXAMPLES / "replay" / "buggy_inventory.json"


def tree_digest(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def replay_responses():
    return json.loads(REPLAY.read_text())["responses"]


def py_reply(code: str, prose: str = "Here are the tests.") -> str:
    return f"<think>planning</think>\n{prose}\n\n```python\n{code.strip()}\n```\n"


def json_reply(payload) -> str:
    return f"<think>judging</think>\n```json\n{json.dumps(payload, indent=1)}\n```"
