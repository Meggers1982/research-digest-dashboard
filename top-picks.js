// Top picks (MEA-721): the score threshold and per-digest daily cap for the
// "new" queue. View-only: it picks which studies to show and never changes them.
//
// Loaded by index.html as a plain <script> (defines window.applyTopPicks) and by
// tests/top-picks.test.mjs, which runs it against the real data/*.json files.
(function (root) {
  "use strict";

  // Shipped defaults. 7+ with 3 per digest per day gives about 17 studies a day
  // over the last week of real data; 8+ gives about 7 (see README "Top picks").
  var TOP_PICKS_DEFAULTS = { minScore: 7, capPerTopic: 3 };

  // Studies with one of these statuses have been acted on and are never hidden.
  var ACTED_ON = { saved: true, pitched: true, passed: true, archived: true };

  function scoreOf(study) {
    var score = Number(study.relevance_score);
    return Number.isNaN(score) ? 0 : score;
  }

  // A study merged from several digests counts once, under its source_id: the
  // digest whose copy the merge kept, which is also what the digest filter and
  // the status key use.
  function topicOf(study) {
    return String(study.source_id || "");
  }

  function dayOf(study) {
    return String(study.run_date || study.pubdate || "");
  }

  // studies:  array in the dashboard's default order (ties are broken by it)
  // options:  { minScore: 0 = any, capPerTopic: 0 = no cap,
  //             getStatus: study => "new" | "saved" | "pitched" | "passed" }
  // Returns a new array holding the kept studies in their original order.
  function applyTopPicks(studies, options) {
    var opts = options || {};
    var minScore = Number(opts.minScore) || 0;
    var cap = Math.max(0, Math.floor(Number(opts.capPerTopic) || 0));
    var getStatus = typeof opts.getStatus === "function"
      ? opts.getStatus
      : function (study) { return study.status || "new"; };

    var keep = new Array(studies.length);
    var groups = new Map();

    for (var i = 0; i < studies.length; i++) {
      var study = studies[i];
      if (ACTED_ON[getStatus(study)]) { keep[i] = true; continue; }
      var score = scoreOf(study);
      if (score < minScore) continue;
      if (!cap) { keep[i] = true; continue; }
      var key = topicOf(study) + "\u0000" + dayOf(study);
      var group = groups.get(key);
      if (!group) groups.set(key, group = []);
      group.push({ index: i, score: score });
    }

    groups.forEach(function (group) {
      group.sort(function (a, b) { return (b.score - a.score) || (a.index - b.index); });
      for (var j = 0; j < group.length && j < cap; j++) keep[group[j].index] = true;
    });

    return studies.filter(function (_, index) { return keep[index] === true; });
  }

  root.TOP_PICKS_DEFAULTS = TOP_PICKS_DEFAULTS;
  root.applyTopPicks = applyTopPicks;
})(typeof globalThis !== "undefined" ? globalThis : this);
