"""Guardrails enforced in code, whatever the prompt said.

Every thread goes through the same gate twice: once after the pass that
proposed it (stage "structural") and again after the verification pass
(stage "verification"). Every drop, of a whole thread or of one PMID from a
thread, is logged with its reason and ends up in patterns.json's `dropped`.

The gate:
- each cited PMID exists in the loaded data (for a topic thread, in that
  topic's own data) and is not excluded;
- at least MIN_PMIDS distinct PMIDs and MIN_JOURNALS distinct journals;
- at least one cited study was added during the week under review;
- `same_cohort_suspected` drops the thread outright;
- a cross-topic thread needs two different PMIDs carried by two different
  sources. One paper that appears in two digests is one piece of evidence.

`single_country` and `single_group` never drop anything. They stay on the
thread so the page can show them.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import memory as memory_mod
from .data import Corpus, clean_pmid, journal_key
from .prompts import DESIGNS, DIRECTIONS, LINK_TYPES

MIN_PMIDS = 3
MIN_JOURNALS = 2
FLAG_KEYS = ("single_country", "single_group", "same_cohort_suspected")


@dataclass
class DropLog:
    entries: list[dict] = field(default_factory=list)
    quiet: bool = False

    def add(self, *, scope: str, source_id: str, thread_id: str, title: str,
            stage: str, reason: str, pmid: str | None = None) -> None:
        entry = {
            "scope": scope,
            "source_id": source_id,
            "thread_id": thread_id,
            "title": title,
            "stage": stage,
            "reason": reason,
        }
        if pmid:
            entry["pmid"] = pmid
        self.entries.append(entry)
        if not self.quiet:
            what = f"PMID {pmid} from " if pmid else ""
            tag = "DEMOTE" if reason.startswith("demoted") else "DROP"
            print(f"    {tag} [{scope}:{source_id}] {stage}: {what}'{thread_id}' — {reason}")


def normalize_thread(raw: dict, *, cross: bool = False) -> dict:
    """Coerce a model thread into the shape the rest of the job uses."""
    pmids: list[str] = []
    for p in raw.get("pmids") or []:
        p = clean_pmid(p)
        if p and p not in pmids:
            pmids.append(p)
    flags = {k: bool((raw.get("flags") or {}).get(k)) for k in FLAG_KEYS}
    designs = {}
    for row in raw.get("designs") or []:
        p = clean_pmid(row.get("pmid"))
        d = row.get("design")
        if p:
            designs[p] = d if d in DESIGNS else "other"
    direction = raw.get("direction") if raw.get("direction") in DIRECTIONS else "new"
    thread = {
        "id": memory_mod.slugify(raw.get("id") or raw.get("title") or ""),
        "title": " ".join(str(raw.get("title") or "").split()),
        "claim": " ".join(str(raw.get("claim") or "").split()),
        "pmids": pmids,
        "direction": direction,
        "designs": designs,
        "flags": flags,
        "flag_note": " ".join(str(raw.get("flag_note") or "").split()),
    }
    if cross:
        thread["link_type"] = raw.get("link_type") if raw.get("link_type") in LINK_TYPES else "other"
        thread["link"] = " ".join(str(raw.get("link") or "").split())
        thread["model_topics"] = [str(t) for t in raw.get("topics") or []]
    else:
        thread["relates_to"] = memory_mod.slugify(raw["relates_to"]) if raw.get("relates_to") else ""
    return thread


def unique_ids(threads: list[dict]) -> list[dict]:
    seen: set[str] = set()
    for t in threads:
        base, n = t["id"], 2
        while t["id"] in seen:
            t["id"] = f"{base}-{n}"
            n += 1
        seen.add(t["id"])
    return threads


class Gate:
    """The checks, bound to one scope ("topic" for a source, or "cross_topic")."""

    def __init__(self, corpus: Corpus, log: DropLog, *, week_start: date, week_end: date,
                 source_id: str | None, in_scope_sources: list[str] | None = None):
        self.corpus = corpus
        self.log = log
        self.week_start = week_start
        self.week_end = week_end
        self.source_id = source_id  # None means cross-topic
        self.in_scope = set(in_scope_sources or corpus.by_source)

    @property
    def scope(self) -> str:
        return "topic" if self.source_id else "cross_topic"

    @property
    def scope_id(self) -> str:
        return self.source_id or "cross_topic"

    # ---- per-PMID --------------------------------------------------------
    def study(self, pmid: str):
        if self.source_id:
            return self.corpus.study(pmid, self.source_id)
        for sid in self.in_scope:
            hit = self.corpus.study(pmid, sid)
            if hit:
                return hit
        return None

    def pmid_problem(self, pmid: str) -> str | None:
        if pmid in self.corpus.excluded:
            return "cites an excluded study"
        if self.study(pmid) is not None:
            return None
        if self.source_id and self.corpus.study(pmid) is not None:
            return f"PMID is not in {self.source_id}'s data"
        return "PMID does not exist in the loaded data"

    def this_week(self, pmid: str) -> bool:
        sids = [self.source_id] if self.source_id else [
            s for s in self.corpus.sources_of(pmid) if s in self.in_scope
        ]
        for sid in sids:
            st = self.corpus.study(pmid, sid)
            if st and st.run_date and self.week_start <= st.run_date <= self.week_end:
                return True
        return False

    def sources_for(self, pmid: str) -> set[str]:
        return {s for s in self.corpus.sources_of(pmid) if s in self.in_scope}

    # ---- gate --------------------------------------------------------------
    def _drop(self, thread: dict, stage: str, reason: str, pmid: str | None = None) -> None:
        self.log.add(scope=self.scope, source_id=self.scope_id, thread_id=thread["id"],
                     title=thread.get("title", ""), stage=stage, reason=reason, pmid=pmid)

    def filter_pmids(self, thread: dict, stage: str) -> None:
        kept = []
        for p in thread["pmids"]:
            problem = self.pmid_problem(p)
            if problem:
                self._drop(thread, stage, problem, pmid=p)
            else:
                kept.append(p)
        thread["pmids"] = kept
        thread["designs"] = {p: d for p, d in thread["designs"].items() if p in kept}

    def minimums_problem(self, thread: dict) -> str | None:
        if thread["flags"].get("same_cohort_suspected"):
            note = f" ({thread['flag_note']})" if thread.get("flag_note") else ""
            return f"same cohort suspected{note}"
        n = len(thread["pmids"])
        if n < MIN_PMIDS:
            return f"only {n} distinct PMID{'s' if n != 1 else ''} (needs {MIN_PMIDS})"
        journals = {journal_key(self.study(p).journal) for p in thread["pmids"]}
        journals.discard("")
        if len(journals) < MIN_JOURNALS:
            return f"only {len(journals)} distinct journal{'s' if len(journals) != 1 else ''} (needs {MIN_JOURNALS})"
        if not any(self.this_week(p) for p in thread["pmids"]):
            return "no cited study was added this week"
        if not self.source_id and not self.independent_sources(thread["pmids"]):
            return "evidence does not come from 2 different topics via 2 different studies"
        return None

    def independent_sources(self, pmids: list[str]) -> bool:
        """Two different PMIDs, carried by two different sources."""
        srcs = {p: self.sources_for(p) for p in pmids}
        for i, a in enumerate(pmids):
            for b in pmids[i + 1:]:
                if any(sa != sb for sa in srcs[a] for sb in srcs[b]):
                    return True
        return False

    def check(self, threads: list[dict], stage: str) -> list[dict]:
        kept = []
        for t in threads:
            self.filter_pmids(t, stage)
            problem = self.minimums_problem(t)
            if problem:
                self._drop(t, stage, problem)
            else:
                kept.append(t)
        return kept

    # ---- verification ------------------------------------------------------
    def apply_verification(self, threads: list[dict], verdicts: list[dict]) -> tuple[list[dict], set[str]]:
        """Apply the verifier's per-PMID and per-thread verdicts, then re-gate.

        Returns (kept threads, ids of threads the verifier itself rejected).
        A thread or PMID the verifier skipped counts as dropped.
        """
        stage = "verification"
        by_id = {memory_mod.slugify(v.get("id", "")): v for v in verdicts or []}
        kept, rejected = [], set()
        for t in threads:
            v = by_id.get(t["id"])
            if v is None:
                self._drop(t, stage, "verifier returned no verdict for this thread")
                rejected.add(t["id"])
                continue
            pmid_verdicts = {clean_pmid(p.get("pmid")): p for p in v.get("pmids") or []}
            survivors = []
            for p in t["pmids"]:
                pv = pmid_verdicts.get(p)
                if pv is None:
                    self._drop(t, stage, "verifier gave no verdict for this PMID", pmid=p)
                elif pv.get("verdict") != "keep":
                    self._drop(t, stage, f"verifier: {pv.get('reason') or 'does not support the claim'}", pmid=p)
                else:
                    survivors.append(p)
            t["_verifier_dropped"] = [p for p in t["pmids"] if p not in survivors]
            t["pmids"] = survivors
            t["designs"] = {p: d for p, d in t["designs"].items() if p in survivors}
            for k in FLAG_KEYS:
                t["flags"][k] = bool(t["flags"].get(k) or (v.get("flags") or {}).get(k))
            note = " ".join(str(v.get("flag_note") or "").split())
            if note and note not in t.get("flag_note", ""):
                t["flag_note"] = f"{t['flag_note']}; {note}".strip("; ") if t.get("flag_note") else note
            if v.get("verdict") != "keep":
                self._drop(t, stage, f"verifier: {v.get('reason') or 'claim not supported'}")
                rejected.add(t["id"])
                continue
            revised = " ".join(str(v.get("revised_claim") or "").split())
            if revised and revised != t["claim"]:
                t["original_claim"] = t["claim"]
                t["claim"] = revised
            t["verification_note"] = " ".join(str(v.get("reason") or "").split())
            kept.append(t)
        return self.check(kept, stage), rejected

    # ---- output -------------------------------------------------------------
    def finalize(self, thread: dict, *, first_seen: str = "") -> dict:
        """Attach study cards and counts so the page renders from patterns.json alone."""
        studies = []
        for p in thread["pmids"]:
            st = self.study(p)
            card = st.card()
            card["design"] = thread["designs"].get(p, "other")
            card["this_week"] = self.this_week(p)
            if not self.source_id:
                card["sources"] = sorted(self.sources_for(p))
            studies.append(card)
        run_dates = [s["run_date"] for s in studies if s["run_date"]]
        earliest = min(run_dates) if run_dates else ""
        out = {k: v for k, v in thread.items()
               if k not in ("designs", "pmids", "model_topics") and not k.startswith("_")}
        out["pmids"] = list(thread["pmids"])
        out["first_seen"] = min(x for x in (first_seen, earliest) if x) if (first_seen or earliest) else ""
        out["design_mix"] = dict(Counter(s["design"] for s in studies).most_common())
        out["journal_count"] = len({journal_key(s["journal"]) for s in studies} - {""})
        out["this_week_count"] = sum(1 for s in studies if s["this_week"])
        if not self.source_id:
            out["topics"] = sorted({sid for s in studies for sid in s["sources"]})
        out["studies"] = studies
        return out


def check_memory(mem, corpus: Corpus, source_id: str, log: DropLog, *,
                 prior=None, rejected_ids: set[str] = frozenset(),
                 verifier_dropped: dict[str, list[str]] | None = None):
    """Hold memory bullets to the same PMID rules as threads.

    - PMIDs that don't exist in this topic's data, or are excluded, are removed.
    - PMIDs the verifier rejected from a thread are removed from the memory
      bullet that shares the thread's id.
    - A bullet newly added this run for a thread the verifier rejected is
      removed. A bullet that only failed the 3-PMID/2-journal minimum stays:
      "emerging" is where a two-study pattern belongs until a third arrives.
    - A bullet left with no PMIDs is removed.
    - An "established" bullet needs MIN_PMIDS PMIDs from MIN_JOURNALS journals,
      added in at least 2 different weeks. One that falls short is demoted to
      "emerging" (and logged), not deleted. So is one the model promoted this
      run while the verifier rejected its matching thread.
    - `first_seen` on emerging bullets comes from the prior memory for the same
      id, else the earliest date a cited study was added.
    """
    verifier_dropped = verifier_dropped or {}
    prior_ids = prior.ids() if prior else set()
    prior_established = {i.id for i in prior.established} if prior else set()
    demoted: list = []
    for section in ("established", "emerging"):
        kept = []
        pending = getattr(mem, section) if section == "established" else [*demoted, *mem.emerging]
        for item in pending:
            if item.id in rejected_ids and item.id not in prior_ids:
                log.add(scope="memory", source_id=source_id, thread_id=item.id, title=item.text[:80],
                        stage="memory", reason="new bullet for a thread the verifier rejected")
                continue
            good = []
            for p in item.pmids:
                problem = None
                if p in corpus.excluded:
                    problem = "cites an excluded study"
                elif corpus.study(p, source_id) is None:
                    problem = "PMID does not exist in this topic's data"
                elif p in verifier_dropped.get(item.id, []):
                    problem = "verifier rejected this PMID for the matching thread"
                if problem:
                    log.add(scope="memory", source_id=source_id, thread_id=item.id, title=item.text[:80],
                            stage="memory", reason=problem, pmid=p)
                elif p not in good:
                    good.append(p)
            item.pmids = good
            if not good:
                log.add(scope="memory", source_id=source_id, thread_id=item.id, title=item.text[:80],
                        stage="memory", reason="no valid PMIDs left")
                continue
            if section == "established":
                shortfall = _established_shortfall(good, corpus, source_id)
                if not shortfall and item.id in rejected_ids and item.id not in prior_established:
                    shortfall = "the verifier rejected this week's matching thread"
                if shortfall:
                    log.add(scope="memory", source_id=source_id, thread_id=item.id, title=item.text[:80],
                            stage="memory", reason=f"demoted to emerging: {shortfall}")
                    demoted.append(item)
                    continue
            if section == "emerging":
                old = prior.find(item.id) if prior else None
                dates = [corpus.study(p, source_id).run_date for p in good]
                dates = [d.isoformat() for d in dates if d]
                candidates = [x for x in ((old.first_seen if old else ""), min(dates) if dates else "") if x]
                item.first_seen = min(candidates) if candidates else ""
            kept.append(item)
        setattr(mem, section, kept)
    return mem


def _established_shortfall(pmids: list[str], corpus: Corpus, source_id: str) -> str:
    studies = [corpus.study(p, source_id) for p in pmids]
    if len(pmids) < MIN_PMIDS:
        return f"only {len(pmids)} PMIDs (needs {MIN_PMIDS})"
    journals = {journal_key(s.journal) for s in studies} - {""}
    if len(journals) < MIN_JOURNALS:
        return f"only {len(journals)} journal(s) (needs {MIN_JOURNALS})"
    weeks = {s.run_date - timedelta(days=s.run_date.weekday()) for s in studies if s.run_date}
    if len(weeks) < 2:
        return "all its studies were added in the same week"
    return ""
