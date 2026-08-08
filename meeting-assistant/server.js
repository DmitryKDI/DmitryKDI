import 'dotenv/config';
import express from 'express';
import multer from 'multer';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Anthropic from '@anthropic-ai/sdk';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = process.env.PORT || 3000;
const ANTHROPIC_MODEL = process.env.ANTHROPIC_MODEL || 'claude-opus-5';

const anthropic = new Anthropic();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 100 * 1024 * 1024 } });

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// --- Speech-to-text: forward the recorded audio to OpenAI Whisper ---
app.post('/api/transcribe', upload.single('audio'), async (req, res) => {
  try {
    if (!process.env.OPENAI_API_KEY) {
      return res.status(500).json({ error: 'OPENAI_API_KEY is not set on the server' });
    }
    if (!req.file) {
      return res.status(400).json({ error: 'No audio file uploaded' });
    }

    const form = new FormData();
    const blob = new Blob([req.file.buffer], { type: req.file.mimetype || 'audio/webm' });
    form.append('file', blob, req.file.originalname || 'recording.webm');
    form.append('model', 'whisper-1');
    form.append('language', 'ru');

    const whisperRes = await fetch('https://api.openai.com/v1/audio/transcriptions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${process.env.OPENAI_API_KEY}` },
      body: form,
    });

    if (!whisperRes.ok) {
      const errText = await whisperRes.text();
      return res.status(502).json({ error: `Whisper API error: ${errText}` });
    }

    const data = await whisperRes.json();
    res.json({ text: data.text });
  } catch (err) {
    console.error('transcribe error', err);
    res.status(500).json({ error: 'Transcription failed' });
  }
});

// --- Summarization: send the transcript to Claude ---
app.post('/api/summarize', async (req, res) => {
  try {
    const { transcript } = req.body;
    if (!transcript || !transcript.trim()) {
      return res.status(400).json({ error: 'transcript is required' });
    }

    const message = await anthropic.messages.create({
      model: ANTHROPIC_MODEL,
      max_tokens: 2048,
      system:
        'Ты — ассистент, который делает краткое деловое резюме встречи по её транскрипту. ' +
        'Отвечай на русском языке. Структурируй ответ так: "Основные темы", "Договорённости / решения", ' +
        '"Задачи и ответственные" (если упоминались), "Открытые вопросы". Если какого-то раздела нет — не выдумывай, опусти его.',
      messages: [{ role: 'user', content: `Транскрипт встречи:\n\n${transcript}` }],
    });

    const summary = message.content
      .filter((block) => block.type === 'text')
      .map((block) => block.text)
      .join('\n');

    res.json({ summary });
  } catch (err) {
    console.error('summarize error', err);
    res.status(500).json({ error: 'Summarization failed' });
  }
});

// --- Delivery: send the summary to Telegram ---
app.post('/api/send-telegram', async (req, res) => {
  try {
    const { text } = req.body;
    if (!text || !text.trim()) {
      return res.status(400).json({ error: 'text is required' });
    }
    if (!process.env.TELEGRAM_BOT_TOKEN || !process.env.TELEGRAM_CHAT_ID) {
      return res.status(500).json({ error: 'TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID is not set on the server' });
    }

    const tgRes = await fetch(
      `https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/sendMessage`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: process.env.TELEGRAM_CHAT_ID,
          text,
        }),
      },
    );

    if (!tgRes.ok) {
      const errText = await tgRes.text();
      return res.status(502).json({ error: `Telegram API error: ${errText}` });
    }

    res.json({ ok: true });
  } catch (err) {
    console.error('telegram error', err);
    res.status(500).json({ error: 'Telegram delivery failed' });
  }
});

app.listen(PORT, () => {
  console.log(`Meeting assistant (Stage 1 MVP) running at http://localhost:${PORT}`);
});
