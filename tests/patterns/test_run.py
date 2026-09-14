"""Whole job against the fixture corpus and a scripted fake client."""
import json

import pytest

from scripts.patterns import llm, prompts, run
from tests.patterns.conftest import FakeClient, message, system_of, thread, user_of


def verdict(id, pmids, drop=(), verdict="keep", revised=""):
    return {
        "id": id, "verdict": verdict, "reason": "checked", "revised_claim": revised,
        "pmids": [{"pmid": str(p), "verdict": "drop" if p in drop else "keep", "reason": "off-topic"}
                  for p in pmids],
        "flags": {"single_country": False, "single_group": False, "same_cohort_suspected": False},
        "flag_note": "",
    }


def responder(kw):
    system, user = system_of(kw), user_of(kw)
    if system == prompts.TOPIC_SYSTEM and "source id: alpha" in user:
        return message({
            "threads": [
                thread([1001, 1002, 1003], id="alpha-good", flags={"single_country": True},
                       flag_note="all from Japan"),
                thread([1001, 1002, 9999], id="alpha-missing"),
                thread([1001, 1002, 1007], id="alpha-cohort", flags={"same_cohort_suspected": True}),
            ],
            "memory": {
                "established": [{"id": "est", "text": "Established thing", "pmids": ["1001", "1005"]}],
                "emerging": [
                    {"id": "alpha-good", "text": "Emerging thing", "pmids": ["1001", "1002", "1003"]},
                    {"id": "ghost", "text": "Invented", "pmids": ["9999"]},
                ],
            },
        })
    if system == prompts.TOPIC_SYSTEM and "source id: beta" in user:
        return message({
            "threads": [thread([2001, 2002, 2003], id="beta-thread")],
            "memory": {"established": [], "emerging": [
                {"id": "beta-thread", "text": "Beta pattern", "pmids": ["2001", "2002"]}]},
        })
    if system == prompts.VERIFY_SYSTEM and user.startswith("Scope: Alpha Topic"):
        return message({"threads": [verdict("alpha-good", [1001, 1002, 1003], revised="Narrower.")]})
    if system == prompts.VERIFY_SYSTEM and user.startswith("Scope: Beta Topic"):
        return message({"threads": [verdict("beta-thread", [2001, 2002, 2003], drop=(2003,))]})
    if system == prompts.CROSS_SYSTEM:
        return message({"threads": [
            thread([1001, 2001, 2002], id="cross-good", link_type="drug", link="metformin",
                   topics=["alpha", "beta"]),
            thread([2001, 2002, 2003], id="cross-one-topic", link_type="drug", link="x", topics=["beta"]),
        ]})
    if system == prompts.VERIFY_SYSTEM and user.startswith("Scope: cross-topic"):
        return message({"threads": [verdict("cross-good", [1001, 2001, 2002])]})
    raise AssertionError(f"unexpected call: {user[:80]}")


REAL_CLAUDE = llm.Claude


def patch_client(monkeypatch, client):
    monkeypatch.setattr(run.llm, "Claude", lambda: REAL_CLAUDE(client=client))


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient(responder)
    patch_client(monkeypatch, client)
    return client


def run_job(data_dir, out, *extra):
    return run.main(["--data-dir", str(data_dir), "--out", str(out), "--week-ending", "2026-09-13",
                     "--workers", "1", *extra])


def test_full_run_outputs(fake, data_dir, tmp_path):
    out = tmp_path / "out"
    assert run_job(data_dir, out) == 0
    payload = json.loads((out / "data" / "patterns.json").read_text())

    # Top-level shape.
    for key in ("generated_at", "week_start", "week_end", "model", "topics", "cross_topic", "dropped"):
        assert key in payload
    assert payload["week_start"] == "2026-09-07" and payload["week_end"] == "2026-09-13"
    assert payload["model"] == "claude-opus-5"
    assert list(payload["topics"]) == ["alpha", "beta"]  # no senior-research, no pitch-ideas

    # Alpha: one thread survives both gates, with its flag and the verifier's narrower claim.
    alpha = payload["topics"]["alpha"]
    assert alpha["label"] == "Alpha Topic" and alpha["status"] == "ok"
    [t] = alpha["threads"]
    assert t["id"] == "alpha-good" and t["claim"] == "Narrower." and t["original_claim"] == "Claim alpha-good"
    assert t["flags"]["single_country"] is True and t["flag_note"] == "all from Japan"
    assert t["pmids"] == ["1001", "1002", "1003"]
    assert t["first_seen"] == "2026-09-09"
    assert t["design_mix"] == {"cohort": 3}
    assert t["journal_count"] == 3 and t["this_week_count"] == 3
    study = t["studies"][0]
    for key in ("pmid", "headline", "journal", "relevance_score", "summary", "caveats", "design", "doi"):
        assert key in study

    # Beta: the verifier removed a PMID, which broke the 3-PMID minimum.
    assert payload["topics"]["beta"]["threads"] == []

    # Cross-topic: the independent thread survives, the one-topic thread doesn't.
    [c] = payload["cross_topic"]
    assert c["id"] == "cross-good" and c["topics"] == ["alpha", "beta"] and c["link"] == "metformin"
    assert all("sources" in s for s in c["studies"])

    # Every drop is logged with a reason.
    got = {(d["thread_id"], d.get("pmid"), d["reason"]) for d in payload["dropped"]}
    assert ("alpha-missing", "9999", "PMID does not exist in the loaded data") in got
    assert ("alpha-missing", None, "only 2 distinct PMIDs (needs 3)") in got
    assert any(r[0] == "alpha-cohort" and r[2].startswith("same cohort suspected") for r in got)
    assert ("beta-thread", "2003", "verifier: off-topic") in got
    assert ("cross-one-topic", None,
            "evidence does not come from 2 different topics via 2 different studies") in got
    assert ("ghost", None, "no valid PMIDs left") in got
    assert all(d["reason"] and d["stage"] for d in payload["dropped"])

    # Memory files, keyed by source id, and nothing for context-only sources.
    names = sorted(p.name for p in (out / "topic_memory").iterdir())
    assert names == ["alpha.md", "beta.md"]
    alpha_md = (out / "topic_memory" / "alpha.md").read_text()
    assert "covers studies through 2026-09-13" in alpha_md
    assert "ghost" not in alpha_md and "9999" not in alpha_md
    beta_md = (out / "topic_memory" / "beta.md").read_text()
    assert "**beta-thread**" in beta_md  # 2-study pattern stays as an emerging thread

    # Calls: 2 topic + 2 verify + 1 cross + 1 cross verify.
    assert len(fake.calls) == 6
    assert payload["usage"]["calls"] == 6


