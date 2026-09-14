from scripts.patterns import guardrails
from scripts.patterns.data import journal_key
from tests.patterns.conftest import WEEK_END, WEEK_START, thread


def gate(corpus, log, source_id="alpha", in_scope=None):
    return guardrails.Gate(corpus, log, week_start=WEEK_START, week_end=WEEK_END,
                           source_id=source_id, in_scope_sources=in_scope)


def run(corpus, log, raw_threads, source_id="alpha", in_scope=None, cross=False):
    threads = guardrails.unique_ids([guardrails.normalize_thread(t, cross=cross) for t in raw_threads])
    return gate(corpus, log, source_id, in_scope).check(threads, "structural")


def reasons(log):
    return [e["reason"] for e in log.entries]


# ---- corpus rules -------------------------------------------------------------

def test_excluded_studies_are_never_loaded(corpus):
    assert corpus.study("1006", "alpha") is None
    # Excluded in beta only, but excluded everywhere.
    assert corpus.study("1012", "alpha") is None
    assert "1012" in corpus.excluded


def test_skipped_and_context_sources(corpus):
    assert "pitch-ideas" not in corpus.by_source
    assert corpus.topic_ids() == ["alpha", "beta"]
    assert "senior-research" in corpus.by_source  # loaded as cross-topic context


def test_duplicate_pmid_in_a_source_keeps_earliest_run_date(corpus):
    assert corpus.study("1013", "alpha").run_date.isoformat() == "2026-08-20"
    week = [s.pmid for s in corpus.window("alpha", WEEK_START, WEEK_END)]
    assert "1013" not in week and "1006" not in week


def test_journal_key_merges_formatting_variants():
    assert journal_key("The Lancet (London, England)") == journal_key("Lancet")
    assert journal_key("Dermatitis: Contact, Atopic") == journal_key("dermatitis")
    assert journal_key("Journal B") != journal_key("Journal C")


# ---- structural gate ------------------------------------------------------------

def test_good_thread_survives(corpus, log):
    kept = run(corpus, log, [thread([1001, 1002, 1003])])
    assert [t["id"] for t in kept] == ["t1"]
    assert log.entries == []


def test_missing_pmid_is_dropped_but_thread_survives_if_still_valid(corpus, log):
    kept = run(corpus, log, [thread([1001, 1002, 1003, 9999])])
    assert kept[0]["pmids"] == ["1001", "1002", "1003"]
    assert log.entries[0]["pmid"] == "9999"
    assert "does not exist" in log.entries[0]["reason"]


def test_missing_pmid_that_breaks_the_minimum_drops_the_thread(corpus, log):
    kept = run(corpus, log, [thread([1001, 1002, 9999])])
    assert kept == []
    assert any("only 2 distinct PMIDs" in r for r in reasons(log))


def test_pmid_from_another_topic_is_rejected(corpus, log):
    run(corpus, log, [thread([1001, 1002, 1003, 2001])])
    assert "PMID is not in alpha's data" in reasons(log)


def test_excluded_study_is_rejected(corpus, log):
    kept = run(corpus, log, [thread([1001, 1002, 1006, 1012])])
    assert kept == []
    dropped = {e.get("pmid"): e["reason"] for e in log.entries if e.get("pmid")}
    assert dropped == {"1006": "cites an excluded study", "1012": "cites an excluded study"}


def test_fewer_than_three_pmids_after_dedupe(corpus, log):
    kept = run(corpus, log, [thread([1001, 1001, "PMID 1002"])])
    assert kept == []
    assert reasons(log) == ["only 2 distinct PMIDs (needs 3)"]


def test_fewer_than_two_journals(corpus, log):
    kept = run(corpus, log, [thread([1001, 1004, 1009])])
    assert kept == []
    assert reasons(log) == ["only 1 distinct journal (needs 2)"]


def test_journal_variants_count_once(corpus, log):
    # Two spellings of the Lancet plus one other journal: 2 journals, not 3.
    kept = run(corpus, log, [thread([1007, 1008, 1001])])
    assert gate(corpus, log).finalize(kept[0])["journal_count"] == 2


def test_same_cohort_suspected_drops(corpus, log):
    kept = run(corpus, log, [thread([1001, 1002, 1003], flags={"same_cohort_suspected": True},
                                    flag_note="all UK Biobank")])
    assert kept == []
    assert reasons(log) == ["same cohort suspected (all UK Biobank)"]


def test_needs_a_study_from_this_week(corpus, log):
    kept = run(corpus, log, [thread([1005, 1010, 1011])])
    assert kept == []
    assert reasons(log) == ["no cited study was added this week"]


def test_single_country_and_group_flags_stay_visible(corpus, log):
    kept = run(corpus, log, [thread([1001, 1002, 1003],
                                    flags={"single_country": True, "single_group": True},
                                    flag_note="all from Denmark")])
    out = gate(corpus, log).finalize(kept[0])
    assert out["flags"] == {"single_country": True, "single_group": True, "same_cohort_suspected": False}
    assert out["flag_note"] == "all from Denmark"


