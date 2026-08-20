/* Разбор помещений — сопоставление ПД и РД/ИД по номеру помещения, целиком в браузере.
   Порт логики packages/documents/extract.py + packages/analysis/rules/rooms.py
   (репозиторий nadzor-ai) на JavaScript. Один и тот же ключ сшивки — номер помещения,
   извлекаемый одинаково что из подписи на плане, что из строки «Экспликации». */

const RE_ROOM_ROW = /^(\d{3,4}[а-яё]?)\s+([А-Яа-яЁё][^\d]{1,90}?)(?:\s+(\d+[.,]\d+))?(?:\s+(В\d))?$/;
const RE_ROOM_LIST_ROW = /^((?:\d{3,4}[а-яё]?\s*,\s*)+\d{3,4}[а-яё]?)\s+([А-Яа-яЁё][^\d]{1,90}?)(?:\s+(\d+[.,]\d+))?(?:\s+(В\d))?$/;
const RE_ROOM_TOKEN = /\b\d{3,4}[а-яё]?\b/g;

// ---------- pdf.js: библиотека и воркер приложены к странице как base64, без сети ----------
async function loadPdfJs() {
  const libSrc = atob(document.getElementById('pdfjs-lib-b64').textContent.trim());
  const workerSrc = atob(document.getElementById('pdfjs-worker-b64').textContent.trim());
  const libBlobUrl = URL.createObjectURL(new Blob([libSrc], { type: 'text/javascript' }));
  const pdfjsLib = await import(libBlobUrl);
  const workerBlobUrl = URL.createObjectURL(new Blob([workerSrc], { type: 'text/javascript' }));
  pdfjsLib.GlobalWorkerOptions.workerSrc = workerBlobUrl;
  return pdfjsLib;
}

// ---------- Разбор страницы: текстовые элементы -> строки -> абзацы ----------
async function readPageItems(page) {
  const content = await page.getTextContent();
  return content.items
    .filter((it) => it.str && it.str.trim())
    .map((it) => ({
      str: it.str,
      x: it.transform[4],
      y: it.transform[5],
      height: Math.hypot(it.transform[2], it.transform[3]) || 10,
      width: it.width || 0,
    }));
}

function buildLines(items) {
  // Насыщенный чертёж содержит множество подписей на одной высоте по всей ширине
  // листа — группировка только по Y склеила бы их в одну строку через весь лист.
  // Продолжаем текущую строку, только если следующий элемент действительно рядом
  // и по Y, и по X (короткий разрыв), иначе начинаем новую подпись.
  const sorted = items.slice().sort((a, b) => b.y - a.y || a.x - b.x);
  const lines = [];
  let current = null;
  for (const it of sorted) {
    const sameRow = current && Math.abs(current.y - it.y) < it.height * 0.45;
    const closeX = current && it.x - current.endX < it.height * 6;
    if (sameRow && closeX) {
      const gap = it.x - current.endX;
      if (gap > it.height * 0.28 && !/\s$/.test(current.text)) current.text += ' ';
      current.text += it.str;
      current.endX = Math.max(current.endX, it.x + it.width);
      current.maxHeight = Math.max(current.maxHeight, it.height);
    } else {
      current = { y: it.y, x0: it.x, endX: it.x + it.width, text: it.str, maxHeight: it.height };
      lines.push(current);
    }
  }
  for (const line of lines) {
    line.text = line.text.replace(/\s+/g, ' ').trim();
    line.height = line.maxHeight;
  }
  return lines.filter((l) => l.text);
}

function buildParagraphs(lines) {
  // Высота глифа из PDF.js не соответствует напрямую кеглю шрифта настолько
  // предсказуемо, чтобы по ней надёжно отличать заголовок от текста — эвристика
  // по размеру здесь только шумит и теряет абзацы. Короткие фрагменты (меньше
  // 40 символов после склейки) отсекаются длиной, этого достаточно.
  const paragraphs = [];
  let current = null;
  for (const line of lines) {
    const gapOk = current && (current.lastY - line.y) < current.height * 2.4;
    const xOk = current && Math.abs(current.x0 - line.x0) < 40;
    if (current && gapOk && xOk) {
      current.text += (/[-–]\s*$/.test(current.text) ? '' : ' ') + line.text;
      current.lastY = line.y;
    } else {
      current = { text: line.text, y: line.y, lastY: line.y, x0: line.x0, height: line.height };
      paragraphs.push(current);
    }
  }
  return paragraphs.filter((p) => p.text.length >= 40);
}

// ---------- Факты: помещения (по строке) и текст (по абзацу) ----------
function roomFactsFromLine(line, pageNo) {
  const facts = [];
  const text = line.text;
  let m = RE_ROOM_ROW.exec(text);
  if (m) {
    facts.push(makeRoomFact(m[1], m[2], m[3], m[4], pageNo));
    return facts;
  }
  m = RE_ROOM_LIST_ROW.exec(text);
  if (m) {
    for (const roomNo of m[1].split(/\s*,\s*/)) {
      facts.push(makeRoomFact(roomNo, m[2], m[3], m[4], pageNo));
    }
  }
  return facts;
}

function makeRoomFact(roomNo, name, area, category, pageNo) {
  return {
    type: 'room',
    key: roomNo,
    name: name.replace(/[\s-]+$/, '').trim(),
    area: area ? area.replace(',', '.') : '',
    category: category || '',
    page: pageNo,
    text: '',
  };
}

