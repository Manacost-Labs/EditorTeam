#!/usr/bin/env node
// Слепая человеческая оценка настоящего A/B-прогона.
//
//   node evals/blind_review.js prepare результат.json --out build/blind
//     → pairs.json и pairs.md с парами A/B без названий вариантов,
//       key.json с ключом рандомизации (хранить отдельно от оценщика).
//   node evals/blind_review.js import build/blind/ratings.json --key build/blind/key.json --out build/blind
//     → report.json и report.md: средние оценки по вариантам и предпочтения.
//
// Формат ratings.json:
//   [{"id":"corpus-05","A":{"readability":4,"naturalness":5,"usefulness":4,"voice":5},
//     "B":{...},"preferred":"A|B|tie","comment":""}]
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const CRITERIA = ['readability', 'naturalness', 'usefulness', 'voice'];
const CRITERIA_RU = {
  readability: 'читаемость', naturalness: 'естественность русского', usefulness: 'полезность для игрока', voice: 'сохранение авторского голоса',
};

function loadResults(file) {
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  const results = raw?.results?.results || raw?.results || [];
  return Array.isArray(results) ? results : [];
}

function variantOf(result) {
  const metadata = result.response?.metadata || {};
  if (metadata.retrieval_variant) return metadata.retrieval_variant;
  const label = String(result.provider?.label || '');
  if (/без retrieval|no-retrieval/i.test(label)) return 'no-retrieval';
  if (/retrieval/i.test(label)) return 'retrieval';
  return String(result.prompt?.label || label || 'unknown');
}

function pairUp(results) {
  const byCase = new Map();
  for (const result of results) {
    const id = result.vars?.id ?? `test-${result.testIdx}`;
    if (!byCase.has(id)) byCase.set(id, {});
    byCase.get(id)[variantOf(result)] = result;
  }
  const pairs = [];
  for (const [id, variants] of byCase) {
    const names = Object.keys(variants);
    if (names.length !== 2) continue;
    pairs.push({ id, source: String(variants[names[0]].vars?.text || ''), variants: names, outputs: names.map((name) => String(variants[name].response?.output || '')) });
  }
  return pairs;
}

// randomBit использует криптографический источник: оценщик не должен
// угадывать сторону по порядку кейсов.
function randomBit() {
  return crypto.randomBytes(1)[0] % 2;
}

function prepare(file, outDir, random = randomBit) {
  fs.mkdirSync(outDir, { recursive: true });
  const pairs = pairUp(loadResults(file));
  const blind = [];
  const key = [];
  for (const pair of pairs) {
    const flip = random() === 1;
    const [first, second] = flip ? [1, 0] : [0, 1];
    blind.push({ id: pair.id, source: pair.source, A: pair.outputs[first], B: pair.outputs[second], criteria: CRITERIA });
    key.push({ id: pair.id, A: pair.variants[first], B: pair.variants[second] });
  }
  fs.writeFileSync(path.join(outDir, 'pairs.json'), JSON.stringify(blind, null, 2) + '\n');
  fs.writeFileSync(path.join(outDir, 'key.json'), JSON.stringify({ created_at: new Date().toISOString(), source_file: path.basename(file), pairs: key }, null, 2) + '\n');
  const lines = ['# Слепая оценка редактуры', '', 'Оцените каждую пару по шкале 1–5 и укажите, какой вариант предпочли (A, B или tie). Названия вариантов скрыты; ключ хранится отдельно.', ''];
  for (const item of blind) {
    lines.push(`## ${item.id}`, '', '### Исходник', '', item.source, '', '### Вариант A', '', item.A, '', '### Вариант B', '', item.B, '');
    lines.push('| Критерий | A (1–5) | B (1–5) |', '| --- | --- | --- |');
    for (const criterion of CRITERIA) lines.push(`| ${CRITERIA_RU[criterion]} | | |`);
    lines.push('', 'Предпочтение: A / B / tie', '');
  }
  fs.writeFileSync(path.join(outDir, 'pairs.md'), lines.join('\n'));
  const template = blind.map((item) => ({ id: item.id, A: Object.fromEntries(CRITERIA.map((c) => [c, null])), B: Object.fromEntries(CRITERIA.map((c) => [c, null])), preferred: null, comment: '' }));
  fs.writeFileSync(path.join(outDir, 'ratings.template.json'), JSON.stringify(template, null, 2) + '\n');
  return { pairs: blind.length, out: outDir };
}

