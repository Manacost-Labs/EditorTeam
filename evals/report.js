#!/usr/bin/env node
// Краткий отчёт по JSON-выводу Promptfoo (`-o результат.json`): сравнение
// candidate без retrieval и с retrieval (или baseline и candidate) с итоговым
// verdict по порогам из evals/thresholds.json. Ничего не придумывает: если в
// файле нет результатов настоящей модели, печатает
// "real model evaluation not executed" и пороги не применяет.
//
//   node evals/report.js результат.json [--thresholds evals/thresholds.json] [--json]
//   exit 1, если verdict=fail.
const fs = require('node:fs');
const path = require('node:path');

const NO_RUN = 'real model evaluation not executed';
const DEFAULT_THRESHOLDS = path.join(__dirname, 'thresholds.json');
const FAKE_MODELS = new Set(['identity', 'fake', 'fake-editor', 'fake-model']);

function rate(part, total) {
  return total ? Number((part / total).toFixed(3)) : 0;
}

function words(text) {
  return String(text || '').toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
}

function sentencesOf(text) {
  return String(text || '')
    .split(/(?<=[.!?…])\s+|\n+/u)
    .map((item) => words(item).join(' '))
    .filter(Boolean);
}

// tokenEditDistance — расстояние Левенштейна по словам (вставка, удаление,
// замена по одному слову).
function tokenEditDistance(a, b) {
  const n = a.length;
  const m = b.length;
  if (!n) return m;
  if (!m) return n;
  let previous = Array.from({ length: m + 1 }, (_, index) => index);
  for (let i = 1; i <= n; i += 1) {
    const current = [i];
    for (let j = 1; j <= m; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      current[j] = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost);
    }
    previous = current;
  }
  return previous[m];
}

// longestIncreasing — длина неубывающей подпоследовательности индексов:
// столько предложений сохранили взаимный порядок.
function longestIncreasing(values) {
  const tails = [];
  for (const value of values) {
    let low = 0;
    let high = tails.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (tails[mid] < value) low = mid + 1; else high = mid;
    }
    tails[low] = value;
  }
  return tails.length;
}

