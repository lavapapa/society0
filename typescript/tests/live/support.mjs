export function text(value) {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(part => typeof part === 'string' ? part : part?.text ?? '').join('');
  return '';
}

export function sectionsOf(content) {
  const lines = String(content ?? '').split('\n');
  const find = labels => {
    const index = lines.findIndex(line => labels.some(label => line.startsWith(label)));
    if (index < 0) return '';
    return lines.slice(index).join('\n');
  };
  return {
    state: find(['当前状态', '状态（变更）']),
    actions: find(['可用行动', '可用行动（变更）']),
    percepts: find(['感知', '感知（变更）']),
  };
}

export function estimateSlots(record, budget, baselineText) {
  const messages = record.messages ?? [];
  const system = messages.find(message => message.role === 'system');
  const current = [...messages].reverse().find(message => message.role === 'user' && String(message.content).includes('本次输入'));
  const perceptMessages = messages.filter(message => message.role === 'user' && String(message.content).trim().startsWith('{"source"'));
  const inputSections = sectionsOf(current?.content);
  const header = budget.estimate(text(system?.content)) + Math.ceil(Number(record.toolSchemaBytes ?? 0) / 3.2);
  const baseline = budget.estimate(baselineText);
  const stateDelta = budget.estimate(inputSections.state);
  const actions = budget.estimate(inputSections.actions);
  const percepts = perceptMessages.reduce((sum, message) => sum + budget.estimate(text(message.content)), 0);
  const total = messages.reduce((sum, message) => sum + budget.estimate(text(message.content)), 0);
  const output = Number(record.response?.usage?.completion_tokens ?? 0) || budget.estimate(text(record.response?.content));
  const systemText = budget.estimate(text(system?.content));
  const history = Math.max(0, total - systemText - baseline - stateDelta - actions - percepts - output);
  return { header, baseline, stateDelta, actions, percepts, history, output, total, promptTokens: Number(record.response?.usage?.prompt_tokens ?? 0) || null, budget: budget.tokens };
}
