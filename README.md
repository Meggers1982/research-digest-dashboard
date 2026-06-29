# Research Digest Dashboard

One GitHub Pages dashboard for multiple separate research digest repositories.

Each digest repo stays independent. After its workflow finishes, it publishes one JSON file into this dashboard repo under `data/`. The dashboard loads every source listed in `data/sources.json`, merges the studies, deduplicates by PMID, and shows one searchable, filterable interface.

https://meggers1982.github.io/research-digest-dashboard/

## Structure

```text
index.html
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
    pages.yml
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

## GitHub Pages

1. Create a GitHub repo for this folder.
2. Push the repo to GitHub.
3. Go to **Settings -> Pages**.
4. Set source to **GitHub Actions**.
5. Push to `main`; `.github/workflows/pages.yml` deploys the static site.

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

From this directory:

```bash
python3 -m http.server 8123
```

Then open `http://localhost:8123`.
