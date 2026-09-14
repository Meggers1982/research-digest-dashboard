# Research Digest Dashboard

One dashboard for multiple separate research digest repositories.

Each digest repo stays independent. After its workflow finishes, it publishes one JSON file into this dashboard repo under `data/`. The dashboard loads every source listed in `data/sources.json`, merges the studies, deduplicates by PMID, and shows one searchable, filterable interface.

https://research-digest-dashboard.vercel.app

## Structure

```text
index.html
top-picks.js          # score threshold + daily cap (pure function, see "Top picks")
tests/
  top-picks.test.mjs
api/
  status.js
data/
  sources.json
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
.github/
  workflows/
    sync-senior-research.yml
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
