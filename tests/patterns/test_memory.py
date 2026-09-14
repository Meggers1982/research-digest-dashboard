from scripts.patterns import guardrails, memory
from scripts.patterns.memory import MemoryItem, TopicMemory


def sample() -> TopicMemory:
    return TopicMemory(
        source_id="alpha",
        label="Alpha Topic — Renamed (v2)",
        established=[
            MemoryItem("statins-lower-risk", "Statins lower risk (in adults 65+), across designs", ["1001", "1002"]),
        ],
        emerging=[
            MemoryItem("sleep-and-mood", "Short sleep tracks with worse mood; dose unclear", ["1003"], "2026-08-20"),
            MemoryItem("no-date", "Undated emerging bullet", ["1004", "1005"]),
        ],
        last_updated="2026-09-14",
        covered_through="2026-09-13",
    )


def test_render_parse_round_trip():
    mem = sample()
    text = mem.render()
    back = memory.parse(text, "alpha", mem.label)
    assert back.render() == text
    assert back.covered_through == "2026-09-13"
    assert back.last_updated == "2026-09-14"
    assert [(i.id, i.text, i.pmids) for i in back.established] == [
        ("statins-lower-risk", "Statins lower risk (in adults 65+), across designs", ["1001", "1002"])]
    assert back.emerging[0].first_seen == "2026-08-20"
    assert back.emerging[1].first_seen == ""


def test_rendered_file_matches_senior_research_style():
    text = sample().render()
    assert text.startswith("# Topic Memory: Alpha Topic")
    assert "\n## Established findings\n- **statins-lower-risk** — " in text
    assert "\n## Emerging threads\n" in text
    assert "(PMIDs 1003; first seen 2026-08-20)" in text


def test_empty_sections_round_trip():
    mem = TopicMemory(source_id="beta", label="Beta", last_updated="2026-09-14", covered_through="2026-09-13")
    text = mem.render()
    assert "_None yet._" in text
    back = memory.parse(text, "beta", "Beta")
    assert back.established == [] and back.emerging == []
    assert back.render() == text


def test_save_and_load_keyed_by_source_id(tmp_path):
    mem = sample()
    path = memory.save(tmp_path, mem)
    assert path.name == "alpha.md"  # never the display label
    loaded = memory.load(tmp_path, "alpha", "Any Label")
    assert loaded.render().split("\n", 1)[1] == mem.render().split("\n", 1)[1]
    assert memory.load(tmp_path, "missing", "x") is None


def test_from_model_cleans_text_and_ids():
    mem = memory.from_model({
        "established": [{"id": "Statins & Risk!", "text": "Line one\nline two (PMIDs 1, 2)", "pmids": ["PMID 1001", 1002]}],
        "emerging": [{"id": "dup", "text": "a", "pmids": ["1003"]}, {"id": "dup", "text": "b", "pmids": ["1004"]}],
    }, source_id="alpha", label="Alpha")
    assert mem.established[0].id == "statins-risk"
    assert mem.established[0].text == "Line one line two"
    assert mem.established[0].pmids == ["1001", "1002"]
    assert [i.id for i in mem.emerging] == ["dup", "dup-2"]


def test_check_memory_enforces_pmid_rules(corpus, log):
    prior = TopicMemory("alpha", "Alpha", emerging=[MemoryItem("kept-old", "old", ["1001"], "2026-08-01")])
    mem = TopicMemory("alpha", "Alpha",
                      established=[MemoryItem("mixed", "text", ["1001", "9999", "1006", "2001", "1005", "1003"])],
                      emerging=[
                          MemoryItem("kept-old", "still here", ["1001", "1002"]),
                          MemoryItem("rejected-new", "verifier said no", ["1002"]),
                          MemoryItem("empty", "nothing valid", ["9999"]),
                          MemoryItem("fresh", "new bullet", ["1005", "1001"]),
                      ])
    guardrails.check_memory(mem, corpus, "alpha", log, prior=prior,
                            rejected_ids={"rejected-new", "kept-old"},
                            verifier_dropped={"kept-old": ["1002"]})
    assert mem.established[0].pmids == ["1001", "1005", "1003"]
    ids = [i.id for i in mem.emerging]
    # A rejected id that was already in memory is kept; a new one is removed.
    assert ids == ["kept-old", "fresh"]
    assert mem.emerging[0].pmids == ["1001"]  # verifier-rejected PMID stripped
    assert mem.emerging[0].first_seen == "2026-08-01"  # from the prior memory
    assert mem.emerging[1].first_seen == "2026-08-20"  # earliest run_date of its studies
    reasons = {(e["thread_id"], e.get("pmid")): e["reason"] for e in log.entries}
    assert reasons[("mixed", "9999")] == "PMID does not exist in this topic's data"
    assert reasons[("mixed", "1006")] == "cites an excluded study"
    assert reasons[("mixed", "2001")] == "PMID does not exist in this topic's data"
    assert reasons[("empty", None)] == "no valid PMIDs left"
    assert reasons[("rejected-new", None)] == "new bullet for a thread the verifier rejected"


def test_established_needs_two_weeks_three_pmids_two_journals(corpus, log):
    mem = TopicMemory("alpha", "Alpha", established=[
        MemoryItem("solid", "two weeks", ["1001", "1003", "1005"]),
        MemoryItem("one-week", "all this week", ["1001", "1002", "1003"]),
        MemoryItem("one-journal", "same journal", ["1001", "1004", "1009"]),
        MemoryItem("too-few", "two studies", ["1001", "1005"]),
    ], emerging=[MemoryItem("already", "emerging", ["1002"])])
    guardrails.check_memory(mem, corpus, "alpha", log)
    assert [i.id for i in mem.established] == ["solid"]
    # Demoted, not deleted, and ahead of the existing emerging bullets.
    assert [i.id for i in mem.emerging] == ["one-week", "one-journal", "too-few", "already"]
    assert all(i.first_seen for i in mem.emerging)
    reasons = {e["thread_id"]: e["reason"] for e in log.entries}
    assert reasons == {
        "one-week": "demoted to emerging: all its studies were added in the same week",
        "one-journal": "demoted to emerging: only 1 journal(s) (needs 2)",
        "too-few": "demoted to emerging: only 2 PMIDs (needs 3)",
    }


def test_rejected_thread_blocks_promotion_but_not_existing_established(corpus, log):
    prior = TopicMemory("alpha", "Alpha",
                        established=[MemoryItem("was-established", "old", ["1001", "1003", "1005"])],
                        emerging=[MemoryItem("promoted", "was emerging", ["1001", "1005"])])
    mem = TopicMemory("alpha", "Alpha", established=[
        MemoryItem("was-established", "still", ["1001", "1003", "1005"]),
        MemoryItem("promoted", "now established", ["1001", "1003", "1005"]),
    ])
    guardrails.check_memory(mem, corpus, "alpha", log, prior=prior,
                            rejected_ids={"was-established", "promoted"})
    assert [i.id for i in mem.established] == ["was-established"]
    assert [i.id for i in mem.emerging] == ["promoted"]
    assert log.entries[0]["reason"] == "demoted to emerging: the verifier rejected this week's matching thread"
