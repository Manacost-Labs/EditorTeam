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

function contentHash(text) {
  const normalized = String(text).toLowerCase().split(/\s+/).filter(Boolean).join(' ');
  return crypto.createHash('sha256').update(normalized).digest('hex');
}

// Style example texts never leave the gateway; for the copy check the
// evaluation asks the analyzer sidecar for the same examples directly.
async function exampleTexts(payload) {
  const analyzer = (process.env.EDITOR_EVAL_ANALYZER_URL || '').replace(/\/$/, '');
  if (!analyzer) return [];
  const response = await fetch(`${analyzer}/corpus/examples`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      text: payload.text, game: payload.game, profile: payload.profile, limit: 3,
      exclude_hash: contentHash(payload.text),
    }),
  });
  const body = await readJson(response, 'EditorTeam corpus examples');
  return (body.examples || []).map((item) => String(item.excerpt || '')).filter(Boolean);
}

class PipelineE2EProvider {
  constructor(options = {}) {
    this.config = options.config || {};
  }

  id() {
    return this.config.retrieval === 'off' ? 'pipeline-e2e:no-retrieval' : 'pipeline-e2e';
  }

  async callApi(prompt, context = {}) {
    const vars = context.vars || {};
    const input = String(vars.text || '');
    if (!input.trim()) throw new Error('Eval case variable "text" is required');
    const promptVersion = resolvePromptVersion(prompt);
    const gateway = gatewayFor(promptVersion);
    const retrievalOff = this.config.retrieval === 'off';
    const payload = {
      text: input,
      mode: vars.mode || 'edit',
      game: vars.game || 'hearthstone',
      profile: vars.profile || 'constructed-guide',
      language: vars.language || 'ru-RU',
      editorial_mode: vars.editorial_mode || 'GUIDE',
    };
    if (retrievalOff) payload.retrieval = 'off';
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
    const retrieval = result.retrieval || { status: retrievalOff ? 'disabled' : 'unknown', examples_used: 0, example_ids: [] };
    const styleExampleTexts = !retrievalOff && retrieval.status === 'ok' && retrieval.examples_used > 0
      ? await exampleTexts(payload)
      : [];
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
        status: result.status,
        rejection_reasons: result.rejection_reasons || [],
        checks_complete: checksComplete,
        attempts: result.attempts,
        retrieval_variant: retrievalOff ? 'no-retrieval' : 'retrieval',
        retrieval_status: retrieval.status,
        retrieval_examples_used: retrieval.examples_used,
        retrieval_example_ids: retrieval.example_ids || [],
        style_example_texts: styleExampleTexts,
        rejected_returned_source: !accepted && output === input,
        analyzers: Object.keys(health.analyzers || {}),
        digest: crypto.createHash('sha256').update(output).digest('hex'),
      },
    };
  }
}

module.exports = PipelineE2EProvider;
module.exports.contentHash = contentHash;
