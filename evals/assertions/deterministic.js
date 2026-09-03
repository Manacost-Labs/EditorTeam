const AI_PHRASES = [
  'важно отметить',
  'стоит отметить',
  'следует отметить',
  'как уже было сказано выше',
  'в этой статье мы рассмотрим',
  'подводя итог',
  'в заключение',
  'по своей сути',
  'по сути можно сказать',
];

const BUREAUCRACY = [
  'в рамках данного',
  'на текущий момент',
  'имеет место',
  'обеспечивает возможность',
  'осуществлять',
  'произвести замену',
  'посредством',
];

function multiset(values) {
  const counts = new Map();
  for (const value of values) counts.set(value, (counts.get(value) || 0) + 1);
  return counts;
}

function sameMultiset(left, right) {
  const a = multiset(left);
  const b = multiset(right);
  if (a.size !== b.size) return false;
  return [...a].every(([key, count]) => b.get(key) === count);
}

function isSubset(subset, superset) {
  const wanted = multiset(subset);
  const actual = multiset(superset);
  return [...wanted].every(([key, count]) => (actual.get(key) || 0) >= count);
}

function numbers(text) {
  return String(text).match(/(?<![\p{L}\p{N}_])\d+(?:[.,]\d+)?(?:\s*%)?/gu) || [];
}

function urls(text) {
  return String(text).match(/https?:\/\/[^\s)<>"]+/g) || [];
}

function negations(text) {
  return (String(text).toLowerCase().match(/(?<![\p{L}\p{N}_])(?:не|ни|нет|нельзя|никогда|без)(?![\p{L}\p{N}_])/gu) || []);
}

function fencedBlocks(text) {
  return String(text).match(/^(```|~~~)[^\n]*\n[\s\S]*?^\1\s*$/gm) || [];
}

function tableBlocks(text) {
  const lines = String(text).split(/\r?\n/);
  const blocks = [];
  for (let index = 0; index < lines.length;) {
    if (!lines[index].includes('|')) {
      index += 1;
      continue;
    }
    const block = [];
    while (index < lines.length && lines[index].includes('|')) {
      block.push(lines[index]);
      index += 1;
    }
    if (block.length >= 2 && block.some((line) => /\|?\s*:?-{3,}/.test(line))) blocks.push(block.join('\n'));
  }
  return blocks;
}

function markdownSignature(text) {
  const lines = String(text).split(/\r?\n/);
  return {
    headings: lines.filter((line) => /^#{1,6}\s/.test(line)).map((line) => line.match(/^#{1,6}/)[0]),
    lists: lines.filter((line) => /^\s*(?:[-+*]|\d+[.)])\s/.test(line)).map((line) => line.match(/^\s*(?:[-+*]|\d+[.)])/)[0].trim().replace(/^\d+/, '1')),
    quotes: lines.filter((line) => /^\s*>/.test(line)).length,
    links: [...String(text).matchAll(/\[[^\]]+\]\(([^)]+)\)/g)].map((match) => match[1]),
    emphasis: (String(text).match(/(?:\*\*|__|(?<!\*)\*(?!\*)|(?<!_)_(?!_))/g) || []).length,
  };
}

function sameJSON(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function result(name, pass, detail = '') {
  return {
    pass,
    score: pass ? 1 : 0,
    reason: `${name}: ${pass ? 'ok' : detail || 'failed'}`,
  };
}

function hasNone(text, phrases) {
  const lower = String(text).toLowerCase();
  return phrases.every((phrase) => !lower.includes(phrase));
}

function asList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== 'string' || !value.trim()) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [value];
  } catch {
    return [value];
  }
}

function deterministic(output, context = {}) {
  const source = String(context.vars?.text || '');
  const edited = String(output || '');
  const expected = context.vars?.expected_properties || {};
  const entities = asList(context.vars?.protected_entities);
  const sourceNumbers = numbers(source);
  const outputNumbers = numbers(edited);
  const sourceURLs = urls(source);
  const outputURLs = urls(edited);
  const sourceNegations = negations(source);
  const outputNegations = negations(edited);
  const checks = [
    result('non-empty', edited.trim().length > 0, 'output is empty'),
    result('numbers-preserved', isSubset(sourceNumbers, outputNumbers), 'source number or percentage changed or disappeared'),
    result('no-new-numbers', isSubset(outputNumbers, sourceNumbers), 'output introduced a number or percentage'),
    result('urls-preserved', isSubset(sourceURLs, outputURLs), 'source URL changed or disappeared'),
    result('no-new-urls', isSubset(outputURLs, sourceURLs), 'output introduced a URL'),
    result('negations-preserved', sameJSON(sourceNegations, outputNegations), 'negation markers changed or moved'),
    result('markdown-preserved', sameJSON(markdownSignature(source), markdownSignature(edited)), 'Markdown structure changed'),
    result('tables-intact', sameJSON(tableBlocks(source), tableBlocks(edited)), 'Markdown table changed or moved'),
    result('code-blocks-intact', sameJSON(fencedBlocks(source), fencedBlocks(edited)), 'fenced code block changed or moved'),
    result('game-entities-preserved', entities.every((entity) => edited.toLocaleLowerCase('ru').includes(String(entity).toLocaleLowerCase('ru'))), 'protected card or game entity disappeared'),
    result('ai-phrases-removed', !expected.remove_ai_slop || hasNone(edited, AI_PHRASES), 'known AI phrase remains'),
    result('bureaucracy-removed', !expected.remove_bureaucracy || hasNone(edited, BUREAUCRACY), 'forbidden bureaucracy remains'),
    result(
      'checks-complete',
      context.metadata?.deterministic_only === true || context.metadata?.checks_complete === true,
      'provider did not complete all checks',
    ),
    result(
      'rejected-returns-source',
      context.metadata?.accepted !== false || edited === source,
      'rejected provider response did not return the source verbatim',
    ),
  ];
  return {
    pass: checks.every((check) => check.pass),
    score: checks.reduce((sum, check) => sum + check.score, 0) / checks.length,
    reason: checks.every((check) => check.pass) ? 'deterministic guardrails: all passed' : 'deterministic guardrails: failed',
    componentResults: checks,
  };
}

module.exports = deterministic;
module.exports.AI_PHRASES = AI_PHRASES;
module.exports.BUREAUCRACY = BUREAUCRACY;
module.exports.numbers = numbers;
module.exports.urls = urls;
module.exports.negations = negations;
