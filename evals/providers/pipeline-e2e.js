const crypto = require('node:crypto');
const { resolvePromptVersion } = require('./prompt-direct.js');

function gatewayFor(version) {
  const key = `EDITOR_EVAL_${version.toUpperCase()}_GATEWAY_URL`;
  const value = process.env[key];
  if (!value) throw new Error(`${key} is required for pipeline-e2e`);
  return value.replace(/\/$/, '');
}

async function readJson(response, label) {
  let body;
  try {
    body = await response.json();
  } catch (error) {
    throw new Error(`${label} returned invalid JSON: ${error.message}`);
  }
  if (!response.ok) throw new Error(body?.error || `${label} HTTP ${response.status}`);
  return body;
}

class PipelineE2EProvider {
  id() { return 'pipeline-e2e'; }

  async callApi(prompt, context = {}) {
    const vars = context.vars || {};
    const input = String(vars.text || '');
    if (!input.trim()) throw new Error('Eval case variable "text" is required');
    const promptVersion = resolvePromptVersion(prompt);
    const gateway = gatewayFor(promptVersion);
    const payload = {
      text: input,
      mode: vars.mode || 'edit',
      game: vars.game || 'hearthstone',
      profile: vars.profile || 'constructed-guide',
      language: vars.language || 'ru-RU',
      editorial_mode: vars.editorial_mode || 'GUIDE',
    };
    const [editResponse, healthResponse] = await Promise.all([
      fetch(`${gateway}/v2/edit`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      }),
      fetch(`${gateway}/health`),
    ]);
    const [result, health] = await Promise.all([
      readJson(editResponse, 'EditorTeam pipeline'),
      readJson(healthResponse, 'EditorTeam health'),
    ]);
    if (result.prompt_variant !== promptVersion) {
      throw new Error(`Gateway variant mismatch: expected ${promptVersion}, got ${result.prompt_variant}`);
    }
    const checksComplete = result.checks_complete === true && health.checks_complete === true;
    const accepted = result.accepted === true && checksComplete;
    const output = accepted ? String(result.text || '') : input;
    return {
      output,
      metadata: {
        mode: 'pipeline-e2e',
        case_id: vars.id,
        provider: result.provider,
        model: result.model,
        prompt_version: result.prompt_variant,
        pipeline_prompt_version: result.prompt_version,
        accepted,
        checks_complete: checksComplete,
        attempts: result.attempts,
        rejected_returned_source: !accepted && output === input,
        analyzers: Object.keys(health.analyzers || {}),
        digest: crypto.createHash('sha256').update(output).digest('hex'),
      },
    };
  }
}

module.exports = PipelineE2EProvider;
