// Tests for top-picks.js against the real data/*.json files.
// Run: node tests/top-picks.test.mjs   (no dependencies; exits 1 on failure)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// Load top-picks.js the way the browser does: as a plain script.
const sandbox = {};
vm.runInNewContext(readFileSync(join(ROOT, "top-picks.js"), "utf8"), sandbox);
const { applyTopPicks, TOP_PICKS_DEFAULTS } = sandbox;
const DEFAULTS = { minScore: TOP_PICKS_DEFAULTS.minScore, capPerTopic: TOP_PICKS_DEFAULTS.capPerTopic };

// ── Load and merge the real data, mirroring index.html init() + dedupeStudies() ──

function loadStudies() {
  const sources = JSON.parse(readFileSync(join(ROOT, "data/sources.json"), "utf8"));
  const raw = sources.flatMap(source => {
    const payload = JSON.parse(readFileSync(join(ROOT, source.file), "utf8"));
    const studies = (Array.isArray(payload) ? payload : (payload.studies || [])).filter(s => !s.excluded);
    const sourceId = payload.source_id || source.id;
    const sourceLabel = payload.source_label || source.label;
    return studies.map(s => ({ ...s, source_id: s.source_id || sourceId, source_label: s.source_label || sourceLabel }));
  });
  const byKey = new Map();
  for (const study of raw) {
    const key = study.pmid || study.doi || [study.headline, study.journal, study.pubdate].join("|");
    const existing = byKey.get(key);
    if (!existing) { byKey.set(key, { ...study, _source_labels: [study.source_label] }); continue; }
    const labels = existing._source_labels;
    if (!labels.includes(study.source_label)) labels.push(study.source_label);
    const a = Number(study.relevance_score || 0), b = Number(existing.relevance_score || 0);
    const better = a !== b ? a > b
      : String(study.run_date || study.pubdate || "") > String(existing.run_date || existing.pubdate || "");
    byKey.set(key, { ...(better ? study : existing), _source_labels: labels });
  }
  return Array.from(byKey.values());
}

const statusId = s => [s.source_id || "source", s.pmid || s.headline || ""].join(":");
const day = s => s.run_date || s.pubdate || "";

// ── Tiny harness ──

let failures = 0;
function check(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (err) { failures++; console.log("  FAIL " + name + "\n       " + err.message); }
}

const studies = loadStudies();
const snapshot = studies.map(statusId).join("\n");

// Mark a spread of low-score and over-cap studies as acted on.
const statuses = new Map();
const lowScore = studies.filter(s => Number(s.relevance_score) <= 5).slice(0, 6);
const overCapGroup = Object.values(Object.groupBy(
  studies.filter(s => Number(s.relevance_score) >= DEFAULTS.minScore),
  s => s.source_id + "|" + day(s)
)).find(g => g.length > DEFAULTS.capPerTopic + 2);
const actedOn = [...lowScore, ...(overCapGroup || []).slice(-2)];
["saved", "pitched", "passed"].forEach((status, i) =>
  actedOn.filter((_, j) => j % 3 === i).forEach(s => statuses.set(statusId(s), status)));
const getStatus = s => statuses.get(statusId(s)) || "new";

const picked = applyTopPicks(studies, { ...DEFAULTS, getStatus });
const pickedIds = new Set(picked.map(statusId));

console.log(`\nTop picks, defaults ${DEFAULTS.minScore}+ / max ${DEFAULTS.capPerTopic} per digest per day`);
console.log(`${studies.length} merged studies in, ${picked.length} out (${actedOn.length} marked acted-on)\n`);

check("result is non-empty", () => assert.ok(picked.length > 0));

check("acted-on studies survive regardless of score or cap", () => {
  assert.ok(actedOn.length >= 6, "expected at least 6 acted-on fixtures");
  for (const s of actedOn) assert.ok(pickedIds.has(statusId(s)), "missing acted-on " + statusId(s));
});

check("no digest exceeds the cap on any run_date", () => {
  const counts = new Map();
  for (const s of picked) {
    if (getStatus(s) !== "new") continue;
    const key = s.source_id + "|" + day(s);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  for (const [key, n] of counts) assert.ok(n <= DEFAULTS.capPerTopic, `${key} has ${n}`);
});

check("new studies below the minimum score are hidden", () => {
  for (const s of picked) {
    if (getStatus(s) === "new") assert.ok(Number(s.relevance_score) >= DEFAULTS.minScore, statusId(s));
  }
});

check("show all (any score, no cap) returns every study", () => {
  assert.equal(applyTopPicks(studies, { minScore: 0, capPerTopic: 0, getStatus }).length, studies.length);
});

check("input array is not modified and output keeps input order", () => {
  assert.equal(studies.map(statusId).join("\n"), snapshot);
  const positions = picked.map(s => studies.indexOf(s));
  assert.ok(positions.every((p, i) => p >= 0 && (i === 0 || p > positions[i - 1])));
});

check("cap ranks by score, ties go to the earlier study; acted-on studies take no slot", () => {
  const mk = (id, score, status = "new") => ({ pmid: id, source_id: "a", run_date: "2026-09-01", relevance_score: score, status });
  const out = applyTopPicks([mk("1", 7), mk("2", 9), mk("3", 8), mk("4", 8), mk("5", 4, "saved")], { minScore: 0, capPerTopic: 2 });
  assert.equal(out.map(s => s.pmid).join(","), "2,3,5");
  const days = applyTopPicks([mk("1", 8), { ...mk("2", 8), run_date: "2026-09-02" }, { ...mk("3", 8), source_id: "b" }], { minScore: 8, capPerTopic: 1 });
  assert.equal(days.length, 3, "cap is per digest per run_date");
});

// ── Daily default queue over the last 7 days of data ──

const latest = studies.map(day).filter(Boolean).sort().at(-1);
const last7 = Array.from({ length: 7 }, (_, i) => {
  const d = new Date(latest + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() - (6 - i));
  return d.toISOString().slice(0, 10);
});
const queue = applyTopPicks(studies, DEFAULTS); // nothing acted on: the pure "new" queue
const perDay = last7.map(d => ({
  day: d,
  incoming: studies.filter(s => day(s) === d).length,
  shown: queue.filter(s => day(s) === d).length
}));
const avg = perDay.reduce((sum, r) => sum + r.shown, 0) / perDay.length;

console.log("\n  run_date     incoming  shown");
for (const r of perDay) console.log(`  ${r.day}  ${String(r.incoming).padStart(8)}  ${String(r.shown).padStart(5)}`);
console.log(`  average      ${(perDay.reduce((s, r) => s + r.incoming, 0) / 7).toFixed(1).padStart(8)}  ${avg.toFixed(1).padStart(5)}\n`);

check("default daily queue averages roughly 10-20 over the last 7 days", () => {
  assert.ok(avg >= 8 && avg <= 25, `average ${avg.toFixed(1)} is outside the rough 10-20 band (8-25 allowed)`);
});

console.log(failures ? `\n${failures} check(s) failed` : "\nAll checks passed");
process.exit(failures ? 1 : 0);
