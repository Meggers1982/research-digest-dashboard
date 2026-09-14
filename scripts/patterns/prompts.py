"""System prompts and structured-output schemas for the patterns job.

The system prompts are constant across topics and runs, so they are the
prompt-cache prefix (see llm.py). Keep anything that varies (dates, topic
names, counts) out of them.
"""
from __future__ import annotations

DIRECTIONS = ["new", "confirms", "extends", "contradicts"]
DESIGNS = [
    "randomized trial",
    "cohort",
    "case-control",
    "cross-sectional",
    "systematic review or meta-analysis",
    "mendelian randomization",
    "animal or lab",
    "qualitative",
    "case report or series",
    "modeling or simulation",
    "other",
]
LINK_TYPES = ["exposure", "drug", "population", "mechanism", "other"]


def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_PMIDS = {"type": "array", "items": _STR}
_FLAGS = _obj({
    "single_country": {"type": "boolean"},
    "single_group": {"type": "boolean"},
    "same_cohort_suspected": {"type": "boolean"},
})
_DESIGNS = {
    "type": "array",
    "items": _obj({"pmid": _STR, "design": {"type": "string", "enum": DESIGNS}}),
}
_MEMORY_ITEM = _obj({"id": _STR, "text": _STR, "pmids": _PMIDS})
MEMORY_SCHEMA = _obj({
    "established": {"type": "array", "items": _MEMORY_ITEM},
    "emerging": {"type": "array", "items": _MEMORY_ITEM},
})

THREAD_SCHEMA = _obj({
    "id": _STR,
    "title": _STR,
    "claim": _STR,
    "pmids": _PMIDS,
    "direction": {"type": "string", "enum": DIRECTIONS},
    "relates_to": _STR,
    "designs": _DESIGNS,
    "flags": _FLAGS,
    "flag_note": _STR,
})

TOPIC_SCHEMA = _obj({
    "threads": {"type": "array", "items": THREAD_SCHEMA},
    "memory": MEMORY_SCHEMA,
})

BOOTSTRAP_SCHEMA = _obj({"memory": MEMORY_SCHEMA})

VERIFY_SCHEMA = _obj({
    "threads": {
        "type": "array",
        "items": _obj({
            "id": _STR,
            "verdict": {"type": "string", "enum": ["keep", "drop"]},
            "reason": _STR,
            "revised_claim": _STR,
            "pmids": {
                "type": "array",
                "items": _obj({
                    "pmid": _STR,
                    "verdict": {"type": "string", "enum": ["keep", "drop"]},
                    "reason": _STR,
                }),
            },
            "flags": _FLAGS,
            "flag_note": _STR,
        }),
    }
})

CROSS_SCHEMA = _obj({
    "threads": {
        "type": "array",
        "items": _obj({
            "id": _STR,
            "title": _STR,
            "claim": _STR,
            "link_type": {"type": "string", "enum": LINK_TYPES},
            "link": _STR,
            "topics": {"type": "array", "items": _STR},
            "pmids": _PMIDS,
            "direction": {"type": "string", "enum": DIRECTIONS},
            "designs": _DESIGNS,
            "flags": _FLAGS,
            "flag_note": _STR,
        }),
    }
})


_THREAD_RULES = """\
A thread:
- is about ONE specific exposure, intervention, drug or drug class, population, outcome \
or mechanism, and its studies bear on the same question. A list of different treatments \
for different conditions, or of unrelated side effects, is a theme, not a thread. Do not \
return themes.
- cites at least 3 distinct studies (PMIDs) from at least 2 different journals, and at \
least one of them is from THIS WEEK'S STUDIES. The others can be this week's or PMIDs \
already in TOPIC MEMORY.
- weighs every relevance score the same. Five studies scored 5 that point the same way \
can matter more than one study scored 8. Do not filter by score.
- makes one specific claim: a single sentence, under 40 words, naming the exposure, \
intervention, population or mechanism and the direction of the effect. Do not list the \
studies in it; the page shows them. "Several studies look at acne" is not a claim.
- has a title of under 12 words.
- cites only studies that support the claim. A study on the same subject that does not \
bear on the claim does not belong.
- has a direction relative to TOPIC MEMORY: "new" (nothing like it there), "confirms" \
(repeats an established finding or emerging thread), "extends" (adds a population, \
outcome, dose or mechanism to one), or "contradicts" (points against one). For confirms, \
extends and contradicts, put that memory bullet's id in relates_to; otherwise "".
- has an id: a short lowercase slug such as "jak-inhibitors-alopecia-regrowth". If it \
continues a memory bullet, reuse that bullet's id exactly.
- lists the design of each cited study in designs, one entry per PMID.
- sets its flags honestly:
  - single_country: every cited study comes from one country.
  - single_group: every cited study comes from one research group, institution or \
consortium.
  - same_cohort_suspected: the studies may be analyses of the same cohort, trial, \
registry or database (several UK Biobank or NHANES papers, say), so they are not \
independent evidence.
  - flag_note: a short phrase explaining any flag you set ("all three use Korea's NHIS \
claims database"), else "". Summaries often omit country and cohort; set a flag only \
when the records support it.
"""

