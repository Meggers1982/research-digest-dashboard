# Research Digest Dashboard

One dashboard for multiple separate research digest repositories.

Each digest repo stays independent. After its workflow finishes, it publishes one JSON file into this dashboard repo under `data/`. The dashboard loads every source listed in `data/sources.json`, merges the studies, deduplicates by PMID, and shows one searchable, filterable interface.

https://research-digest-dashboard.vercel.app

## Structure

```text
index.html
top-picks.js             # score threshold + daily cap (pure function, see "Top picks")
patterns.html            # Evidence Patterns page (weekly job output)
api/
  status.js
data/
  sources.json
  patterns.json          # written by the weekly patterns job
  aging-longevity.json
  cardiology-heart.json
  conditions-body.json
  dermatology-skin.json
  elderly-geriatric.json
  senior-research.json
  fitness-exercise.json
  gut-digestive.json
  mental-health.json
  pediatric-health.json
  science-environment.json
  womens-health.json
topic_memory/            # one running memory per topic, <source_id>.md
scripts/
  patterns/              # weekly patterns job (Python)
tests/
  top-picks.test.mjs     # node tests/top-picks.test.mjs
  patterns/              # pytest suite for the patterns job
requirements.txt
.github/
  workflows/
    sync-senior-research.yml
    weekly-patterns.yml
```

## Data Shape

Each source file should look like this:

```json
{
  "source_id": "womens-health",
  "source_label": "Women's Health",
  "last_updated": "",
  "total_studies": 0,
  "studies": []
}
```

The dashboard also tolerates older source files shaped like:

```json
{
  "last_updated": "",
  "total_studies": 0,
  "studies": []
}
```

When a source does not include `source_id` or `source_label`, the dashboard fills them from `data/sources.json`.

## Top picks (score threshold + daily cap)

About 90 merged studies arrive a day (125 before cross-digest duplicates are
merged), so the "new" queue is trimmed by two controls in the filter bar (MEA-721):

| Control | Options | Default |
|---|---|---|
| Minimum score | Any score / 7+ / 8+ / 9+ | **7+** |
| Daily cap | No cap / 1 / 3 / 5 per digest per day | **3** |

Rules, all in `applyTopPicks()` in `top-picks.js`:

- **Acted-on studies are never hidden.** Anything saved, pitched or passed shows
  whatever its score and takes no cap slot. The threshold and cap only trim
  studies still marked new.
- **The cap is per digest per `run_date`.** Within each digest-and-day group, the
  top N new studies by `relevance_score` are kept. Ties go to the dashboard's
  default order (the merge order the relevance sort already uses).
- **A study merged from several digests counts once**, under its `source_id`.
  That is the digest whose copy the merge kept (highest score, then latest run
  date). The digest filter and the saved/pitched status key use the same
  `source_id`, so the cap and the digest filter always agree. The study's other
  digests don't spend a slot on it.
- **Top picks run last**, on whatever the search, digest, category, type, status
  and date filters leave. Searching "sleep" shows the best 3 sleep studies per
  digest per day.
- **Nothing is lost.** The summary line reads "Showing X of Y studies · Z new below
  the top-picks cut" with a **Show all** button that sets both controls to Any
  score / No cap. **Clear** puts them back to the defaults.
- **View-only.** No study is deleted or changed, and `data/*.json` is never touched.
- The two settings persist in `localStorage` (`research-digest-dashboard-top-picks`),
  and every read and write is wrapped in try/catch. They are not put in the URL,
  since no other filter is.

**Why 7+ and not 8+.** Over the 7 run dates ending 2026-09-14, 8+ with a cap of 3
leaves about 7 studies a day (2 on several days), and the cap barely does anything
because few digests reach 8. 7+ with a cap of 3 leaves about 17 a day
(32, 19, 15, 11, 11, 6, 25), and the cap does real work on heavy run days. Some
digests almost never score 8+:

| Digest | 8+ | 7+ |
|---|---|---|
| senior-research (1 run so far) | 14.8% | 48.1% |
| aging-longevity | 10.5% | 25.1% |
| mental-health | 10.4% | 29.1% |
| pediatric-health | 10.4% | 26.1% |
| science-environment | 10.3% | 28.5% |
| elderly-geriatric | 9.6% | 24.3% |
| womens-health | 7.0% | 21.2% |
| fitness-exercise | 6.9% | 21.7% |
| cardiology-heart | 6.1% | 22.3% |
| gut-digestive | 5.1% | 14.4% |
| conditions-body | 4.4% | 15.5% |
| dermatology-skin | 4.3% | 23.2% |