def test_duplicate_thread_ids_are_made_unique(corpus, log):
    kept = run(corpus, log, [thread([1001, 1002, 1003]), thread([1001, 1002, 1007])])
    assert [t["id"] for t in kept] == ["t1", "t1-2"]


# ---- verification -----------------------------------------------------------------

def verdict(id, pmids, *, verdict="keep", drop=(), revised="", flags=None, reason="ok"):
    return {
        "id": id, "verdict": verdict, "reason": reason, "revised_claim": revised,
        "pmids": [{"pmid": str(p), "verdict": "drop" if p in drop else "keep", "reason": "off-topic"}
                  for p in pmids],
        "flags": {"single_country": False, "single_group": False, "same_cohort_suspected": False,
                  **(flags or {})},
        "flag_note": "",
    }


def test_verifier_pmid_drop_reapplies_minimum(corpus, log):
    g = gate(corpus, log)
    kept = run(corpus, log, [thread([1001, 1002, 1003])])
    kept, rejected = g.apply_verification(kept, [verdict("t1", [1001, 1002, 1003], drop=(1003,))])
    assert kept == [] and rejected == set()
    stages = [(e["stage"], e.get("pmid"), e["reason"]) for e in log.entries]
    assert ("verification", "1003", "verifier: off-topic") in stages
    assert ("verification", None, "only 2 distinct PMIDs (needs 3)") in stages


def test_verifier_thread_drop(corpus, log):
    g = gate(corpus, log)
    kept = run(corpus, log, [thread([1001, 1002, 1003])])
    kept, rejected = g.apply_verification(
        kept, [verdict("t1", [1001, 1002, 1003], verdict="drop", reason="claim overstated")])
    assert kept == [] and rejected == {"t1"}
    assert "verifier: claim overstated" in reasons(log)


def test_verifier_silence_counts_as_drop(corpus, log):
    g = gate(corpus, log)
    kept = run(corpus, log, [thread([1001, 1002, 1003]), thread([1001, 1002, 1007], id="t2")])
    partial = verdict("t1", [1001, 1002])  # says nothing about 1003, nothing about t2
    kept, rejected = g.apply_verification(kept, [partial])
    assert kept == []
    assert "verifier returned no verdict for this thread" in reasons(log)
    assert "verifier gave no verdict for this PMID" in reasons(log)
    assert rejected == {"t2"}


def test_verifier_can_narrow_claim_and_add_flags(corpus, log):
    g = gate(corpus, log)
    kept = run(corpus, log, [thread([1001, 1002, 1003, 1007])])
    kept, _ = g.apply_verification(kept, [verdict("t1", [1001, 1002, 1003, 1007],
                                                  revised="Narrower claim.",
                                                  flags={"single_country": True})])
    assert kept[0]["claim"] == "Narrower claim."
    assert kept[0]["original_claim"] == "Claim t1"
    assert kept[0]["flags"]["single_country"] is True


def test_verifier_flagging_same_cohort_drops_thread(corpus, log):
    g = gate(corpus, log)
    kept = run(corpus, log, [thread([1001, 1002, 1003])])
    kept, _ = g.apply_verification(kept, [verdict("t1", [1001, 1002, 1003],
                                                  flags={"same_cohort_suspected": True})])
    assert kept == []
    assert any(r.startswith("same cohort suspected") for r in reasons(log))


# ---- cross-topic ------------------------------------------------------------------

def cross_thread(pmids, id="c1"):
    return thread(pmids, id=id, link_type="drug", link="metformin", topics=["alpha", "beta"])


def test_cross_topic_needs_two_sources_from_two_papers(corpus, log):
    scope = ["alpha", "beta", "senior-research"]
    kept = run(corpus, log, [cross_thread([2001, 2002, 2003])], source_id=None, in_scope=scope, cross=True)
    assert kept == []
    assert reasons(log) == ["evidence does not come from 2 different topics via 2 different studies"]


def test_cross_topic_passes_with_independent_sources(corpus, log):
    scope = ["alpha", "beta", "senior-research"]
    kept = run(corpus, log, [cross_thread([1001, 2001, 2002])], source_id=None, in_scope=scope, cross=True)
    out = gate(corpus, log, None, scope).finalize(kept[0])
    assert out["topics"] == ["alpha", "beta"]
    assert out["link_type"] == "drug"
    assert {s["pmid"]: s["sources"] for s in out["studies"]}["1001"] == ["alpha"]


def test_cross_topic_can_cite_context_source(corpus, log):
    scope = ["alpha", "beta", "senior-research"]
    kept = run(corpus, log, [cross_thread([4001, 2001, 2002])], source_id=None, in_scope=scope, cross=True)
    assert len(kept) == 1


def test_cross_topic_rejects_excluded_and_unknown(corpus, log):
    scope = ["alpha", "beta"]
    kept = run(corpus, log, [cross_thread([1012, 9999, 1001, 2001])], source_id=None, in_scope=scope, cross=True)
    assert kept == []
    by_pmid = {e["pmid"]: e["reason"] for e in log.entries if e.get("pmid")}
    assert by_pmid == {"1012": "cites an excluded study", "9999": "PMID does not exist in the loaded data"}