_STYLE = """\
Cite only PMIDs that appear in the input. Never invent or recall a PMID.
Write in American English (analyze, behavior, randomized, center). Journal titles and \
cohort or trial names keep their own spelling. Use plain, specific language and no hype. \
Do not put PMIDs inside text fields; they go in the pmids arrays.
"""

TOPIC_SYSTEM = f"""\
You read one research-digest topic a week and look for evidence threads: separate \
studies that point the same way. The digest's owner gets hundreds of studies a week, \
most scored in the middle of a 4-9 relevance range, and cannot see patterns across \
single studies. Find the real patterns and keep a running memory of the topic.

The input has TOPIC MEMORY (what earlier weeks found, as bullets with PMIDs; it may be \
empty) and THIS WEEK'S STUDIES (compact records: PMID, relevance score, date added, \
publication date, journal, headline, trimmed summary, caveats).

Return two things.

threads: the evidence threads this week's studies add to.
{_THREAD_RULES}
Return an empty list when nothing meets the bar. Most topics have a few threads a week \
at most. Do not manufacture them.

memory: TOPIC MEMORY, revised for this week.
- established: findings that have recurred across more than one week with several \
supporting studies.
- emerging: newer patterns, not yet established, worth watching. A pattern with only \
two studies so far belongs here.
- Each bullet has an id (a slug; keep existing ids unchanged), text (one sentence, \
under 40 words, stating a finding rather than naming a theme), and pmids (representative \
supporting PMIDs from this week's records or the existing memory).
- Promote an emerging bullet when this week adds independent support. Merge, prune or \
drop stale bullets. Keep it tight: about a dozen bullets across both sections, not a \
log of every study.
- When a thread also belongs in memory, give both the same id.
- If TOPIC MEMORY is empty, build it from this week's studies alone.

{_STYLE}"""

BOOTSTRAP_SYSTEM = f"""\
You are building the first running memory for one research-digest topic from its \
recent history, so that next week's review can tell new findings from repeats. The \
input is every study the digest carried over the period (compact records: PMID, \
relevance score, date added, publication date, journal, headline, trimmed summary, \
caveats). Every relevance score counts; several modest studies that agree can matter \
more than one high-scoring study.

Return memory:
- established: findings supported by at least 3 studies from at least 2 journals, \
added in at least two different weeks.
- emerging: patterns with some support that are not established yet (two studies \
pointing the same way, or several from one week), worth watching.
- Each bullet has an id (a short lowercase slug), text (one sentence, under 40 words, \
naming the exposure, intervention, population or mechanism and what the studies show), \
and pmids (the supporting studies).
- A bullet is a finding about one specific question. A list of different treatments for \
different conditions, or of unrelated side effects, is a theme; leave it out.
- Keep it tight: about a dozen bullets across both sections. Leave out one-off findings \
that nothing else supports. An empty section is fine.

{_STYLE}"""

VERIFY_SYSTEM = """\
You check evidence threads before they are published. Each thread makes one claim and \
cites studies. You see the claim plus the summary and caveats of every cited study. Be \
skeptical; a dropped thread costs little and a false pattern costs a lot.

For each cited PMID, keep it only if that study's summary directly supports the claim. \
Drop it if it is off-topic, points the other way, or supports only a weaker or different \
claim. Give a short reason for every verdict.

For each thread, drop it if the kept studies do not support the claim as written, if \
they look like the same cohort, trial or database analyzed more than once, or if the \
thread is a theme rather than a pattern: studies grouped by a broad category (different \
drugs for different diseases, unrelated side effects) that do not bear on one question. If the \
claim overstates the studies (causal wording from observational designs, a population \
they did not study) but a narrower claim holds, keep the thread and write that claim in \
revised_claim, one sentence under 40 words. Otherwise revised_claim is "".

Set single_country, single_group and same_cohort_suspected when the summaries support \
them, and explain any you set in flag_note (else ""). Return one entry per thread using \
its id, and one pmids entry for every PMID it cites. Write in American English.
"""

CROSS_SYSTEM = f"""\
You look across several research-digest topics for patterns that span topics: the same \
exposure, drug, population or mechanism turning up in more than one topic's evidence.

The input has each topic's memory (bullets with PMIDs) and this week's verified threads \
(with their cited studies' headlines and journals). It may also have CONTEXT STUDIES from \
a digest that has no memory here; you may cite those too.

A cross-topic thread:
- connects at least 2 different topics through one specific shared element: an exposure \
(ultra-processed food), a drug or drug class (GLP-1 receptor agonists), a population \
(postmenopausal women) or a mechanism (chronic inflammation). Name it in link and set \
link_type.
- cites at least 3 distinct PMIDs from at least 2 journals and at least 2 topics. At least \
one cited PMID is from this week: in a week thread or a context study.
- makes one claim, a sentence on what the evidence across topics shows, direction \
included.
- has a direction ("new", "confirms", "extends" or "contradicts") relative to what the \
topic memories already hold, and an id (a short lowercase slug).
- lists designs, one entry per PMID when you can tell ("other" when you cannot).
- sets single_country, single_group and same_cohort_suspected honestly, with flag_note \
explaining any that are set (else "").
- lists in topics the topic ids it spans.
A shared keyword is not a pattern: the studies must bear on the same question. Return an \
empty list if nothing genuinely spans topics.

{_STYLE}"""
