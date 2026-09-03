const crypto = require('node:crypto');

function protectedValues(text) {
  return (text.match(/https?:\/\/[^\s)]+|\b\d+(?:[.,]\d+)?\s*%?|\b\d+\b/g) || []).sort();
}

class EditorTeamProvider {
  id() { return 'editorteam'; }

  async callApi(prompt, context) {
  const vars = context.vars || {};
  const input = String(vars.text || prompt || '');
  const mode = process.env.EDITOR_EVAL_MODE === 'baseline' ? 'proofread' : 'edit';
  const base = process.env.EDITOR_GATEWAY_URL || 'http://127.0.0.1:8740';
  const game = vars.game === 'world-of-warcraft' ? 'wow' : (vars.game === 'league-of-legends' ? 'league' : (vars.game || 'hearthstone'));
  let profile = vars.profile || 'constructed-guide';
  if (profile === 'guide') profile = game === 'wow' ? 'wow-guide' : 'constructed-guide';
  const response = await fetch(`${base}/v2/edit`, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({text: input, mode, game, profile, language: 'ru-RU'})
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `EditorTeam HTTP ${response.status}`);
  const output = body.text || input;
  return {
    output,
    metadata: {
      case_id: vars.id,
      prompt_version: body.prompt_version,
      protected_preserved: JSON.stringify(protectedValues(input)) === JSON.stringify(protectedValues(output)),
      accepted: body.accepted,
      checks_complete: body.checks_complete,
      digest: crypto.createHash('sha256').update(output).digest('hex')
    }
  };
  }
}

module.exports = EditorTeamProvider;
