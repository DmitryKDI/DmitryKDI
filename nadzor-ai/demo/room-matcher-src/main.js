/* Управление интерфейсом: загрузка файлов, запуск разбора, отрисовка результатов и листов. */

const state = { before: [], after: [] };
window.__nadzorState = state;
let pdfjsLib = null;
let nextId = 1;

const els = {
  runBtn: document.getElementById('runBtn'),
  statusLine: document.getElementById('statusLine'),
  resultsArea: document.getElementById('resultsArea'),
  viewer: document.getElementById('pageViewer'),
  viewerTitle: document.getElementById('viewerTitle'),
  viewerCanvas: document.getElementById('viewerCanvas'),
  viewerClose: document.getElementById('viewerClose'),
};

function fileList(side) {
  return document.querySelector(`.file-chip-list[data-list="${side}"]`);
}

function renderChip(entry, side) {
  let chip = document.getElementById(`chip-${entry.id}`);
  if (!chip) {
    chip = document.createElement('span');
    chip.className = 'file-chip';
    chip.id = `chip-${entry.id}`;
    fileList(side).appendChild(chip);
  }
  const statusHtml =
    entry.status === 'parsing'
      ? '<span class="spinner" aria-hidden="true"></span>'
      : entry.status === 'ok'
        ? `<span class="status ok">лист. ${entry.pages} · помещ. ${entry.roomFacts.length}</span>`
        : entry.status === 'err'
          ? `<span class="status err">${entry.error}</span>`
          : '<span class="status">в очереди</span>';
  chip.innerHTML = `<span class="name">${escapeHtml(entry.name)}</span>${statusHtml}<button aria-label="Убрать файл" data-remove="${entry.id}">×</button>`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function addFiles(side, fileObjs) {
  for (const file of fileObjs) {
    if (!file.name.toLowerCase().endsWith('.pdf')) continue;
    const entry = { id: nextId++, name: file.name, file, status: 'parsing', pages: 0, roomFacts: [], textFacts: [], pdfDoc: null };
    state[side].push(entry);
    renderChip(entry, side);
    parseFile(entry, side);
  }
}

async function parseFile(entry, side) {
  try {
    const buf = await entry.file.arrayBuffer();
    if (!pdfjsLib) pdfjsLib = await window.NadzorBrowser.loadPdfJs();
    const pdfDoc = await pdfjsLib.getDocument({ data: buf }).promise;
    entry.pdfDoc = pdfDoc;
    const pages = [];
    for (let i = 1; i <= pdfDoc.numPages; i++) {
      const page = await pdfDoc.getPage(i);
      const items = await window.NadzorBrowser.readPageItems(page);
      pages.push({ pageNo: i, items });
    }
    const { roomFacts, textFacts } = window.NadzorBrowser.extractFileFacts(pages);
    entry.pages = pdfDoc.numPages;
    entry.roomFacts = roomFacts;
    entry.textFacts = textFacts;
    entry.status = 'ok';
  } catch (err) {
    entry.status = 'err';
    entry.error = 'не читается как PDF';
    console.error(err);
  }
  renderChip(entry, side);
  updateRunState();
}

function removeFile(id) {
  for (const side of ['before', 'after']) {
    const idx = state[side].findIndex((e) => e.id === id);
    if (idx !== -1) {
      state[side].splice(idx, 1);
      document.getElementById(`chip-${id}`)?.remove();
    }
  }
  updateRunState();
}

function updateRunState() {
  const readyBefore = state.before.some((e) => e.status === 'ok');
  const readyAfter = state.after.some((e) => e.status === 'ok');
  const anyParsing = [...state.before, ...state.after].some((e) => e.status === 'parsing');
  els.runBtn.disabled = !(readyBefore && readyAfter) || anyParsing;
  els.statusLine.textContent = anyParsing ? 'Разбор файлов…' : '';
}

// ---------- Запуск сопоставления и отрисовка результата ----------
function runMatch() {
  const before = state.before.filter((e) => e.status === 'ok');
  const after = state.after.filter((e) => e.status === 'ok');
  const rooms = window.NadzorBrowser.candidateRooms(before, after);
  const groupedBefore = window.NadzorBrowser.factsByRoom(before, rooms);
  const groupedAfter = window.NadzorBrowser.factsByRoom(after, rooms);

  const totalRoomFactsBefore = before.reduce((n, f) => n + f.roomFacts.length, 0);
  const totalRoomFactsAfter = after.reduce((n, f) => n + f.roomFacts.length, 0);

  renderResults({ rooms, groupedBefore, groupedAfter, before, after, totalRoomFactsBefore, totalRoomFactsAfter });
}

function sortRooms(rooms) {
  return [...rooms].sort((a, b) => {
    const na = parseInt(a, 10), nb = parseInt(b, 10);
    if (na !== nb) return na - nb;
    return a.localeCompare(b, 'ru');
  });
}

function renderResults({ rooms, groupedBefore, groupedAfter, before, after, totalRoomFactsBefore, totalRoomFactsAfter }) {
  const area = els.resultsArea;
  if (rooms.size === 0) {
    area.innerHTML = `<div class="card empty-state">Общих номеров помещений не найдено. Проверьте, что в обоих комплектах есть подписи или «Экспликация помещений» с номерами.</div>`;
    return;
  }

  const sorted = sortRooms(rooms);
  let richCount = 0;
  const rows = sorted.map((room) => {
    const b = groupedBefore.get(room) || [];
    const a = groupedAfter.get(room) || [];
    const textCount = b.filter((p) => p.fact.type === 'text').length + a.filter((p) => p.fact.type === 'text').length;
    const rich = textCount >= 1;
    if (rich) richCount++;
    const name = window.NadzorBrowser.roomLabel(a) || window.NadzorBrowser.roomLabel(b) || '—';
    return `
      <div class="room-row" id="room-${room}">
        <div class="room-summary" tabindex="0" role="button" aria-expanded="false">
          <span class="room-no mono">${escapeHtml(room)}</span>
          <span class="room-name">${escapeHtml(name)}</span>
          <span class="evidence-chip ${rich ? 'rich' : 'thin'}">${rich ? '🟢 есть текст' : '🟠 только графика'}</span>
          <span class="chev">▸</span>
        </div>
        <div class="room-detail">
          ${evidenceColumn('Проектная документация (требование)', b, room)}
          ${evidenceColumn('Рабочая / исполнительная (факт)', a, room)}
        </div>
      </div>`;
  });

  area.innerHTML = `
    <div class="stats-strip">
      <div class="stat"><span class="n mono">${rooms.size}</span><span class="label">Общих помещений</span></div>
      <div class="stat"><span class="n mono">${richCount}</span><span class="label">С текстовым пояснением</span></div>
      <div class="stat"><span class="n mono">${totalRoomFactsBefore}</span><span class="label">Помещений в ПД</span></div>
      <div class="stat"><span class="n mono">${totalRoomFactsAfter}</span><span class="label">Помещений в РД/ИД</span></div>
    </div>
    <div class="section-head">
      <h2>Сопоставленные помещения</h2>
      <span class="note">нажмите на строку, чтобы посмотреть доказательства</span>
    </div>
    <div class="room-list">${rows.join('')}</div>`;

  area.querySelectorAll('.room-summary').forEach((el) => {
    el.addEventListener('click', () => toggleRoom(el));
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleRoom(el); }
    });
  });
  area.querySelectorAll('.page-link').forEach((el) => {
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      const { fileIdx, side, page } = el.dataset;
      openPage(side, Number(fileIdx), Number(page));
    });
  });
}