const RE_BARE_ROOM_NO = /^(\d{3,4}[а-яё]?)$/;
const RE_NAME_START = /^[А-Яа-яЁё]/;

// На насыщенном чертеже номер помещения и его название иногда распадаются на
// соседние строки вместо одной (число и подпись стоят чуть по-разному). Если
// одна строка — голый номер, а следующая рядом начинается с названия, это
// такая же подпись помещения, как и цельная строка.
function roomFactFromAdjacentLines(numLine, nameLine, pageNo) {
  const numMatch = RE_BARE_ROOM_NO.exec(numLine.text);
  if (!numMatch || !RE_NAME_START.test(nameLine.text)) return null;
  const closeEnough =
    Math.abs(numLine.y - nameLine.y) < numLine.height * 3 &&
    Math.abs(numLine.x0 - nameLine.x0) < numLine.height * 12;
  if (!closeEnough) return null;
  const areaMatch = /(\d+[.,]\d+)\s*$/.exec(nameLine.text);
  const name = areaMatch ? nameLine.text.slice(0, areaMatch.index).trim() : nameLine.text;
  return makeRoomFact(numMatch[1], name, areaMatch ? areaMatch[1] : '', '', pageNo);
}

// На насыщенных чертежах (планы этажей, экспликации) имя подписи часто уже
// приходит от читалки PDF одним куском («Раздевальная для МГН»), а отдельно
// стоит только сам номер — строчная склейка это не всегда восстанавливает
// корректно на плотных участках. Дополнительно ищем такие пары напрямую по
// исходным позициям элементов, независимо от того, как разбились строки.
function roomFactsFromItemPairs(items, pageNo) {
  const facts = [];
  const numberItems = items.filter((it) => /^\d{3,4}[а-яё]?$/.test(it.str.trim()));
  const nameItems = items.filter((it) => /^[А-Яа-яЁё]/.test(it.str.trim()) && it.str.trim().length > 2);
  for (const numIt of numberItems) {
    let best = null;
    let bestDist = Infinity;
    for (const nameIt of nameItems) {
      const dy = Math.abs(numIt.y - nameIt.y);
      const dx = nameIt.x - (numIt.x + numIt.width);
      if (dy < numIt.height * 0.6 && dx > -2 && dx < numIt.height * 4) {
        const dist = dx + dy * 5;
        if (dist < bestDist) {
          bestDist = dist;
          best = nameIt;
        }
      }
    }
    if (best) facts.push(makeRoomFact(numIt.str.trim(), best.str.trim(), '', '', pageNo));
  }
  return facts;
}

function extractFileFacts(pages) {
  const roomFacts = [];
  const textFacts = [];
  for (const { pageNo, items } of pages) {
    const lines = buildLines(items);
    const matchedAsBare = new Set();
    for (let i = 0; i < lines.length; i++) {
      const direct = roomFactsFromLine(lines[i], pageNo);
      if (direct.length) {
        roomFacts.push(...direct);
        continue;
      }
      for (const j of [i - 1, i + 1]) {
        const neighbour = lines[j];
        if (!neighbour || matchedAsBare.has(i)) continue;
        const fact = roomFactFromAdjacentLines(lines[i], neighbour, pageNo);
        if (fact) {
          roomFacts.push(fact);
          matchedAsBare.add(i);
          break;
        }
      }
    }
    const knownRooms = new Set(roomFacts.map((f) => f.key));
    for (const fact of roomFactsFromItemPairs(items, pageNo)) {
      if (!knownRooms.has(fact.key)) {
        roomFacts.push(fact);
        knownRooms.add(fact.key);
      }
    }
    for (const para of buildParagraphs(lines)) {
      textFacts.push({ type: 'text', text: para.text, page: pageNo });
    }
  }
  return { roomFacts, textFacts };
}

// ---------- Блокировка: сопоставление и группировка по номеру помещения ----------
function roomNumberSet(files) {
  const set = new Set();
  for (const f of files) for (const r of f.roomFacts) set.add(r.key);
  return set;
}

function candidateRooms(beforeFiles, afterFiles) {
  const a = roomNumberSet(beforeFiles);
  const b = roomNumberSet(afterFiles);
  const out = new Set();
  for (const r of a) if (b.has(r)) out.add(r);
  return out;
}

function factsByRoom(files, rooms) {
  const grouped = new Map();
  for (const r of rooms) grouped.set(r, []);
  for (const file of files) {
    for (const fact of [...file.roomFacts, ...file.textFacts]) {
      const mentioned = new Set();
      const text = fact.type === 'room' ? fact.key : fact.text;
      let m;
      RE_ROOM_TOKEN.lastIndex = 0;
      while ((m = RE_ROOM_TOKEN.exec(text))) {
        if (rooms.has(m[0])) mentioned.add(m[0]);
      }
      if (fact.type === 'room' && rooms.has(fact.key)) mentioned.add(fact.key);
      for (const room of mentioned) grouped.get(room).push({ fact, file });
    }
  }
  return grouped;
}

function roomLabel(pairs) {
  for (const { fact } of pairs) if (fact.type === 'room' && fact.name) return fact.name;
  return '';
}

// ---------- Публичный пайплайн для main.js ----------
window.NadzorBrowser = {
  loadPdfJs,
  readPageItems,
  extractFileFacts,
  candidateRooms,
  factsByRoom,
  roomLabel,
  buildLines,
  buildParagraphs,
};
