"""The one place the patterns job talks to Claude.

Modeled on senior-research-digest's `scripts/llm.py`, with the gotchas that
repo already paid for:

- `claude-opus-5` runs adaptive thinking by default, so the response opens with
  a thinking block and `response.content[0].text` is empty. `text_of` joins the
  text blocks instead.
- Thinking shares the `max_tokens` budget. Every call here streams, so a large
  ceiling doesn't hit the SDK's non-streaming timeout guard.
- A hung call must fail inside the job's time limit. The client times out at
  240s with 2 retries, the same bound senior-research-digest settled on
  (MEA-244).

Every call returns JSON under `output_config.format`. A truncated JSON answer
can't be continued by appending the next turn the way prose can, so the
continuation step here is one retry at a larger ceiling; if that is still
truncated the call raises `Truncated`.

Calls opt into `fallbacks: "default"`, so a safety-classifier decline is re-run
server-side on Anthropic's recommended fallback model instead of failing the
topic. `served_by` in the call log shows when that happened.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

MODEL = "claude-opus-5"
# The verification pass is the "cheaper call": the same model at low effort,
# over a much smaller input (only the surviving threads and their studies).
VERIFY_MODEL = "claude-opus-5"
MAIN_EFFORT = "high"
VERIFY_EFFORT = "low"

TIMEOUT_SECONDS = 240.0
MAX_RETRIES = 2
MAX_TOKENS = 32000
RETRY_MAX_TOKENS = 64000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens, first-party API list prices (September 2026). Used
# only for the cost estimate the run prints; billing is whatever the API says.
PRICES = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


class ModelDeclined(RuntimeError):
    """The model (and any fallback) returned `stop_reason: "refusal"`."""


class Truncated(ValueError):
    """The JSON answer hit `max_tokens` even after the larger retry."""


def text_of(message) -> str:
    """Concatenate the response's text blocks, skipping thinking blocks."""
    return "".join(
        block.text
        for block in (getattr(message, "content", None) or [])
        if getattr(block, "type", None) == "text"
    )


def cached(text: str) -> dict:
    """A text block marked as a prompt-cache breakpoint."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


@dataclass
class CallRecord:
    label: str
    model: str
    served_by: str
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def cost(self) -> float:
        price = PRICES.get(self.served_by) or PRICES.get(self.model) or PRICES[MODEL]
        per = 1_000_000
        return (
            self.input_tokens * price["input"] / per
            + self.cache_creation_input_tokens * price["input"] * CACHE_WRITE_MULTIPLIER / per
            + self.cache_read_input_tokens * price["input"] * CACHE_READ_MULTIPLIER / per
            + self.output_tokens * price["output"] / per
        )


@dataclass
class CallLog:
    records: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> None:
        self.records.append(record)

    def summary(self) -> dict:
        total = {
            "calls": len(self.records),
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        for r in self.records:
            total["input_tokens"] += r.input_tokens
            total["output_tokens"] += r.output_tokens
            total["cache_creation_input_tokens"] += r.cache_creation_input_tokens
            total["cache_read_input_tokens"] += r.cache_read_input_tokens
        total["estimated_cost_usd"] = round(sum(r.cost() for r in self.records), 4)
        total["fallback_calls"] = sum(1 for r in self.records if r.served_by != r.model)
        return total


def make_client():
    """The real Anthropic client, bounded the way the module docstring says."""
    import anthropic

    return anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)


class Claude:
    """Structured-output calls with streaming, fallbacks and a call log.

    `client` is anything with `beta.messages.stream(**kwargs)` returning a
    context manager whose `get_final_message()` yields a message. Tests pass a
    fake one.
    """

    def __init__(self, client=None, log: CallLog | None = None):
        self._client = client
        self.log = log or CallLog()

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict,
        label: str,
        model: str = MODEL,
        effort: str = MAIN_EFFORT,
        max_tokens: int = MAX_TOKENS,
    ) -> dict:
        try:
            return self._once(
                system=system, user=user, schema=schema, label=label,
                model=model, effort=effort, max_tokens=max_tokens,
            )
        except Truncated:
            if max_tokens >= RETRY_MAX_TOKENS:
                raise
            print(f"    {label}: hit max_tokens={max_tokens}; retrying once at {RETRY_MAX_TOKENS}")
            return self._once(
                system=system, user=user, schema=schema, label=label,
                model=model, effort=effort, max_tokens=RETRY_MAX_TOKENS,
            )

    def _once(self, *, system, user, schema, label, model, effort, max_tokens) -> dict:
        import anthropic

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            # The system prompt is identical across every topic in a run, so it
            # is the cache breakpoint; the per-topic user block follows it.
            "system": [cached(system)],
            "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        attempts = 0
        while True:
            attempts += 1
            try:
                with self.client.beta.messages.stream(**kwargs) as stream:
                    message = stream.get_final_message()
                break
            except anthropic.APIConnectionError:
                # The SDK retries a failed request start; this covers a stream
                # that drops mid-answer, once.
                if attempts > 1:
                    raise
                print(f"    {label}: stream dropped; retrying once")

        usage = getattr(message, "usage", None)
        record = CallRecord(
            label=label,
            model=model,
            served_by=getattr(message, "model", model) or model,
            stop_reason=getattr(message, "stop_reason", "") or "",
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        self.log.add(record)
        print(
            f"    {label}: {record.stop_reason}, in={record.input_tokens} "
            f"(cache read {record.cache_read_input_tokens}, write "
            f"{record.cache_creation_input_tokens}) out={record.output_tokens}"
            + (f", served by {record.served_by}" if record.served_by != model else "")
        )

        if record.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise ModelDeclined(f"{label}: {getattr(details, 'category', None) or 'unspecified'}")
        if record.stop_reason == "max_tokens":
            raise Truncated(f"{label}: response hit max_tokens={max_tokens}")
        text = text_of(message)
        if not text.strip():
            raise ValueError(f"{label}: response had no text blocks")
        return json.loads(text)
