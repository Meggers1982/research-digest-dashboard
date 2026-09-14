"""The four model passes: bootstrap, per-topic, verification, cross-topic.

Each builds its user message from compact records and returns the parsed
structured output. Guardrails live in guardrails.py and run on the results;
nothing here decides what survives.
"""
from __future__ import annotations

from datetime import date

from . import llm, prompts
from .data import Corpus, Study, compact_block, trim


def _memory_text(mem) -> str:
    return mem.render() if mem else "(empty: no memory for this topic yet)"


def topic_user_message(*, label: str, source_id: str, week_start: date, week_end: date,
                       memory_text: str, studies: list[Study]) -> str:
    return (
        f"Topic: {label} (source id: {source_id})\n"
        f"Week under review: {week_start.isoformat()} to {week_end.isoformat()} "
        f"({len(studies)} studies)\n\n"
        f"=== TOPIC MEMORY ===\n{memory_text}\n\n"
        f"=== THIS WEEK'S STUDIES ===\n{compact_block(studies)}\n"
    )


def bootstrap_user_message(*, label: str, source_id: str, start: date, end: date,
                           studies: list[Study]) -> str:
    return (
        f"Topic: {label} (source id: {source_id})\n"
        f"History: {start.isoformat()} to {end.isoformat()} ({len(studies)} studies)\n\n"
        f"=== STUDIES ===\n{compact_block(studies)}\n"
    )


def verify_user_message(*, label: str, threads: list[dict], lookup) -> str:
    """`lookup(pmid)` returns the Study the gate resolved for this scope."""
    parts = [f"Scope: {label}\n{len(threads)} thread(s) to check.\n"]
    for t in threads:
        flags = ", ".join(k for k, v in t["flags"].items() if v) or "none"
        parts.append(
            f"=== THREAD {t['id']} ===\n"
            f"Title: {t['title']}\nClaim: {t['claim']}\n"
            f"Flags set by the proposer: {flags}"
            + (f" ({t['flag_note']})" if t.get("flag_note") else "")
        )
        for p in t["pmids"]:
            st = lookup(p)
            parts.append(
                f"--- PMID {p} | {st.journal} | published {st.pubdate or '?'} | "
                f"design as proposed: {t['designs'].get(p, 'unknown')}\n"
                f"Headline: {st.headline}\nSummary: {st.summary}\n"
                f"Caveats: {st.caveats or 'none listed'}"
            )
        parts.append("")
    return "\n".join(parts)


def cross_user_message(*, week_start: date, week_end: date, topics: list[dict],
                       context_label: str = "", context: list[Study] | None = None) -> str:
    """`topics`: [{source_id, label, memory_text, threads: [finalized threads]}]."""
    parts = [f"Week under review: {week_start.isoformat()} to {week_end.isoformat()}\n"]
    for t in topics:
        parts.append(f"=== TOPIC {t['source_id']} ({t['label']}) ===")
        parts.append("Memory:\n" + t["memory_text"].strip())
        if t["threads"]:
            parts.append("This week's verified threads:")
            for th in t["threads"]:
                parts.append(f"- [{th['id']}] ({th['direction']}) {th['title']}: {th['claim']}")
                for s in th["studies"]:
                    parts.append(f"    PMID {s['pmid']} | {s['journal']} | {trim(s['headline'], 160)}")
        else:
            parts.append("This week's verified threads: none")
        parts.append("")
    if context:
        parts.append(f"=== CONTEXT STUDIES: {context_label} (this week; read-only, no memory here) ===")
        parts.append(compact_block(context, with_source=True))
    return "\n".join(parts) + "\n"


# ---- calls ---------------------------------------------------------------

def run_bootstrap(claude: llm.Claude, corpus: Corpus, source_id: str, start: date, end: date,
                  studies: list[Study], *, model: str, effort: str) -> dict:
    return claude.complete_json(
        system=prompts.BOOTSTRAP_SYSTEM,
        user=bootstrap_user_message(label=corpus.label(source_id), source_id=source_id,
                                    start=start, end=end, studies=studies),
        schema=prompts.BOOTSTRAP_SCHEMA,
        label=f"bootstrap {source_id}",
        model=model,
        effort=effort,
    )


def run_topic(claude: llm.Claude, corpus: Corpus, source_id: str, week_start: date, week_end: date,
              studies: list[Study], prior_mem, *, model: str, effort: str) -> dict:
    return claude.complete_json(
        system=prompts.TOPIC_SYSTEM,
        user=topic_user_message(label=corpus.label(source_id), source_id=source_id,
                                week_start=week_start, week_end=week_end,
                                memory_text=_memory_text(prior_mem), studies=studies),
        schema=prompts.TOPIC_SCHEMA,
        label=f"topic {source_id}",
        model=model,
        effort=effort,
    )


def run_verify(claude: llm.Claude, *, label: str, scope_id: str, threads: list[dict], lookup,
               model: str, effort: str) -> list[dict]:
    result = claude.complete_json(
        system=prompts.VERIFY_SYSTEM,
        user=verify_user_message(label=label, threads=threads, lookup=lookup),
        schema=prompts.VERIFY_SCHEMA,
        label=f"verify {scope_id}",
        model=model,
        effort=effort,
    )
    return result.get("threads", [])


def run_cross(claude: llm.Claude, *, week_start: date, week_end: date, topics: list[dict],
              context_label: str, context: list[Study], model: str, effort: str) -> list[dict]:
    result = claude.complete_json(
        system=prompts.CROSS_SYSTEM,
        user=cross_user_message(week_start=week_start, week_end=week_end, topics=topics,
                                context_label=context_label, context=context),
        schema=prompts.CROSS_SCHEMA,
        label="cross-topic",
        model=model,
        effort=effort,
    )
    return result.get("threads", [])