// changeVolume — доля изменённого текста по token-level edit distance с
// учётом перестановок: перенесённые предложения считаются перемещением, а
// не удалением и вставкой; перестановка слов внутри предложения ловится
// расстоянием по отсортированным словам.
function changeVolume(source, output) {
  const a = words(source);
  const b = words(output);
  const size = Math.max(a.length, b.length);
  if (!size) return { edit_distance: 0, change_ratio: 0, moved_sentences: 0, reordered_words: 0 };
  const sourceSentences = sentencesOf(source);
  const outputSentences = sentencesOf(output);
  const positions = [];
  const outputIndex = new Map(outputSentences.map((sentence, index) => [sentence, index]));
  for (const sentence of sourceSentences) {
    if (outputIndex.has(sentence)) positions.push(outputIndex.get(sentence));
  }
  const movedSentences = positions.length - longestIncreasing(positions);
  const raw = tokenEditDistance(a, b);
  const sortedDistance = tokenEditDistance([...a].sort(), [...b].sort());
  const reorderedWords = Math.max(0, raw - sortedDistance);
  const moved = new Set(sourceSentences.filter((sentence) => outputIndex.has(sentence)));
  const stripped = (text) => sentencesOf(text).filter((sentence) => !moved.has(sentence)).join(' ');
  const distance = movedSentences > 0 ? tokenEditDistance(words(stripped(source)), words(stripped(output))) : raw;
  return {
    edit_distance: distance,
    change_ratio: Number((distance / size).toFixed(3)),
    moved_sentences: movedSentences,
    reordered_words: reorderedWords,
  };
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

function judgeScores(result) {
  const out = {};
  for (const [name, value] of Object.entries(result.namedScores || {})) {
    if (name.startsWith('judge-') && typeof value === 'number') out[name] = value;
  }
  return out;
}

function variantOf(result) {
  const metadata = result.response?.metadata || {};
  if (metadata.retrieval_variant) return metadata.retrieval_variant;
  const label = String(result.provider?.label || result.provider?.id || '');
  if (/без retrieval|no-retrieval/i.test(label)) return 'no-retrieval';
  if (/с retrieval|retrieval/i.test(label)) return 'retrieval';
  const prompt = String(result.prompt?.label || '');
  if (/baseline/i.test(prompt)) return 'baseline';
  if (/candidate/i.test(prompt)) return 'candidate';
  return label || 'unknown';
}

function isRealResult(result) {
  const metadata = result.response?.metadata || {};
  if (metadata.deterministic_only === true) return false;
  const provider = String(metadata.provider || '').toLowerCase();
  const model = String(metadata.model || '').toLowerCase();
  if (!provider || provider === 'offline' || !model) return false;
  return !FAKE_MODELS.has(model) && !model.startsWith('fake');
}

function isRealRun(results) {
  return results.length > 0 && results.every(isRealResult);
}

function candidateVariant(variants) {
  if (variants.includes('retrieval')) return 'retrieval';
  if (variants.includes('candidate')) return 'candidate';
  return variants[variants.length - 1];
}

function baselineVariant(variants) {
  if (variants.includes('no-retrieval')) return 'no-retrieval';
  if (variants.includes('baseline')) return 'baseline';
  return variants[0];
}

function summarize(results) {
  const groups = new Map();
  for (const result of results) {
    const variant = variantOf(result);
    if (!groups.has(variant)) groups.set(variant, []);
    groups.get(variant).push(result);
  }
  const variants = [...groups.keys()];
  const summary = { variants: {} };
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
    const average = (key) => Number((volumes.reduce((sum, value) => sum + value[key], 0) / (volumes.length || 1)).toFixed(3));
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
    let expectedEdit = 0;
    let editToUnchanged = 0;
    let expectedUnchanged = 0;
    let unchangedToEdit = 0;
    for (const item of items) {
      const expected = item.vars?.expected_action;
      const status = metadataOf(item).status;
      if (expected === 'edit') {
        expectedEdit += 1;
        if (status === 'unchanged') editToUnchanged += 1;
      } else if (expected === 'unchanged') {
        expectedUnchanged += 1;
        if (metadataOf(item).accepted === true && status === 'edited') unchangedToEdit += 1;
      }
    }
    const judgeTotals = {};
    for (const item of items) {
      for (const [name, value] of Object.entries(judgeScores(item))) {
        judgeTotals[name] = judgeTotals[name] || { sum: 0, count: 0 };
        judgeTotals[name].sum += value;
        judgeTotals[name].count += 1;
      }
    }
    const judgeAverages = {};
    for (const [name, total] of Object.entries(judgeTotals)) judgeAverages[name] = Number((total.sum / total.count).toFixed(3));
    summary.variants[variant] = {
      cases: items.length,
      accepted_rate: rate(accepted, items.length),
      rejected_rate: rate(rejected, items.length),
      unchanged_rate: rate(unchanged, items.length),
      checks_complete_rate: rate(checksComplete, items.length),
      corpus_copy_count: ruleCount('corpus_copy'),
      corpus_fact_leak_count: ruleCount('corpus_fact_leak'),
      facts_preserved_rate: passRate(['numbers-preserved', 'no-new-numbers', 'urls-preserved', 'no-new-urls', 'game-entities-preserved']),
      markdown_preserved_rate: passRate(['markdown-preserved', 'tables-intact', 'code-blocks-intact']),
      avg_change_ratio: average('change_ratio'),
      avg_edit_distance: average('edit_distance'),
      avg_moved_sentences: average('moved_sentences'),
      avg_reordered_words: average('reordered_words'),
      edit_to_unchanged: { count: editToUnchanged, expected_edit: expectedEdit },
      unchanged_to_edit: { count: unchangedToEdit, expected_unchanged: expectedUnchanged, false_edit_rate: rate(unchangedToEdit, expectedUnchanged) },
      judge_averages: judgeAverages,
      by_profile: byKey('profile'),
      by_game: byKey('game'),
    };
  }

  // Полнота A/B-пар: каждый кейс должен иметь ровно один результат на вариант.
  const candidate = candidateVariant(variants);
  const baseline = baselineVariant(variants);
  const paired = new Map();
  for (const result of results) {
    const id = result.vars?.id ?? `test-${result.testIdx}`;
    if (!paired.has(id)) paired.set(id, {});
    const bucket = paired.get(id);
    const variant = variantOf(result);
    bucket[variant] = bucket[variant] || [];
    bucket[variant].push(result);
  }
  const missing = [];
  const duplicates = [];
  let complete = 0;
  let wins = 0;
  let losses = 0;
  let ties = 0;
  for (const [id, bucket] of paired) {
    const a = bucket[candidate] || [];
    const b = bucket[baseline] || [];
    if (a.length > 1 || b.length > 1) duplicates.push(id);
    if (!a.length || !b.length || candidate === baseline) {
      missing.push(id);
      continue;
    }
    complete += 1;
    const acceptedA = a[0].response?.metadata?.accepted === true;
    const acceptedB = b[0].response?.metadata?.accepted === true;
    if (acceptedA !== acceptedB) {
      if (acceptedA) wins += 1; else losses += 1;
      continue;
    }
    const scoresA = Object.values(judgeScores(a[0]));
    const scoresB = Object.values(judgeScores(b[0]));
    const meanA = scoresA.length ? scoresA.reduce((sum, value) => sum + value, 0) / scoresA.length : null;
    const meanB = scoresB.length ? scoresB.reduce((sum, value) => sum + value, 0) / scoresB.length : null;
    if (meanA === null || meanB === null || meanA === meanB) ties += 1;
    else if (meanA > meanB) wins += 1;
    else losses += 1;
  }
  summary.pairs = {
    candidate, baseline, total: paired.size, complete, complete_rate: rate(complete, paired.size),
    missing, duplicates,
  };
  summary.candidate_win_rate = {
    pairs: complete, wins, losses, ties,
    win_rate: rate(wins, complete), loss_rate: rate(losses, complete),
  };
  return summary;
}

