const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const PROMPTS = Object.freeze({
  baseline: fs.readFileSync(path.join(__dirname, '../prompts/baseline.txt'), 'utf8').trim(),
  candidate: fs.readFileSync(path.join(__dirname, '../prompts/candidate.txt'), 'utf8').trim(),
});
const PROVIDER_DEFAULTS = Object.freeze({
  openai: 'https://api.openai.com/v1',
  openrouter: 'https://openrouter.ai/api/v1',
  ollama: 'http://127.0.0.1:11434/v1',
});
const ALLOWED_PROVIDERS = new Set(['openai-compatible', ...Object.keys(PROVIDER_DEFAULTS)]);

function resolvePromptVersion(prompt) {
  const selected = String(prompt || '').trim();
  for (const [version, allowed] of Object.entries(PROMPTS)) {
    if (selected === allowed) return version;
  }
  throw new Error('Prompt is not allowlisted: expected repository baseline or candidate');
}

function envForVersion(version, suffix) {
  return process.env[`EDITOR_EVAL_${version.toUpperCase()}_${suffix}`];
}

function protectedValues(text) {
  return (String(text).match(/https?:\/\/[^\s)]+|(?<![\p{L}\p{N}_])\d+(?:[.,]\d+)?\s*%?/gu) || []).sort();
}

async function readJson(response, label) {
  let body;
  try {
    body = await response.json();
  } catch (error) {
    throw new Error(`${label} returned invalid JSON: ${error.message}`);
  }
  if (!response.ok) {
    throw new Error(body?.error?.message || body?.error || `${label} HTTP ${response.status}`);
  }
  return body;
}

function normalizeGame(value) {
  if (value === 'world-of-warcraft') return 'wow';
  if (value === 'league-of-legends') return 'league';
  return value || 'hearthstone';
}

function normalizeProfile(profile, game) {
  if (!profile || profile === 'guide') return game === 'wow' ? 'wow-guide' : 'constructed-guide';
  return profile;
}

class PromptDirectProvider {
  id() { return 'prompt-direct'; }

  async callApi(prompt, context = {}) {
    const vars = context.vars || {};
    const input = String(vars.text || '');
    if (!input.trim()) throw new Error('Eval case variable "text" is required');

    const promptVersion = resolvePromptVersion(prompt);
    if (process.env.EDITOR_EVAL_OFFLINE === '1') {
      return this.offlineResponse(input, vars, promptVersion);
    }

    const provider = envForVersion(promptVersion, 'PROVIDER')
      || process.env.EDITOR_EVAL_PROVIDER
      || 'openai-compatible';
    if (!ALLOWED_PROVIDERS.has(provider)) {
      throw new Error(`Unsupported EDITOR_EVAL_PROVIDER ${JSON.stringify(provider)}`);
    }
    const model = envForVersion(promptVersion, 'MODEL')
      || process.env.EDITOR_EVAL_MODEL
      || process.env.EDITOR_MODEL;
    if (!model) throw new Error(`Model is required for ${promptVersion}: set EDITOR_EVAL_${promptVersion.toUpperCase()}_MODEL or EDITOR_EVAL_MODEL`);

    const baseURL = (envForVersion(promptVersion, 'BASE_URL')
      || process.env.EDITOR_EVAL_BASE_URL
      || PROVIDER_DEFAULTS[provider]
      || '').replace(/\/$/, '');
    if (!baseURL) throw new Error(`Base URL is required for provider ${provider}`);
    const apiKey = envForVersion(promptVersion, 'API_KEY')
      || process.env.EDITOR_EVAL_API_KEY
      || process.env.EDITOR_API_KEY
      || process.env.OPENAI_API_KEY;
    if (provider !== 'ollama' && !apiKey) throw new Error(`API key is required for provider ${provider}`);

    const headers = { 'content-type': 'application/json' };
    if (apiKey) headers.authorization = `Bearer ${apiKey}`;
    const messages = [
      { role: 'system', content: PROMPTS[promptVersion] },
      { role: 'user', content: input },
    ];
    const modelResponse = await fetch(`${baseURL}/chat/completions`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        model,
        messages,
        temperature: Number(process.env.EDITOR_EVAL_TEMPERATURE || 0.2),
      }),
    });
    const modelBody = await readJson(modelResponse, `${provider} model`);
    const proposed = String(modelBody?.choices?.[0]?.message?.content || '').trim();
    if (!proposed) throw new Error(`${provider} model returned empty content`);

    const gateway = (process.env.EDITOR_GATEWAY_URL || 'http://127.0.0.1:8740').replace(/\/$/, '');
    const game = normalizeGame(vars.game);
    const profile = normalizeProfile(vars.profile, game);
    const [validationResponse, healthResponse] = await Promise.all([
      fetch(`${gateway}/validate`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          before: input,
          after: proposed,
          game,
          profile,
          mode: vars.editorial_mode || 'GUIDE',
          depth: vars.depth || 'обычная',
        }),
      }),
      fetch(`${gateway}/health`),
    ]);
    const [validation, health] = await Promise.all([
      readJson(validationResponse, 'EditorTeam validation'),
      readJson(healthResponse, 'EditorTeam health'),
    ]);
    const checksComplete = health.ok === true && health.checks_complete === true;
    const accepted = validation.accepted === true && checksComplete;
    const output = accepted ? proposed : input;

    return {
      output,
      prompt: `SYSTEM (${promptVersion}):\n${PROMPTS[promptVersion]}\n\nUSER:\n${input}`,
      metadata: {
        mode: 'prompt-direct',
        case_id: vars.id,
        provider,
        model,
        prompt_version: promptVersion,
        accepted,
        checks_complete: checksComplete,
        rejected_returned_source: !accepted,
        protected_preserved: JSON.stringify(protectedValues(input)) === JSON.stringify(protectedValues(output)),
        validation_violations: validation.violations || [],
        digest: crypto.createHash('sha256').update(output).digest('hex'),
      },
    };
  }

  offlineResponse(input, vars, promptVersion) {
    return {
      output: input,
      prompt: `SYSTEM (${promptVersion}):\n${PROMPTS[promptVersion]}\n\nUSER:\n${input}`,
      metadata: {
        mode: 'prompt-direct',
        case_id: vars.id,
        provider: 'offline',
        model: 'identity',
        prompt_version: promptVersion,
        accepted: null,
        checks_complete: false,
        rejected_returned_source: false,
        protected_preserved: true,
        deterministic_only: true,
        digest: crypto.createHash('sha256').update(input).digest('hex'),
      },
    };
  }
}

module.exports = PromptDirectProvider;
module.exports.resolvePromptVersion = resolvePromptVersion;
module.exports.PROMPTS = PROMPTS;