The defaults live in one place, `TOP_PICKS_DEFAULTS` in `top-picks.js`.

### Test

```bash
node tests/top-picks.test.mjs
```

No dependencies. It loads the real `data/*.json`, merges them the way `index.html`
does, and runs `applyTopPicks()` with the defaults. It checks that acted-on
studies survive, that no digest goes over the cap on any day, that the result
isn't empty, and that the daily queue over the last 7 run dates averages roughly
10–20 (the check allows 8–25, so a heavy week doesn't fail it). It prints the
per-day counts. It exits 1 on failure.

## Hosting

The dashboard is hosted on Vercel at https://research-digest-dashboard.vercel.app

The Vercel project builds this repo directly (root directory `.`, framework
"Other"), so every push to `main` redeploys the static site. No build step and no
workflow are involved.

GitHub Pages previously served a second copy of this dashboard at
`meggers1982.github.io/research-digest-dashboard`. It was retired in favor of the
Vercel deployment, and `.github/workflows/pages.yml` was removed along with it.

## Status Sync

Saved/pitched/passed labels on studies sync through a single shared Neon Postgres
database (Vercel Marketplace project `neon-green-book`), so labels are the same
on every device. `api/status.js` is a Vercel Function backed by
`@neondatabase/serverless`:

- `GET /api/status` &mdash; returns every `{study_id, status}` row.
- `POST /api/status` &mdash; upserts one record (or an array of records).

`DATABASE_URL` is provisioned automatically by the Neon integration and lives in
the Vercel project's environment variables (also pulled into `.env.local` for
local dev). The `study_status` table:

```sql
create table study_status (
  study_id   text primary key,
  status     text not null,
  updated_at timestamptz default now()
);
```

If the API is unreachable, the page falls back to `localStorage` for that
browser and retries the shared DB on the next load. This replaced an earlier
per-visitor "paste your own Supabase URL/key" setup &mdash; since this dashboard
has one real user, one shared database is simpler.

## Digest Repo Publishing

Each separate digest repo needs a token that can write to this dashboard repo.

Add this secret to each digest repo:

```text
DASHBOARD_REPO_TOKEN
```

Then add a step after the digest repo has produced or merged `data/results.json`.

```yaml
- name: Publish results to dashboard
  env:
    DASHBOARD_REPO_TOKEN: ${{ secrets.DASHBOARD_REPO_TOKEN }}
    DASHBOARD_REPO: Meggers1982/research-digest-dashboard
    DASHBOARD_FILE: data/womens-health.json
    SOURCE_ID: womens-health
    SOURCE_LABEL: Women's Health
  run: |
    git clone "https://x-access-token:${DASHBOARD_REPO_TOKEN}@github.com/${DASHBOARD_REPO}.git" /tmp/research-digest-dashboard
    python - <<'PY'
    import json
    import os
    from pathlib import Path

    source = json.loads(Path("data/results.json").read_text())
    if isinstance(source, list):
        source = {"last_updated": "", "total_studies": len(source), "studies": source}

    source["source_id"] = os.environ["SOURCE_ID"]
    source["source_label"] = os.environ["SOURCE_LABEL"]
    source["total_studies"] = len(source.get("studies", []))

    out = Path("/tmp/research-digest-dashboard") / os.environ["DASHBOARD_FILE"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(source, indent=2) + "\n")
    PY
    cd /tmp/research-digest-dashboard
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git add "$DASHBOARD_FILE"
    if git diff --cached --quiet; then
      echo "No dashboard changes"
    else
      git commit -m "Update ${SOURCE_LABEL} digest results"
      pushed=false
      for attempt in 1 2 3; do
        if git pull --rebase origin main && git push; then
          pushed=true
          break
        fi
        sleep $((attempt * 5))
      done
      if [ "$pushed" != "true" ]; then
        echo "Failed to publish dashboard update"
        exit 1
      fi
    fi
```

Change `DASHBOARD_FILE`, `SOURCE_ID`, and `SOURCE_LABEL` for each digest repo.

### Pulled sources

One source is pulled by a workflow here instead of pushed by its digest repo,
so that repo needs no token that can write to this one:

- `senior-research` — `.github/workflows/sync-senior-research.yml`, from
  `docs/data/shared-dashboard.json` in `senior-research-digest`. That repo's
  new-this-week run replaced `elderly-geriatric-digest` on 2026-09-13 (MEA-573).
  The sync refuses to shrink the file, since upstream is rebuilt from the whole
  archive and should only grow.

