"""The real Token Factory client, driven over HTTP against a local stub server."""

import pytest

from testsmith.config import Settings
from testsmith.llm import ModelNotFound, NebiusLLM, UsageLog

from .stub_server import StubServer

LIGHTNING = "nvidia/Nemotron-3_5-Lightning"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"


def make(server, **kw):
    settings = Settings.from_env(env={"NEBIUS_API_KEY": "test-key", "NEBIUS_BASE_URL": server.base_url})
    return NebiusLLM(settings, UsageLog(prices=settings.prices), sleep=lambda s: None, **kw)


def test_chat_returns_content_and_logs_usage():
    with StubServer(["hello"]) as srv:
        llm = make(srv)
        assert llm.chat([{"role": "user", "content": "hi"}], model=LIGHTNING, purpose="write") == "hello"
    req = srv.requests[0]
    assert req["path"].endswith("/v1/chat/completions")
    assert req["auth"] == "Bearer test-key"
    assert req["body"]["model"] == LIGHTNING
    call = llm.usage.calls[0]
    assert (call.purpose, call.prompt_tokens, call.completion_tokens, call.attempts) == ("write", 120, 30, 1)


def test_retries_rate_limits_and_server_errors():
    with StubServer([429, 500, "finally"]) as srv:
        llm = make(srv)
        assert llm.chat([{"role": "user", "content": "x"}], model=LIGHTNING, purpose="triage") == "finally"
    assert len(srv.requests) == 3
    assert llm.usage.calls[0].attempts == 3


def test_gives_up_after_max_attempts():
    with StubServer([500, 500]) as srv:
        llm = make(srv, max_attempts=2)
        with pytest.raises(Exception):
            llm.chat([{"role": "user", "content": "x"}], model=LIGHTNING, purpose="write")
    assert llm.usage.calls[-1].error


def test_unknown_model_raises_model_not_found():
    with StubServer(["x"]) as srv:
        llm = make(srv)
        with pytest.raises(ModelNotFound):
            llm.chat([{"role": "user", "content": "x"}], model="nvidia/not-a-model", purpose="triage")


def test_cost_uses_published_price_only():
    with StubServer(["a", "b"], models=[LIGHTNING, ULTRA]) as srv:
        llm = make(srv)
        llm.chat([{"role": "user", "content": "x"}], model=ULTRA, purpose="triage")
        llm.chat([{"role": "user", "content": "x"}], model=LIGHTNING, purpose="write")
    ultra, lightning = llm.usage.calls
    assert llm.usage.cost_of(ultra) == pytest.approx((120 * 1.0 + 30 * 3.0) / 1e6)
    assert llm.usage.cost_of(lightning) is None
    totals = llm.usage.totals()
    assert totals["calls"] == 2 and totals["cost_complete"] is False


def test_list_models():
    with StubServer([], models=[LIGHTNING, ULTRA, "other/model"]) as srv:
        assert make(srv).list_models() == sorted([LIGHTNING, ULTRA, "other/model"])


def test_missing_key_gives_actionable_error():
    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        NebiusLLM(Settings.from_env(env={}))