function evaluate(summary, thresholds) {
  const candidate = summary.variants[summary.pairs.candidate] || {};
  const checks = [
    ['facts_preserved_rate_min', candidate.facts_preserved_rate, (value, limit) => value >= limit],
    ['markdown_preserved_rate_min', candidate.markdown_preserved_rate, (value, limit) => value >= limit],
    ['corpus_copy_count_max', candidate.corpus_copy_count, (value, limit) => value <= limit],
    ['corpus_fact_leak_count_max', candidate.corpus_fact_leak_count, (value, limit) => value <= limit],
    ['complete_pairs_rate_min', summary.pairs.complete_rate, (value, limit) => value >= limit],
    ['candidate_win_rate_min', summary.candidate_win_rate.win_rate, (value, limit) => value >= limit],
    ['candidate_loss_rate_max', summary.candidate_win_rate.loss_rate, (value, limit) => value <= limit],
    ['false_edit_rate_max', candidate.unchanged_to_edit?.false_edit_rate, (value, limit) => value <= limit],
  ];
  const failures = [];
  for (const [name, value, ok] of checks) {
    if (!(name in thresholds)) continue;
    if (value === undefined || value === null || !ok(value, thresholds[name])) {
      failures.push({ threshold: name, limit: thresholds[name], actual: value ?? null });
    }
  }
  return { verdict: failures.length ? 'fail' : 'pass', failures };
}

function load(pathname) {
  const raw = JSON.parse(fs.readFileSync(pathname, 'utf8'));
  const results = raw?.results?.results || raw?.results || [];
  return Array.isArray(results) ? results : [];
}

function report(pathname, options = {}) {
  if (!pathname || !fs.existsSync(pathname)) return { status: NO_RUN };
  const results = load(pathname);
  if (!results.length || !isRealRun(results)) return { status: NO_RUN };
  const thresholds = options.thresholds || JSON.parse(fs.readFileSync(DEFAULT_THRESHOLDS, 'utf8'));
  const summary = summarize(results);
  return { status: 'ok', ...summary, thresholds: evaluate(summary, thresholds) };
}

function main(argv) {
  const args = argv.slice(2);
  const file = args.find((item) => !item.startsWith('--'));
  const thresholdsIndex = args.indexOf('--thresholds');
  const options = {};
  if (thresholdsIndex >= 0 && args[thresholdsIndex + 1]) {
    options.thresholds = JSON.parse(fs.readFileSync(args[thresholdsIndex + 1], 'utf8'));
  }
  const output = report(file, options);
  if (output.status === NO_RUN) {
    console.log(NO_RUN);
    return 0;
  }
  console.log(JSON.stringify(output, null, 2));
  return output.thresholds.verdict === 'pass' ? 0 : 1;
}

if (require.main === module) {
  process.exitCode = main(process.argv);
}

module.exports = { report, summarize, evaluate, changeVolume, tokenEditDistance, NO_RUN };