def test_excluded_and_old_studies_are_not_sent(fake, data_dir, tmp_path):
    run_job(data_dir, tmp_path / "out")
    alpha_user = next(user_of(c) for c in fake.calls
                      if system_of(c) == prompts.TOPIC_SYSTEM and "source id: alpha" in user_of(c))
    assert "PMID 1001 " in alpha_user
    assert "PMID 1006 " not in alpha_user  # excluded
    assert "PMID 1012 " not in alpha_user  # excluded in another source
    assert "PMID 1005 " not in alpha_user  # added before this week
    assert "score 4" in alpha_user  # low scores are included


def test_rerun_same_week_does_not_refold_memory(fake, data_dir, tmp_path):
    out = tmp_path / "out"
    run_job(data_dir, out)
    before = (out / "topic_memory" / "alpha.md").read_text()
    fake.calls.clear()
    assert run_job(data_dir, out) == 0
    topic_calls = [c for c in fake.calls if system_of(c) == prompts.TOPIC_SYSTEM]
    assert topic_calls == []
    payload = json.loads((out / "data" / "patterns.json").read_text())
    assert payload["topics"]["alpha"]["status"] == "carried_over"
    assert [t["id"] for t in payload["topics"]["alpha"]["threads"]] == ["alpha-good"]
    assert (out / "topic_memory" / "alpha.md").read_text() == before


def test_bootstrap_builds_memory_from_prior_weeks(monkeypatch, data_dir, tmp_path):
    def boot_responder(kw):
        if system_of(kw) == prompts.BOOTSTRAP_SYSTEM:
            assert "PMID 1005 " in user_of(kw) and "PMID 1001 " not in user_of(kw)
            return message({"memory": {"established": [], "emerging": [
                {"id": "early", "text": "Seen before", "pmids": ["1005", "1010"]}]}})
        return responder(kw)

    client = FakeClient(boot_responder)
    patch_client(monkeypatch, client)
    out = tmp_path / "out"
    assert run_job(data_dir, out, "--sources", "alpha", "--bootstrap-weeks", "4") == 0
    boots = [c for c in client.calls if system_of(c) == prompts.BOOTSTRAP_SYSTEM]
    assert len(boots) == 1  # beta had no history in the window and wasn't requested
    topic_user = next(user_of(c) for c in client.calls if system_of(c) == prompts.TOPIC_SYSTEM)
    assert "**early**" in topic_user  # the weekly pass saw the bootstrapped memory
    payload = json.loads((out / "data" / "patterns.json").read_text())
    assert payload["topics"]["alpha"]["bootstrapped"]["studies"] >= 1
    assert payload["cross_topic_meta"]["status"] == "skipped"


def test_dry_run_makes_no_calls_and_writes_nothing(fake, data_dir, tmp_path):
    out = tmp_path / "out"
    assert run_job(data_dir, out, "--dry-run", "--bootstrap-weeks", "2") == 0
    assert fake.calls == []
    assert not out.exists()


def test_context_only_source_cannot_be_a_topic(fake, data_dir, tmp_path):
    assert run_job(data_dir, tmp_path / "out", "--sources", "senior-research") == 2


def test_failed_topic_leaves_memory_untouched(monkeypatch, data_dir, tmp_path):
    def failing(kw):
        if system_of(kw) == prompts.TOPIC_SYSTEM and "source id: beta" in user_of(kw):
            return message(text="", stop_reason="refusal")
        return responder(kw)

    client = FakeClient(failing)
    patch_client(monkeypatch, client)
    out = tmp_path / "out"
    assert run_job(data_dir, out) == 0
    payload = json.loads((out / "data" / "patterns.json").read_text())
    assert payload["topics"]["beta"]["status"] == "failed"
    assert "ModelDeclined" in payload["topics"]["beta"]["error"]
    assert not (out / "topic_memory" / "beta.md").exists()
    assert payload["topics"]["alpha"]["threads"]
