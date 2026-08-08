import { Router } from 'express';
import { runAssistant } from '../claude/chat.js';

export function createChatRouter(adapter) {
  const router = Router();

  router.post('/', async (req, res) => {
    try {
      const { history, message } = req.body;
      if (!message || !message.trim()) {
        return res.status(400).json({ error: 'message is required' });
      }
      if (!process.env.ANTHROPIC_API_KEY) {
        return res.status(500).json({ error: 'ANTHROPIC_API_KEY не задан на сервере' });
      }

      const { messages, text, reports } = await runAssistant(
        Array.isArray(history) ? history : [],
        message,
        adapter,
      );

      res.json({ history: messages, text, reports });
    } catch (err) {
      console.error('chat error', err);
      res.status(500).json({ error: err.message || 'Ошибка ассистента' });
    }
  });

  return router;
}
