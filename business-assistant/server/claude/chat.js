import Anthropic from '@anthropic-ai/sdk';
import { SYSTEM_PROMPT } from './systemPrompt.js';
import { tools, executeTool } from './tools.js';

const anthropic = new Anthropic();
const MODEL = process.env.ANTHROPIC_MODEL || 'claude-opus-5';
const MAX_TOOL_ITERATIONS = 6;

/**
 * Прогоняет один ход диалога через Claude с ручным agentic-циклом function calling.
 * @param {object[]} history — предыдущая история в формате Anthropic messages (может быть пустой)
 * @param {string} userMessage
 * @param {object} adapter — экземпляр адаптера данных (createDataAdapter())
 * @returns {{ messages: object[], text: string, reports: object[] }}
 */
export async function runAssistant(history, userMessage, adapter) {
  const messages = [...history, { role: 'user', content: userMessage }];
  const reports = [];

  for (let iteration = 0; iteration < MAX_TOOL_ITERATIONS; iteration += 1) {
    const response = await anthropic.messages.create({
      model: MODEL,
      max_tokens: 2048,
      system: SYSTEM_PROMPT,
      tools,
      messages,
    });

    messages.push({ role: 'assistant', content: response.content });

    if (response.stop_reason !== 'tool_use') {
      const text = response.content
        .filter((block) => block.type === 'text')
        .map((block) => block.text)
        .join('\n')
        .trim();
      return { messages, text, reports };
    }

    const toolResults = [];
    for (const block of response.content) {
      if (block.type !== 'tool_use') continue;
      let resultPayload;
      try {
        resultPayload = await executeTool(block.name, block.input, adapter, reports);
      } catch (err) {
        toolResults.push({
          type: 'tool_result',
          tool_use_id: block.id,
          content: `Ошибка: ${err.message}`,
          is_error: true,
        });
        continue;
      }
      toolResults.push({
        type: 'tool_result',
        tool_use_id: block.id,
        content: JSON.stringify(resultPayload),
      });
    }
    messages.push({ role: 'user', content: toolResults });
  }

  return {
    messages,
    text: 'Не удалось завершить обработку запроса за отведённое число шагов. Переформулируйте вопрос, пожалуйста.',
    reports,
  };
}
