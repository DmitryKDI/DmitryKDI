import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';

const GREETING = {
  role: 'assistant',
  text: 'Здравствуйте! Спросите про сделки, задачи или попросите сформировать Excel-отчёт по объектам.',
  reports: [],
};

export default function ChatPanel() {
  const [displayMessages, setDisplayMessages] = useState([GREETING]);
  const [apiHistory, setApiHistory] = useState([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [displayMessages, sending]);

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setDisplayMessages((prev) => [...prev, { role: 'user', text }]);
    setSending(true);
    try {
      const res = await api.sendChatMessage(apiHistory, text);
      setApiHistory(res.history);
      setDisplayMessages((prev) => [...prev, { role: 'assistant', text: res.text, reports: res.reports }]);
    } catch (err) {
      setDisplayMessages((prev) => [...prev, { role: 'error', text: err.message }]);
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="chat-wrap">
      <div className="chat-messages" ref={scrollRef}>
        {displayMessages.map((m, i) => (
          <div className={`msg ${m.role}`} key={i}>
            {m.text}
            {m.reports?.map((r) => (
              <div key={r.filename}>
                <a className="report-link" href={r.reportUrl} download>
                  📥 Скачать отчёт ({r.dealCount} сделок)
                </a>
              </div>
            ))}
          </div>
        ))}
        {sending && <div className="msg assistant">…</div>}
      </div>

      <div className="chat-input-row">
        <button className="icon-btn" disabled title="Голосовой ввод — Этап 3">
          🎤
        </button>
        <textarea
          rows={1}
          value={input}
          placeholder="Спросите про сделки, задачи или отчёт…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button className="send-btn" onClick={handleSend} disabled={sending || !input.trim()}>
          ➤
        </button>
      </div>
    </div>
  );
}
