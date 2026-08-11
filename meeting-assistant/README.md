# ИИ-ассистент встреч — Этап 1 (MVP)

Локальный прототип: запись звонка в браузере → расшифровка (OpenAI Whisper) →
резюме (Claude API) → доставка в Telegram. Без деплоя, без голосового диалога,
без камеры (см. `../CLAUDE.md`/бриф в корне для дорожной карты последующих этапов).

## Стек

- **Backend**: Node.js + Express (`server.js`) — прокси к Whisper/Claude/Telegram,
  чтобы API-ключи не попадали в браузер.
- **Frontend**: чистый HTML/JS (`public/`) — запись через `MediaRecorder`
  (`getUserMedia`), без сборщиков.
- **STT**: OpenAI Whisper API (`whisper-1`).
- **LLM**: Claude API (`@anthropic-ai/sdk`), модель задаётся в `.env`.
- **Доставка**: Telegram Bot API (`sendMessage`).

## Установка

```bash
cd meeting-assistant
npm install
cp .env.example .env
```

Заполните `.env`:

- `OPENAI_API_KEY` — ключ OpenAI для Whisper.
- `ANTHROPIC_API_KEY` — ключ Claude API.
- `TELEGRAM_BOT_TOKEN` — токен бота от [@BotFather](https://t.me/BotFather).
- `TELEGRAM_CHAT_ID` — куда слать резюме. Напишите боту любое сообщение,
  затем откройте `https://api.telegram.org/bot<TOKEN>/getUpdates` и возьмите
  `message.chat.id`.

## Запуск

```bash
npm start
```

Откройте http://localhost:3000, разрешите доступ к микрофону и пройдите
сценарий: запись → распознать → резюме → отправить в Telegram.

## Что дальше (не в этом MVP)

Этап 2 — голосовой диалог в реальном времени (STT+LLM+TTS цикл).
Этап 3 — анализ через камеру (снимки раз в несколько секунд + vision-модель).
См. бриф в корне репозитория.