function importRatings(ratingsFile, keyFile, outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  const ratings = JSON.parse(fs.readFileSync(ratingsFile, 'utf8'));
  const key = JSON.parse(fs.readFileSync(keyFile, 'utf8'));
  const sides = new Map(key.pairs.map((item) => [item.id, item]));
  const totals = {};
  const preferences = {};
  let rated = 0;
  const skipped = [];
  for (const rating of ratings) {
    const side = sides.get(rating.id);
    if (!side) {
      skipped.push(rating.id);
      continue;
    }
    rated += 1;
    for (const letter of ['A', 'B']) {
      const variant = side[letter];
      totals[variant] = totals[variant] || Object.fromEntries(CRITERIA.map((c) => [c, { sum: 0, count: 0 }]));
      for (const criterion of CRITERIA) {
        const value = Number(rating[letter]?.[criterion]);
        if (Number.isFinite(value) && value >= 1 && value <= 5) {
          totals[variant][criterion].sum += value;
          totals[variant][criterion].count += 1;
        }
      }
    }
    const preferred = String(rating.preferred || 'tie').toUpperCase();
    const winner = preferred === 'A' || preferred === 'B' ? side[preferred] : 'tie';
    preferences[winner] = (preferences[winner] || 0) + 1;
  }
  const averages = {};
  for (const [variant, criteria] of Object.entries(totals)) {
    averages[variant] = {};
    for (const [criterion, total] of Object.entries(criteria)) {
      averages[variant][criterion] = total.count ? Number((total.sum / total.count).toFixed(2)) : null;
    }
  }
  const report = { rated, skipped, averages, preferences, key_file: path.basename(keyFile) };
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  const lines = ['# Итог слепой оценки', '', `Оценено пар: ${rated}${skipped.length ? `, пропущено без ключа: ${skipped.join(', ')}` : ''}`, '', '## Средние оценки (1–5)', '', `| Вариант | ${CRITERIA.map((c) => CRITERIA_RU[c]).join(' | ')} |`, `| --- | ${CRITERIA.map(() => '---').join(' | ')} |`];
  for (const [variant, criteria] of Object.entries(averages)) {
    lines.push(`| ${variant} | ${CRITERIA.map((c) => criteria[c] ?? '—').join(' | ')} |`);
  }
  lines.push('', '## Предпочтения', '');
  for (const [variant, count] of Object.entries(preferences)) lines.push(`- ${variant}: ${count}`);
  lines.push('');
  fs.writeFileSync(path.join(outDir, 'report.md'), lines.join('\n'));
  return report;
}

function main(argv) {
  const [command, file] = argv.slice(2);
  const option = (name, fallback) => {
    const index = argv.indexOf(name);
    return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
  };
  if (command === 'prepare' && file) {
    const result = prepare(file, option('--out', 'build/blind'));
    console.log(`pairs: ${result.pairs}, written to ${result.out} (key.json хранить отдельно)`);
    return 0;
  }
  if (command === 'import' && file) {
    const outDir = option('--out', path.dirname(file));
    const result = importRatings(file, option('--key', path.join(outDir, 'key.json')), outDir);
    console.log(JSON.stringify(result, null, 2));
    return 0;
  }
  console.error('usage: blind_review.js prepare результат.json --out dir | import ratings.json --key key.json --out dir');
  return 2;
}

if (require.main === module) {
  process.exitCode = main(process.argv);
}

module.exports = { prepare, importRatings, pairUp, CRITERIA };
