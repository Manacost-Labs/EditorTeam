// Promptfoo-загрузчик редакторского набора evals/cases/editorial.json:
// канонический формат кейса (id, game, profile, source, reference,
// expected_action, defects, must_preserve, allowed_changes) переводится в
// переменные теста без дублирования данных.
const fs = require('node:fs');
const path = require('node:path');

function load() {
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'editorial.json'), 'utf8'));
  return cases.map((item) => ({
    description: `${item.synthetic ? 'synthetic' : 'corpus'} ${item.expected_action} ${item.id}`,
    vars: {
      id: item.id,
      game: item.game,
      profile: item.profile,
      text: item.source,
      reference: item.reference,
      expected_action: item.expected_action,
      defects: item.defects,
      must_preserve: item.must_preserve,
      protected_entities: item.must_preserve,
      allowed_changes: item.allowed_changes,
      synthetic: item.synthetic === true,
      expected_properties: {
        preserve_facts: true,
        preserve_game_terms: true,
        preserve_markdown: true,
        remove_ai_slop: item.defects.includes('ai-frames'),
        remove_bureaucracy: item.defects.includes('bureaucracy'),
      },
    },
  }));
}

module.exports = load;
