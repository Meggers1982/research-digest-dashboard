"""Weekly patterns job.

    python -m scripts.patterns.run --week-ending 2026-09-13
    python -m scripts.patterns.run --sources dermatology-skin --bootstrap-weeks 2 --out /tmp/p
    python -m scripts.patterns.run --dry-run

Per topic: fold the week's studies into topic_memory/<source_id>.md and
propose evidence threads; gate them in code; verify the survivors with a
second call; gate again. Then one cross-topic pass over every topic's memory
and threads, verified and gated the same way. Writes data/patterns.json and
the memory files under --out (default: the repo root). Nothing is written
until every pass has finished, and nothing at all with --dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import guardrails, llm, memory, passes
from .data import CONTEXT_ONLY_SOURCES, Corpus, default_week_end, week_bounds

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_BOOTSTRAP_WEEKS = 8


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m scripts.patterns.run", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--week-ending", type=date.fromisoformat,
                   help="Last day (YYYY-MM-DD) of the 7-day week to review. Default: the most "
                        "recent Sunday before today (UTC).")
    p.add_argument("--sources", default="",
                   help="Comma-separated source ids to run (default: every topic).")
    p.add_argument("--out", type=Path, default=None,
                   help="Output root; writes <out>/data/patterns.json and <out>/topic_memory/. "
                        "Default: the repo root.")
    p.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data",
                   help="Where sources.json and the source files live.")
    p.add_argument("--bootstrap-weeks", type=int, default=0,
                   help=f"For a topic with no memory yet, first build it from the N weeks before "
                        f"the review week (one call per topic; max {MAX_BOOTSTRAP_WEEKS}).")
    p.add_argument("--force", action="store_true",
                   help="Re-run a topic whose memory already covers this week.")
    p.add_argument("--dry-run", action="store_true",
                   help="Load data and build prompts, print sizes; no API calls, no writes.")
    p.add_argument("--model", default=llm.MODEL)
    p.add_argument("--verify-model", default=llm.VERIFY_MODEL)
    p.add_argument("--effort", default=llm.MAIN_EFFORT)
    p.add_argument("--verify-effort", default=llm.VERIFY_EFFORT)
    p.add_argument("--workers", type=int, default=3, help="Topics processed in parallel.")
    args = p.parse_args(argv)
    if not 0 <= args.bootstrap_weeks <= MAX_BOOTSTRAP_WEEKS:
        p.error(f"--bootstrap-weeks must be between 0 and {MAX_BOOTSTRAP_WEEKS}")
    return args


def _load_previous(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _prior_first_seen(thread_id: str, *, mem, previous_threads: list[dict]) -> str:
    candidates = []
    item = mem.find(thread_id) if mem else None
    if item and item.first_seen:
        candidates.append(item.first_seen)
    for t in previous_threads:
        if t.get("id") == thread_id and t.get("first_seen"):
            candidates.append(t["first_seen"])
    return min(candidates) if candidates else ""


class Job:
    def __init__(self, args, corpus: Corpus, claude: llm.Claude, today: date):
        self.args = args
        self.corpus = corpus
        self.claude = claude
        self.today = today
        self.log = guardrails.DropLog()
        self.week_end = args.week_ending or default_week_end(today)
        self.week_start, _ = week_bounds(self.week_end)
        out = args.out or REPO_ROOT
        self.memory_dir = out / "topic_memory"
        self.patterns_path = out / "data" / "patterns.json"
        self.previous = _load_previous(self.patterns_path)
        self.same_week = self.previous.get("week_end") == self.week_end.isoformat()
        self.memories_to_write: dict[str, memory.TopicMemory] = {}

    # ---- topic -------------------------------------------------------------
    def topic(self, sid: str) -> dict:
        a, corpus = self.args, self.corpus
        label = corpus.label(sid)
        prior = memory.load(self.memory_dir, sid, label)
        prev_topic = (self.previous.get("topics") or {}).get(sid, {}) if self.same_week else {}
        result = {"label": label, "status": "ok", "study_count": 0, "threads": []}

        if prior is None and a.bootstrap_weeks:
            b_end = self.week_start - timedelta(days=1)
            b_start = self.week_start - timedelta(days=7 * a.bootstrap_weeks)
            history = corpus.window(sid, b_start, b_end)
            print(f"  [{sid}] bootstrap from {len(history)} studies, {b_start} to {b_end}")
            if history and not a.dry_run:
                try:
                    raw = passes.run_bootstrap(self.claude, corpus, sid, b_start, b_end, history,
                                               model=a.model, effort=a.effort)
                    boot = memory.from_model(raw.get("memory", {}), source_id=sid, label=label)
                    guardrails.check_memory(boot, corpus, sid, self.log)
                    boot.last_updated = self.today.isoformat()
                    boot.covered_through = b_end.isoformat()
                    prior = boot
                    self.memories_to_write[sid] = boot
                    result["bootstrapped"] = {"from": b_start.isoformat(), "to": b_end.isoformat(),
                                              "studies": len(history)}
                except Exception as exc:  # noqa: BLE001 - one topic must not sink the run
                    print(f"  [{sid}] bootstrap FAILED: {type(exc).__name__}: {exc}")
                    result["bootstrap_error"] = f"{type(exc).__name__}: {exc}"
            elif history:
                chars = len(passes.bootstrap_user_message(label=label, source_id=sid, start=b_start,
                                                          end=b_end, studies=history))
                print(f"  [{sid}] dry run: bootstrap prompt ~{chars // 4} tokens")

        if prior and prior.covered_through >= self.week_end.isoformat() and not a.force:
            print(f"  [{sid}] memory already covers {self.week_end}; skipping (use --force to redo)")
            if prev_topic:
                return {**prev_topic, "status": "carried_over"}
            return {**result, "status": "skipped", "note": "memory already covers this week"}

        week = corpus.window(sid, self.week_start, self.week_end)
        result["study_count"] = len(week)
        if not week:
            print(f"  [{sid}] no studies this week")
            return {**result, "status": "no_studies"}

        if a.dry_run:
            chars = len(passes.topic_user_message(label=label, source_id=sid, week_start=self.week_start,
                                                  week_end=self.week_end, memory_text=passes._memory_text(prior),
                                                  studies=week))
            print(f"  [{sid}] dry run: {len(week)} studies, topic prompt ~{chars // 4} tokens")
            return {**result, "status": "dry_run"}

        print(f"  [{sid}] {len(week)} studies; memory {'present' if prior else 'empty'}")
        try:
            raw = passes.run_topic(self.claude, corpus, sid, self.week_start, self.week_end, week, prior,
                                   model=a.model, effort=a.effort)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{sid}] topic pass FAILED: {type(exc).__name__}: {exc}")
            return {**result, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}

        threads = guardrails.unique_ids([guardrails.normalize_thread(t) for t in raw.get("threads", [])])
        result["proposed"] = len(threads)
        gate = guardrails.Gate(corpus, self.log, week_start=self.week_start, week_end=self.week_end,
                               source_id=sid)
        kept = gate.check(threads, "structural")
        rejected: set[str] = set()
        if kept:
            try:
                verdicts = passes.run_verify(self.claude, label=label, scope_id=sid, threads=kept,
                                             lookup=gate.study, model=a.verify_model, effort=a.verify_effort)
                kept, rejected = gate.apply_verification(kept, verdicts)
            except Exception as exc:  # noqa: BLE001 - unverified threads are not published
                print(f"  [{sid}] verification FAILED: {type(exc).__name__}: {exc}")
                for t in kept:
                    self.log.add(scope="topic", source_id=sid, thread_id=t["id"], title=t["title"],
                                 stage="verification", reason=f"verification call failed ({type(exc).__name__})")
                result["verify_error"] = f"{type(exc).__name__}: {exc}"
                kept = []
        verifier_dropped = {t["id"]: t.get("_verifier_dropped", []) for t in threads}

        new_mem = memory.from_model(raw.get("memory", {}), source_id=sid, label=label)
        guardrails.check_memory(new_mem, corpus, sid, self.log, prior=prior, rejected_ids=rejected,
                                verifier_dropped=verifier_dropped)
        new_mem.last_updated = self.today.isoformat()
        new_mem.covered_through = self.week_end.isoformat()
        self.memories_to_write[sid] = new_mem

        prev_threads = prev_topic.get("threads", []) if prev_topic else []
        result["threads"] = [
            gate.finalize(t, first_seen=_prior_first_seen(t["id"], mem=prior, previous_threads=prev_threads))
            for t in kept
        ]
        print(f"  [{sid}] {len(result['threads'])} of {len(threads)} proposed threads survived")
        return result

    # ---- cross-topic --------------------------------------------------------
    def cross(self, topics: dict) -> tuple[list[dict], dict]:
        a, corpus = self.args, self.corpus
        blocks = []
        for sid, res in topics.items():
            mem = self.memories_to_write.get(sid) or memory.load(self.memory_dir, sid, corpus.label(sid))
            if not mem and not res.get("threads"):
                continue
            blocks.append({"source_id": sid, "label": corpus.label(sid),
                           "memory_text": passes._memory_text(mem), "threads": res.get("threads", [])})
        if len(blocks) < 2:
            print("  [cross-topic] fewer than 2 topics with memory or threads; skipping")
            return [], {"status": "skipped", "note": "fewer than 2 topics with memory or threads"}

        context_ids = [s for s in CONTEXT_ONLY_SOURCES if s in corpus.by_source]
        context = []
        for cid in context_ids:
            context += corpus.window(cid, self.week_start, self.week_end)
        context_label = ", ".join(f"{corpus.label(c)} ({c})" for c in context_ids)
        in_scope = [b["source_id"] for b in blocks] + context_ids

        if a.dry_run:
            chars = len(passes.cross_user_message(week_start=self.week_start, week_end=self.week_end,
                                                  topics=blocks, context_label=context_label, context=context))
            print(f"  [cross-topic] dry run: {len(blocks)} topics + {len(context)} context studies, "
                  f"prompt ~{chars // 4} tokens")
            return [], {"status": "dry_run"}

        try:
            raw = passes.run_cross(self.claude, week_start=self.week_start, week_end=self.week_end,
                                   topics=blocks, context_label=context_label, context=context,
                                   model=a.model, effort=a.effort)
        except Exception as exc:  # noqa: BLE001
            print(f"  [cross-topic] FAILED: {type(exc).__name__}: {exc}")
            return [], {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

        threads = guardrails.unique_ids([guardrails.normalize_thread(t, cross=True) for t in raw])
        gate = guardrails.Gate(corpus, self.log, week_start=self.week_start, week_end=self.week_end,
                               source_id=None, in_scope_sources=in_scope)
        kept = gate.check(threads, "structural")
        meta = {"status": "ok", "proposed": len(threads), "context_sources": context_ids}
        if kept:
            try:
                verdicts = passes.run_verify(self.claude, label="cross-topic", scope_id="cross-topic",
                                             threads=kept, lookup=gate.study,
                                             model=a.verify_model, effort=a.verify_effort)
                kept, _ = gate.apply_verification(kept, verdicts)
            except Exception as exc:  # noqa: BLE001
                print(f"  [cross-topic] verification FAILED: {type(exc).__name__}: {exc}")
                for t in kept:
                    self.log.add(scope="cross_topic", source_id="cross_topic", thread_id=t["id"],
                                 title=t["title"], stage="verification",
                                 reason=f"verification call failed ({type(exc).__name__})")
                meta["verify_error"] = f"{type(exc).__name__}: {exc}"
                kept = []
        prev_cross = self.previous.get("cross_topic", []) if self.same_week else []
        final = [gate.finalize(t, first_seen=_prior_first_seen(t["id"], mem=None, previous_threads=prev_cross))
                 for t in kept]
        print(f"  [cross-topic] {len(final)} of {len(threads)} proposed threads survived")
        return final, meta

    # ---- whole run ----------------------------------------------------------
    def run(self, topic_ids: list[str]) -> dict:
        print(f"Patterns for week {self.week_start} to {self.week_end}; topics: {', '.join(topic_ids)}")
        workers = max(1, min(self.args.workers, len(topic_ids) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            done = dict(zip(topic_ids, pool.map(self.topic, topic_ids)))

        topics = {}
        if self.same_week:
            for sid, res in (self.previous.get("topics") or {}).items():
                if sid not in done and sid in self.corpus.by_source:
                    topics[sid] = {**res, "status": "carried_over"}
        topics.update(done)
        topics = {sid: topics[sid] for sid in self.corpus.topic_ids() if sid in topics}

        cross, cross_meta = self.cross(topics)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "week_start": self.week_start.isoformat(),
            "week_end": self.week_end.isoformat(),
            "model": self.args.model,
            "verify_model": self.args.verify_model,
            "effort": {"main": self.args.effort, "verify": self.args.verify_effort},
            "guardrails": {"min_pmids": guardrails.MIN_PMIDS, "min_journals": guardrails.MIN_JOURNALS},
            "topics": topics,
            "cross_topic": cross,
            "cross_topic_meta": cross_meta,
            "dropped": self.log.entries,
            "usage": self.claude.log.summary(),
        }

    def write(self, payload: dict) -> None:
        for mem in self.memories_to_write.values():
            path = memory.save(self.memory_dir, mem)
            print(f"  wrote {path}")
        self.patterns_path.parent.mkdir(parents=True, exist_ok=True)
        self.patterns_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
        print(f"  wrote {self.patterns_path}")


def main(argv=None) -> int:
    args = parse_args(argv)
    corpus = Corpus.load(args.data_dir)
    all_topics = corpus.topic_ids()
    if args.sources:
        wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
        bad = [s for s in wanted if s not in all_topics]
        if bad:
            print(f"Unknown or non-topic source(s): {', '.join(bad)}. Topics: {', '.join(all_topics)}. "
                  f"({', '.join(sorted(CONTEXT_ONLY_SOURCES))} keeps its memory upstream.)")
            return 2
        topic_ids = [s for s in all_topics if s in wanted]
    else:
        topic_ids = all_topics

    job = Job(args, corpus, llm.Claude(), datetime.now(timezone.utc).date())
    payload = job.run(topic_ids)

    usage = payload["usage"]
    print(f"\nModel calls: {usage['calls']}  input {usage['input_tokens']} "
          f"(cache read {usage['cache_read_input_tokens']}, write {usage['cache_creation_input_tokens']})  "
          f"output {usage['output_tokens']}  est. ${usage['estimated_cost_usd']}")
    kept = sum(len(t.get("threads", [])) for t in payload["topics"].values())
    print(f"Threads kept: {kept} topic, {len(payload['cross_topic'])} cross-topic; "
          f"drops logged: {len(payload['dropped'])}")
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0
    job.write(payload)
    failed = [sid for sid, t in payload["topics"].items() if t.get("status") == "failed"]
    if payload.get("cross_topic_meta", {}).get("status") == "failed":
        failed.append("cross-topic")
    if failed and os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::Patterns job failed for: {', '.join(failed)}. See the log above.")
    if failed and len([f for f in failed if f != "cross-topic"]) == len(topic_ids):
        print("Every topic failed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
