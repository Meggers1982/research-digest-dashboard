import pytest

from scripts.patterns import llm
from tests.patterns.conftest import FakeClient, message


def test_text_of_skips_thinking_block():
    msg = message({"a": 1})
    assert msg.content[0].text == ""  # the trap: content[0] is the thinking block
    assert llm.text_of(msg) == '{"a": 1}'


def test_complete_json_request_shape():
    client = FakeClient(lambda kw: message({"ok": True}))
    claude = llm.Claude(client=client)
    out = claude.complete_json(system="SYS", user="USER", schema={"type": "object"}, label="t",
                               effort="low")
    assert out == {"ok": True}
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and kw["betas"] == [llm.FALLBACK_BETA]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert claude.log.summary()["calls"] == 1


def test_refusal_raises():
    claude = llm.Claude(client=FakeClient(lambda kw: message(text="", stop_reason="refusal")))
    with pytest.raises(llm.ModelDeclined):
        claude.complete_json(system="S", user="U", schema={}, label="t")


def test_truncation_retries_once_at_larger_ceiling():
    replies = iter([message(text='{"a": ', stop_reason="max_tokens"), message({"a": 2})])
    client = FakeClient(lambda kw: next(replies))
    claude = llm.Claude(client=client)
    assert claude.complete_json(system="S", user="U", schema={}, label="t") == {"a": 2}
    assert [c["max_tokens"] for c in client.calls] == [llm.MAX_TOKENS, llm.RETRY_MAX_TOKENS]


def test_truncation_twice_raises():
    client = FakeClient(lambda kw: message(text="{", stop_reason="max_tokens"))
    with pytest.raises(llm.Truncated):
        llm.Claude(client=client).complete_json(system="S", user="U", schema={}, label="t")
    assert len(client.calls) == 2


def test_fallback_is_recorded_and_priced():
    claude = llm.Claude(client=FakeClient(lambda kw: message({"x": 1}, model="claude-opus-4-8")))
    claude.complete_json(system="S", user="U", schema={}, label="t")
    summary = claude.log.summary()
    assert summary["fallback_calls"] == 1
    assert summary["estimated_cost_usd"] > 0