When `elderly-geriatric-digest` stops running, `data/elderly-geriatric.json` stays
as it is — its studies, and the saved/passed marks keyed `elderly-geriatric:<pmid>`,
keep showing. New studies arrive under `senior-research`.

### Retired sources

- `new-scientist-mh` ("New Scientist — Mental Health") was removed on
  2026-09-14. `new-scientist-story-ideas` keeps running and publishes only to
  its own dashboard, https://new-scientist-story-ideas.vercel.app. Its sync
  workflow and `data/new-scientist-mh.json` are in git history before that
  date; no saved or passed marks were keyed to it.

## Source Map

| Digest repo | Dashboard file | Source ID |
|---|---|---|
| `aging-longevity-digest` | `data/aging-longevity.json` | `aging-longevity` |
| `cardiology-heart-digest` | `data/cardiology-heart.json` | `cardiology-heart` |
| `conditions-body-digest` | `data/conditions-body.json` | `conditions-body` |
| `dermatology-skin-digest` | `data/dermatology-skin.json` | `dermatology-skin` |
| `elderly-geriatric-digest` (retiring, MEA-573) | `data/elderly-geriatric.json` | `elderly-geriatric` |
| `senior-research-digest` (pulled, not pushed) | `data/senior-research.json` | `senior-research` |
| `fitness-exercise-digest` | `data/fitness-exercise.json` | `fitness-exercise` |
| `gut-digestive-digest` | `data/gut-digestive.json` | `gut-digestive` |
| `mental-health-digest` | `data/mental-health.json` | `mental-health` |
| `pediatric-health-digest` | `data/pediatric-health.json` | `pediatric-health` |
| `science-environment-digest` | `data/science-environment.json` | `science-environment` |
| `womens-health-digest` | `data/womens-health.json` | `womens-health` |

## Weekly Evidence Patterns

The dashboard shows single studies. The patterns job connects them: once a
week it reads every topic's new studies, keeps a running memory per topic, and
finds evidence threads, meaning 3 or more studies from 2 or more journals that
point the same way, within a topic and across topics. `patterns.html` renders
the result, and the dashboard header links to it.

It follows the model in senior-research-digest (`scripts/trends.py`,
`scripts/llm.py`, `topic_memory/`), with memory keyed by `source_id` so a
renamed digest keeps its history.

### What runs

For each topic (every source except `pitch-ideas`, which is empty, and
`senior-research`, which keeps its own memory upstream):

1. **Topic pass** (`claude-opus-5`, effort `high`). Input: compact records of
   the week's studies (PMID, headline, journal, dates, score, trimmed summary,
   caveats) plus `topic_memory/<source_id>.md`. Output, as structured JSON: the
   revised memory and a list of threads. Each thread has a stable slug id, a
   title, a one-sentence claim, its PMIDs, a direction (new, confirms, extends
   or contradicts earlier findings), the design of each study, and flags
   (`single_country`, `single_group`, `same_cohort_suspected`). Studies of every
   score are included.
2. **Code guardrails** (below).
3. **Verification pass** (`claude-opus-5`, effort `low`, a much smaller input).
   It sees each surviving thread plus the full summary and caveats of every
   study it cites. It returns keep or drop for each PMID and each thread, can
   narrow an overstated claim, and can add flags. The guardrails then run again.

Then one **cross-topic pass** over every topic's memory and the week's verified
threads looks for the same exposure, drug, population or mechanism in more than
one topic. `senior-research`'s studies for the week are included as read-only
context and can be cited. Cross-topic threads get the same guardrails and their
own verification call.

A week is the 7 days ending `--week-ending` (default: the Sunday before the
run), matched on each study's `run_date`. A study that appears in several
sources is counted once per pass (deduped by PMID). Within a source, a repeat of
the same PMID keeps its earliest `run_date`, so a repeat never counts as new.

### Guardrails (enforced in code)

`scripts/patterns/guardrails.py`. Every drop is printed and logged in
`patterns.json` under `dropped`, with its stage and reason. The page lists them
at the bottom.

- Every cited PMID must exist in the loaded data and not be excluded. A topic
  thread may only cite that topic's own studies. A PMID marked `excluded` in
  any source counts as excluded everywhere.
