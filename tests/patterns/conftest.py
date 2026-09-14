"""Fixture corpus and a fake Anthropic client. No test touches the network."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.patterns import guardrails
from scripts.patterns.data import Corpus

WEEK_START = date(2026, 9, 7)
WEEK_END = date(2026, 9, 13)
IN_WEEK = "2026-09-09"
EARLIER = "2026-08-20"


def study(pmid, journal, run_date=IN_WEEK, score=5, **extra):
    return {
        "pmid": str(pmid),
        "headline": f"Headline {pmid}",
        "journal": journal,
        "pubdate": "2026 Sep 1",
        "doi": f"10.1/{pmid}",
        "summary": f"Summary of study {pmid}. It found something.",
        "why_it_matters": "Because.",
        "caveats": "observational",
        "relevance_score": score,
        "relevance_score_reason": "reason",
        "run_date": run_date,
        "category": "Test",
        **extra,
    }


SOURCES = [
    {"id": "alpha", "label": "Alpha Topic", "file": "data/alpha.json"},
    {"id": "beta", "label": "Beta Topic", "file": "data/beta.json"},
    {"id": "senior-research", "label": "Senior Living", "file": "data/senior-research.json"},
    {"id": "pitch-ideas", "label": "Pitch Ideas", "file": "data/pitch-ideas.json"},
]

RAW = {
    "alpha": [
        study(1001, "Journal of Alpha Medicine"),
        study(1002, "Journal B"),
        study(1003, "Journal C", score=6),
        study(1004, "Journal of Alpha Medicine", score=4),
        study(1005, "Journal B", run_date=EARLIER, score=7),
        study(1006, "Journal C", excluded=True),
        study(1007, "The Lancet (London, England)"),
        study(1008, "Lancet"),
        study(1009, "Journal of Alpha Medicine"),
        study(1010, "Journal C", run_date=EARLIER),
        study(1011, "Journal D", run_date=EARLIER),
        study(1012, "Journal E"),  # excluded in beta, so excluded everywhere
        study(3001, "Journal F"),
        # Same paper carried twice; the earliest run_date wins.
        study(1013, "Journal B", run_date="2026-09-12"),
        study(1013, "Journal B", run_date=EARLIER),
    ],
    "beta": [
        study(2001, "Journal D"),
        study(2002, "Journal E"),
        study(2003, "Journal D"),
        study(3001, "Journal F"),
        study(1012, "Journal E", excluded=True),
    ],
    "senior-research": [study(4001, "Journal G")],
    "pitch-ideas": [],
}


@pytest.fixture
def corpus() -> Corpus:
    return Corpus.from_raw(SOURCES, RAW)


@pytest.fixture
def log() -> guardrails.DropLog:
    return guardrails.DropLog(quiet=True)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo" / "data"
    d.mkdir(parents=True)
    (d / "sources.json").write_text(json.dumps(SOURCES))
    for sid, studies in RAW.items():
        (d / f"{sid}.json").write_text(json.dumps({"source_id": sid, "studies": studies}))
    return d


def thread(pmids, *, id="t1", flags=None, **extra):
    raw = {
        "id": id,
        "title": f"Title {id}",
        "claim": f"Claim {id}",
        "pmids": [str(p) for p in pmids],
        "direction": "new",
        "relates_to": "",
        "designs": [{"pmid": str(p), "design": "cohort"} for p in pmids],
        "flags": {"single_country": False, "single_group": False, "same_cohort_suspected": False,
                  **(flags or {})},
        "flag_note": "",
    }
    raw.update(extra)
    return raw


# ---- fake Anthropic client ---------------------------------------------------

def message(payload=None, *, stop_reason="end_turn", model="claude-opus-5", text=None):
    body = text if text is not None else json.dumps(payload)
    return SimpleNamespace(
        # Adaptive thinking puts a thinking block first; content[0].text is not the answer.
        content=[SimpleNamespace(type="thinking", thinking="", text=""),
                 SimpleNamespace(type="text", text=body)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        model=model,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                              cache_creation_input_tokens=10, cache_read_input_tokens=5),
    )


class _Stream:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._msg


class FakeClient:
    """`responder(kwargs) -> message` decides each reply; every call is recorded."""

    def __init__(self, responder):
        self.calls: list[dict] = []
        self._responder = responder
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream(self._responder(kwargs))


def system_of(kwargs) -> str:
    return kwargs["system"][0]["text"]


def user_of(kwargs) -> str:
    return kwargs["messages"][0]["content"][0]["text"]