function truncateAround(text, room, radius) {
  const idx = text.indexOf(room);
  if (idx === -1) return truncate(text, radius * 2);
  const start = Math.max(0, idx - radius);
  const end = Math.min(text.length, idx + room.length + radius);
  return (start > 0 ? '…' : '') + text.slice(start, end) + (end < text.length ? '…' : '');
}

function truncate(text, max) {
  return text.length > max ? text.slice(0, max - 1) + '…' : text;
}

function dedupePairs(pairs) {
  const seen = new Set();
  return pairs.filter(({ fact, file }) => {
    const key = `${file.id}|${fact.page}|${fact.type}|${fact.type === 'room' ? fact.key : fact.text}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function evidenceColumn(title, pairs, room) {
  pairs = dedupePairs(pairs);
  if (pairs.length === 0) {
    return `<div class="evidence-col"><h3>${title}</h3><div class="empty-side">Ничего не найдено</div></div>`;
  }
  const items = pairs
    .slice()
    .sort((x, y) => (x.fact.type === 'text' ? -1 : 1) - (y.fact.type === 'text' ? -1 : 1))
    .map(({ fact, file }) => {
      const side = state.before.includes(file) ? 'before' : 'after';
      const idx = state[side].indexOf(file);
      const cls = fact.type === 'room' ? 'evidence-item roomfact' : 'evidence-item';
      const text = fact.type === 'room'
        ? `${fact.key} · ${fact.name}${fact.area ? ` · ${fact.area} м²` : ''}${fact.category ? ` · ${fact.category}` : ''}`
        : truncateAround(fact.text, room, 160);
      return `<div class="${cls}">
        <div class="meta">
          <span>${escapeHtml(file.name)}</span>
          <a class="page-link" data-file-idx="${idx}" data-side="${side}" data-page="${fact.page}">лист ${fact.page}</a>
        </div>
        <div class="txt">${escapeHtml(text)}</div>
      </div>`;
    });
  return `<div class="evidence-col"><h3>${title}</h3>${items.join('')}</div>`;
}

function toggleRoom(summaryEl) {
  const row = summaryEl.closest('.room-row');
  const open = row.classList.toggle('open');
  summaryEl.setAttribute('aria-expanded', String(open));
}

async function openPage(side, fileIdx, pageNo) {
  const entry = state[side][fileIdx];
  if (!entry || !entry.pdfDoc) return;
  els.viewerTitle.textContent = `${entry.name} · лист ${pageNo}`;
  els.viewer.showModal();
  const page = await entry.pdfDoc.getPage(pageNo);
  const viewport = page.getViewport({ scale: 1.6 });
  const canvas = els.viewerCanvas;
  const ctx = canvas.getContext('2d');
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({ canvasContext: ctx, viewport }).promise;
}

// ---------- Разметка событий ----------
function wireDropzone(zoneEl) {
  const side = zoneEl.dataset.side;
  const dropArea = zoneEl.querySelector('.drop-area');
  const input = zoneEl.querySelector('input[type="file"]');
  dropArea.addEventListener('click', (e) => {
    if (e.target !== input) input.click();
  });
  dropArea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  input.addEventListener('change', () => addFiles(side, [...input.files]));
  dropArea.addEventListener('dragover', (e) => { e.preventDefault(); dropArea.classList.add('drag'); });
  dropArea.addEventListener('dragleave', () => dropArea.classList.remove('drag'));
  dropArea.addEventListener('drop', (e) => {
    e.preventDefault();
    dropArea.classList.remove('drag');
    addFiles(side, [...e.dataTransfer.files]);
  });
}

document.querySelectorAll('.dropzone').forEach(wireDropzone);
document.getElementById('runBtn').addEventListener('click', runMatch);
document.body.addEventListener('click', (e) => {
  const removeId = e.target?.dataset?.remove;
  if (removeId) removeFile(Number(removeId));
});
els.viewerClose.addEventListener('click', () => els.viewer.close());
els.viewer.addEventListener('click', (e) => { if (e.target === els.viewer) els.viewer.close(); });

// прогрев библиотеки заранее, чтобы первый файл не ждал загрузки pdf.js
window.NadzorBrowser.loadPdfJs().then((lib) => { pdfjsLib = lib; });