- A thread needs at least 3 distinct PMIDs and 2 distinct journals (journal
  names are normalized, so "The Lancet (London, England)" and "Lancet" are one
  journal), and at least one study added this week.
- `same_cohort_suspected` drops the thread.
- A cross-topic thread also needs two different PMIDs carried by two different
  sources. One paper that appears in two digests is one piece of evidence.
- The verifier's PMID and thread drops are applied, then all of the above runs
  again. A thread or PMID the verifier skipped counts as dropped, and if the
  verification call fails, that scope's threads are not published.
- `single_country` and `single_group` never drop a thread. They show on it.
- Memory bullets follow the same PMID rules. An "established" bullet needs 3
  PMIDs from 2 journals, added in at least 2 different weeks; one that falls
  short is demoted to "emerging," not deleted, and a bullet can't be promoted
  in a week the verifier rejected its thread. A new bullet for a rejected thread
  is removed.

### Outputs

- `topic_memory/<source_id>.md`: "Established findings" and "Emerging
  threads," one bullet per finding with its slug id and PMIDs. The header line
  records the last week folded in. A re-run for a week that is already folded in
  skips that topic and carries its threads over, unless you pass `--force`.
- `data/patterns.json`: `{generated_at, week_start, week_end, model,
  verify_model, topics: {<source_id>: {label, status, study_count, threads}},
  cross_topic, dropped, usage}`. Each thread embeds its studies' headline,
  journal, dates, score, summary, caveats and DOI, so `patterns.html` loads this
  one file and never the source files.

Nothing is written until every pass has finished. A topic whose pass fails is
marked `failed` and its memory is left as it was.

### Bootstrap

A new topic has no memory, so its first week would be compared against
nothing. `--bootstrap-weeks N` builds the memory first, for topics that have
none, from the N weeks before the review week: **one call per topic**, with the
whole history in a single prompt, rather than one call per past week. That
keeps the one-time cost to 11 extra calls (one per topic) whatever N is. N is capped at
8; 4 weeks is the largest prompt at about 47K input tokens
(science-environment). Topics that already have memory ignore the flag, so the
workflow passes 4 on every run and only the first run pays for it.

### Cost

A normal week is at most 24 calls: 11 topic passes, up to 11 verifications,
and 1 cross-topic pass plus its verification (verification is skipped when no
thread survives the code checks). The first run adds up to 11 bootstrap calls,
so about 35 in total. The system prompts are prompt-cached across topics. Every
call streams and uses `fallbacks: "default"`, so a safety-classifier decline is
retried on Anthropic's recommended fallback model instead of failing the topic.

Measured on the smallest topic (dermatology-skin, 28 studies, 2-week
bootstrap), two runs: 3 calls each, 18–25K input and 16–22K output tokens
(mostly thinking), $0.51–$0.71. Scaled to every topic, expect roughly $5 a week and
about $8 for the first, bootstrapped run. The job prints its own call count,
tokens and estimated cost, and records them under `usage` in `patterns.json`.

### Running it

Prerequisite: an `ANTHROPIC_API_KEY` **repository secret**. It does not exist
in this repo yet, and the workflow fails with a clear error until it's added.

Locally (Python 3.11+):

```bash
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=...

# See what would be sent, with no calls and no writes:
python -m scripts.patterns.run --dry-run --bootstrap-weeks 4

# One topic into a scratch folder, leaving data/ and topic_memory/ alone:
python -m scripts.patterns.run --sources dermatology-skin --bootstrap-weeks 2 --out /tmp/patterns

# The real thing for a given week:
python -m scripts.patterns.run --week-ending 2026-09-13 --bootstrap-weeks 4

python -m pytest   # no network; uses a fake client
```

Other flags: `--force` re-runs a week already folded in, `--model`,
`--verify-model`, `--effort` and `--verify-effort` override the defaults, and
`--workers` sets how many topics run at once (default 3).

In GitHub Actions, `.github/workflows/weekly-patterns.yml` runs Mondays at
13:23 UTC and on demand (Actions → Weekly Evidence Patterns → Run workflow),
with inputs for `bootstrap_weeks` (default 4), `sources`, `week_ending` and
`force`. It commits `data/patterns.json` and `topic_memory/` as
github-actions[bot] with `git pull --rebase` and one retry, like the other
sync workflows.

## Local Preview

Static preview only (status labels fall back to `localStorage`, no `/api/status`):

```bash
python3 -m http.server 8123
```

Full preview including the status API:

```bash
vercel dev
```

Then open the printed `http://localhost:3000`.
