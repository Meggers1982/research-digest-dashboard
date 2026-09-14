"""Load the dashboard's source files into one PMID-indexed corpus.

Rules the rest of the job relies on:

- `excluded: true` studies are never loaded. A PMID excluded in ANY source is
  treated as excluded everywhere, since an exclusion is an editorial call
  about the paper, not about which digest carried it.
- Within a source, a PMID appears once. When a digest carried the same paper
  on two days, the earliest `run_date` wins, so a repeat never counts as new.
- `pitch-ideas` is skipped. `senior-research` gets no per-topic pass (its
  memory lives upstream in senior-research-digest) but its studies are loaded
  as read-only context for the cross-topic pass.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

SKIP_SOURCES = {"pitch-ideas"}
CONTEXT_ONLY_SOURCES = {"senior-research"}

SUMMARY_CHARS = 420
CAVEAT_CHARS = 220


@dataclass(frozen=True)
class Study:
    pmid: str
    source_id: str
    headline: str
    journal: str
    pubdate: str
    doi: str
    summary: str
    caveats: str
    score: int | None
    score_reason: str
    run_date: date | None
    category: str

    def card(self) -> dict:
        """What patterns.json embeds so the page never loads the source files."""
        return {
            "pmid": self.pmid,
            "headline": self.headline,
            "journal": self.journal,
            "pubdate": self.pubdate,
            "doi": self.doi,
            "relevance_score": self.score,
            "run_date": self.run_date.isoformat() if self.run_date else "",
            "summary": self.summary,
            "caveats": self.caveats,
        }


def clean_pmid(value) -> str:
    """'PMID: 42318867' / 42318867 / ' 42318867 ' -> '42318867'."""
    return re.sub(r"\D", "", str(value or ""))


def journal_key(name: str) -> str:
    """Normalize a journal name so formatting variants count as one journal.

    'The Lancet (London, England)' and 'Lancet' match; so do
    'Dermatitis: Contact, Atopic, Occupational, Drug' and 'Dermatitis'.
    """
    s = (name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.split(":")[0]
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^the ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def trim(text: str, limit: int) -> str:
    """Cut at a sentence end if one falls in the back half, else at a word."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    if stop >= limit // 2:
        return cut[: stop + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _score(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Corpus:
    def __init__(self, sources: list[dict], by_source: dict[str, dict[str, Study]], excluded: set[str]):
        self.sources = sources
        self.by_source = by_source
        self.excluded = excluded
        self._labels = {s["id"]: s.get("label", s["id"]) for s in sources}

    # ---- loading -----------------------------------------------------------
    @classmethod
    def load(cls, data_dir: Path, repo_root: Path | None = None) -> "Corpus":
        data_dir = Path(data_dir)
        root = Path(repo_root) if repo_root else data_dir.parent
        sources = json.loads((data_dir / "sources.json").read_text(encoding="utf-8"))
        sources = [s for s in sources if s["id"] not in SKIP_SOURCES]
        raw: dict[str, list[dict]] = {}
        for s in sources:
            path = root / s["file"]
            if not path.exists():
                path = data_dir / Path(s["file"]).name
            if not path.exists():
                print(f"  WARNING: {s['id']}: {s['file']} not found; skipping source")
                raw[s["id"]] = []
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw[s["id"]] = payload.get("studies", []) if isinstance(payload, dict) else list(payload)
        return cls.from_raw(sources, raw)

    @classmethod
    def from_raw(cls, sources: list[dict], raw: dict[str, list[dict]]) -> "Corpus":
        sources = [s for s in sources if s["id"] not in SKIP_SOURCES]
        excluded = {
            clean_pmid(st.get("pmid"))
            for studies in raw.values()
            for st in studies
            if st.get("excluded")
        }
        excluded.discard("")
        by_source: dict[str, dict[str, Study]] = {}
        for s in sources:
            sid = s["id"]
            kept: dict[str, Study] = {}
            for st in raw.get(sid, []):
                pmid = clean_pmid(st.get("pmid"))
                if not pmid or st.get("excluded") or pmid in excluded:
                    continue
                study = Study(
                    pmid=pmid,
                    source_id=sid,
                    headline=str(st.get("headline") or "").strip(),
                    journal=str(st.get("journal") or "").strip(),
                    pubdate=str(st.get("pubdate") or "").strip(),
                    doi=str(st.get("doi") or "").strip(),
                    summary=str(st.get("summary") or "").strip(),
                    caveats=str(st.get("caveats") or "").strip(),
                    score=_score(st.get("relevance_score")),
                    score_reason=str(st.get("relevance_score_reason") or "").strip(),
                    run_date=_parse_date(st.get("run_date")),
                    category=str(st.get("category") or "").strip(),
                )
                prior = kept.get(pmid)
                if prior is None or (
                    study.run_date and (prior.run_date is None or study.run_date < prior.run_date)
                ):
                    kept[pmid] = study
            by_source[sid] = kept
        return cls(sources, by_source, excluded)

    # ---- lookups -----------------------------------------------------------
    def label(self, source_id: str) -> str:
        return self._labels.get(source_id, source_id)

    def topic_ids(self) -> list[str]:
        """Sources that get a per-topic pass, in sources.json order."""
        return [s["id"] for s in self.sources if s["id"] not in CONTEXT_ONLY_SOURCES]

    def study(self, pmid: str, source_id: str | None = None) -> Study | None:
        pmid = clean_pmid(pmid)
        if source_id is not None:
            return self.by_source.get(source_id, {}).get(pmid)
        for sid in self.by_source:
            hit = self.by_source[sid].get(pmid)
            if hit:
                return hit
        return None

    def sources_of(self, pmid: str) -> set[str]:
        pmid = clean_pmid(pmid)
        return {sid for sid, studies in self.by_source.items() if pmid in studies}

    def window(self, source_id: str, start: date, end: date) -> list[Study]:
        """This source's studies whose (first) run_date is in [start, end]."""
        out = [
            s for s in self.by_source.get(source_id, {}).values()
            if s.run_date and start <= s.run_date <= end
        ]
        return sorted(out, key=lambda s: (s.run_date, s.pmid))

    def window_all(self, start: date, end: date, source_ids=None) -> dict[str, Study]:
        """PMID-deduped studies across sources in the window (first source wins)."""
        out: dict[str, Study] = {}
        for sid in source_ids or self.by_source:
            for s in self.window(sid, start, end):
                out.setdefault(s.pmid, s)
        return out


# ---- week arithmetic -------------------------------------------------------

def default_week_end(today: date) -> date:
    """The most recent Sunday strictly before `today`."""
    days_back = (today.weekday() + 1) % 7 or 7
    return today - timedelta(days=days_back)


def week_bounds(week_end: date) -> tuple[date, date]:
    return week_end - timedelta(days=6), week_end


# ---- compact records sent to the model -------------------------------------

def compact(study: Study, *, with_source: bool = False) -> str:
    """One study as a few short lines. Scores of every level are included."""
    head = [f"PMID {study.pmid}"]
    if with_source:
        head.append(f"source {study.source_id}")
    head += [
        f"score {study.score if study.score is not None else '?'}",
        f"added {study.run_date.isoformat() if study.run_date else '?'}",
        f"published {study.pubdate or '?'}",
        f"journal: {study.journal or '?'}",
    ]
    lines = [" | ".join(head), f"Headline: {study.headline}"]
    if study.summary:
        lines.append(f"Summary: {trim(study.summary, SUMMARY_CHARS)}")
    if study.caveats:
        lines.append(f"Caveats: {trim(study.caveats, CAVEAT_CHARS)}")
    return "\n".join(lines)


def compact_block(studies: list[Study], *, with_source: bool = False) -> str:
    return "\n\n".join(compact(s, with_source=with_source) for s in studies) or "(none)"
