// Эти проверки выполняются самим Promptfoo поверх ответа провайдера.
module.exports = {
  preservedFacts: (output, context) => Boolean(context?.metadata?.protected_preserved),
  readable: (output) => typeof output === 'string' && output.trim().length > 20,
};
