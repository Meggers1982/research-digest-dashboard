# Research Digest Dashboard

One dashboard for multiple separate research digest repositories.

Each digest repo stays independent. After its workflow finishes, it publishes one JSON file into this dashboard repo under `data/`. The dashboard loads every source listed in `data/sources.json`, merges the studies, deduplicates by PMID, and shows one searchable, filterable interface.

https://research-digest-dashboard.vercel.app

## Structure

```text
index.html
api/
  status.js
data/
  sources.json
  aging-longevity.json
  cardiology-heart.json
  conditions-body.json
  dermatology-skin.json
  elderly-geriatric.json
  fitness-exercise.json
  gut-digestive.json
  mental-health.json
  pediatric-health.json
  science-environment.json
  womens-health.json
.github/
  workflows/
    sync-new-scientist.yml
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

## Source Map

| Digest repo | Dashboard file | Source ID |
|---|---|---|
| `aging-longevity-digest` | `data/aging-longevity.json` | `aging-longevity` |
| `cardiology-heart-digest` | `data/cardiology-heart.json` | `cardiology-heart` |
| `conditions-body-digest` | `data/conditions-body.json` | `conditions-body` |
| `dermatology-skin-digest` | `data/dermatology-skin.json` | `dermatology-skin` |
| `elderly-geriatric-digest` | `data/elderly-geriatric.json` | `elderly-geriatric` |
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
