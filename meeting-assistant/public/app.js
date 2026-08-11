const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const timerEl = document.getElementById('timer');
const playback = document.getElementById('playback');
const transcribeBtn = document.getElementById('transcribeBtn');
const transcriptEl = document.getElementById('transcript');
const summarizeBtn = document.getElementById('summarizeBtn');
const summaryEl = document.getElementById('summary');
const sendBtn = document.getElementById('sendBtn');
const statusEl = document.getElementById('status');

let mediaRecorder;
let chunks = [];
let recordedBlob;
let timerInterval;
let secondsElapsed = 0;

function formatTime(s) {
  const m = String(Math.floor(s / 60)).padStart(2, '0');
  const sec = String(s % 60).padStart(2, '0');
  return `${m}:${sec}`;
}

startBtn.addEventListener('click', async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    mediaRecorder.onstop = () => {
      recordedBlob = new Blob(chunks, { type: 'audio/webm' });
      playback.src = URL.createObjectURL(recordedBlob);
      playback.style.display = 'block';
      transcribeBtn.disabled = false;
      stream.getTracks().forEach((t) => t.stop());
    };
    mediaRecorder.start();

    secondsElapsed = 0;
    timerEl.textContent = formatTime(secondsElapsed);
    timerInterval = setInterval(() => {
      secondsElapsed += 1;
      timerEl.textContent = formatTime(secondsElapsed);
    }, 1000);

    startBtn.disabled = true;
    stopBtn.disabled = false;
  } catch (err) {
    setStatus(`Не удалось получить доступ к микрофону: ${err.message}`, true);
  }
});

stopBtn.addEventListener('click', () => {
  mediaRecorder.stop();
  clearInterval(timerInterval);
  startBtn.disabled = false;
  stopBtn.disabled = true;
});

transcribeBtn.addEventListener('click', async () => {
  if (!recordedBlob) return;
  setStatus('Распознаём речь...');
  transcribeBtn.disabled = true;
  try {
    const form = new FormData();
    form.append('audio', recordedBlob, 'recording.webm');
    const res = await fetch('/api/transcribe', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Ошибка распознавания');
    transcriptEl.value = data.text;
    summarizeBtn.disabled = false;
    setStatus('Транскрипт готов.');
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    transcribeBtn.disabled = false;
  }
});

summarizeBtn.addEventListener('click', async () => {
  const transcript = transcriptEl.value.trim();
  if (!transcript) return;
  setStatus('Формируем резюме...');
  summarizeBtn.disabled = true;
  try {
    const res = await fetch('/api/summarize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Ошибка резюмирования');
    summaryEl.value = data.summary;
    sendBtn.disabled = false;
    setStatus('Резюме готово.');
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    summarizeBtn.disabled = false;
  }
});

sendBtn.addEventListener('click', async () => {
  const text = summaryEl.value.trim();
  if (!text) return;
  setStatus('Отправляем в Telegram...');
  sendBtn.disabled = true;
  try {
    const res = await fetch('/api/send-telegram', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Ошибка отправки');
    setStatus('Резюме отправлено в Telegram ✅');
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    sendBtn.disabled = false;
  }
});

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.style.color = isError ? '#c0392b' : '';
}
