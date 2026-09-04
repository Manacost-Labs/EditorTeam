#!/usr/bin/env node
// Краткий отчёт по JSON-выводу Promptfoo (`-o результат.json`): сравнение
// candidate без retrieval и с retrieval. Ничего не придумывает: если в файле
// нет результатов настоящей модели, печатает "real model evaluation not executed".
const fs = require('node:fs');

const NO_RUN = 'real model evaluation not executed';

function rate(part, total) {
  return total ? Number((part / total).toFixed(3)) : 0;
}

function words(text) {
  return String(text || '').toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
}

// changeVolume — доля слов, отличающихся между исходником и результатом.
function changeVolume(source, output) {
  const a = words(source);
  const b = words(output);
  if (!a.length && !b.length) return 0;
  const counts = new Map();
  for (const word of a) counts.set(word, (counts.get(word) || 0) + 1);
  let shared = 0;
  for (const word of b) {
    const left = counts.get(word) || 0;
    if (left > 0) {
      shared += 1;
      counts.set(word, left - 1);
    }
  }
  return Number((1 - shared / Math.max(a.length, b.length)).toFixed(3));
}

function componentPassed(result, name) {
  const components = result.gradingResult?.componentResults || [];
  for (const component of components) {
    const inner = component.componentResults || [component];
    for (const item of inner) {
      if (String(item.reason || '').startsWith(`${name}:`)) return item.pass === true;
    }
  }
  return null;
}

function judgeScore(result) {
  const scores = result.namedScores || {};
  let total = 0;
  let count = 0;
  for (const [name, value] of Object.entries(scores)) {
    if (name.startsWith('judge-') && typeof value === 'number') {
      total += value;
      count += 1;
    }
  }
  return count ? total / count : null;
}

function variantOf(result) {
  const metadata = result.response?.metadata || {};
  if (metadata.retrieval_variant) return metadata.retrieval_variant;
  const label = result.provider?.label || result.provider?.id || '';
  return /без retrieval|no-retrieval/i.test(label) ? 'no-retrieval' : 'retrieval';
}

function isRealRun(results) {
  return results.some((result) => {
    const metadata = result.response?.metadata || {};
    return metadata.deterministic_only !== true && metadata.provider && metadata.provider !== 'offline' && metadata.model && metadata.model !== 'identity';
  });
}

function summarize(results) {
  const groups = new Map();
  for (const result of results) {
    const variant = variantOf(result);
    if (!groups.has(variant)) groups.set(variant, []);
    groups.get(variant).push(result);
  }
  const summary = {};
  for (const [variant, items] of groups) {
    const metadataOf = (item) => item.response?.metadata || {};
    const accepted = items.filter((item) => metadataOf(item).accepted === true).length;
    const unchanged = items.filter((item) => metadataOf(item).status === 'unchanged').length;
    const rejected = items.filter((item) => metadataOf(item).accepted !== true && metadataOf(item).status !== 'unchanged').length;
    const checksComplete = items.filter((item) => metadataOf(item).checks_complete === true).length;
    const ruleCount = (rule) => items.reduce((sum, item) => sum + (metadataOf(item).qa_rule_ids || []).filter((id) => id === rule).length, 0);
    const passRate = (names) => {
      let pass = 0;
      let total = 0;
      for (const item of items) {
        const verdicts = names.map((name) => componentPassed(item, name)).filter((value) => value !== null);
        if (!verdicts.length) continue;
        total += 1;
        if (verdicts.every(Boolean)) pass += 1;
      }
      return rate(pass, total);
    };
    const volumes = items.map((item) => changeVolume(item.vars?.text, item.response?.output));
    const byKey = (key) => {
      const out = {};
      for (const item of items) {
        const value = item.vars?.[key] || 'unknown';
        out[value] = out[value] || { cases: 0, accepted: 0 };
        out[value].cases += 1;
        if (metadataOf(item).accepted === true) out[value].accepted += 1;
      }
      for (const value of Object.values(out)) value.accepted_rate = rate(value.accepted, value.cases);
      return out;
    };
    summary[variant] = {
      cases: items.length,
      accepted_rate: rate(accepted, items.length),
      rejected_rate: rate(rejected, items.length),
      unchanged_rate: rate(unchanged, items.length),
      checks_complete_rate: rate(checksComplete, items.length),
      corpus_copy_count: ruleCount('corpus_copy'),
      corpus_fact_leak_count: ruleCount('corpus_fact_leak'),
      facts_preserved_rate: passRate(['numbers-preserved', 'no-new-numbers', 'urls-preserved', 'no-new-urls', 'game-entities-preserved']),
      markdown_preserved_rate: passRate(['markdown-preserved', 'tables-intact', 'code-blocks-intact']),
      avg_change_volume: Number((volumes.reduce((sum, value) => sum + value, 0) / (volumes.length || 1)).toFixed(3)),
      by_profile: byKey('profile'),
      by_game: byKey('game'),
    };
  }
  // candidate win rate: по кейсу retrieval побеждает no-retrieval, если он принят,
  // а соперник нет, или оба приняты и средний judge-балл выше.
  const paired = new Map();
  for (const result of results) {
    const id = result.vars?.id || result.testIdx;
    if (!paired.has(id)) paired.set(id, {});
    paired.get(id)[variantOf(result)] = result;
  }
  let wins = 0;
  let losses = 0;
  let ties = 0;
  for (const pair of paired.values()) {
    const a = pair.retrieval;
    const b = pair['no-retrieval'];
    if (!a || !b) continue;
    const acceptedA = a.response?.metadata?.accepted === true;
    const acceptedB = b.response?.metadata?.accepted === true;
    if (acceptedA !== acceptedB) {
      if (acceptedA) wins += 1; else losses += 1;
      continue;
    }
    const scoreA = judgeScore(a);
    const scoreB = judgeScore(b);
    if (scoreA === null || scoreB === null || scoreA === scoreB) ties += 1;
    else if (scoreA > scoreB) wins += 1;
    else losses += 1;
  }
  summary.candidate_win_rate = { pairs: wins + losses + ties, wins, losses, ties, win_rate: rate(wins, wins + losses + ties) };
  return summary;
}

function load(path) {
  const raw = JSON.parse(fs.readFileSync(path, 'utf8'));
  const results = raw?.results?.results || raw?.results || [];
  return Array.isArray(results) ? results : [];
}

function report(path) {
  if (!path || !fs.existsSync(path)) return { status: NO_RUN };
  const results = load(path);
  if (!results.length || !isRealRun(results)) return { status: NO_RUN };
  return { status: 'ok', ...summarize(results) };
}

if (require.main === module) {
  const output = report(process.argv[2]);
  if (output.status === NO_RUN) {
    console.log(NO_RUN);
  } else {
    console.log(JSON.stringify(output, null, 2));
  }
}

module.exports = { report, summarize, changeVolume, NO_RUN };
