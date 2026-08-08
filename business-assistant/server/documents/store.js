// Хранилище файлов документов на диске сервера. Сгенерированные текстовые
// документы и загруженные вложения (дизайн-проект, «другой документ») лежат
// здесь как обычные файлы; таблицы documents/document_files (см. data/db.js)
// хранят только метаданные и относительный путь — не сами байты.
//
// Для продакшена с нагрузкой выше «один сервер» замените локальный диск на
// объектное хранилище (S3-совместимое) — интерфейс ниже (readFile/writeFile/
// deleteFile по относительному пути) специально узкий, чтобы такую замену
// можно было сделать, не трогая dbAdapter.js.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const UPLOADS_DIR = process.env.UPLOADS_DIR || path.join(__dirname, '..', 'uploads');

export function ensureUploadsDir() {
  fs.mkdirSync(UPLOADS_DIR, { recursive: true });
}

// Файлы всегда кладём под UPLOADS_DIR/<dealId>/... — sanitize защищает от
// path traversal через имя файла (../../etc/passwd и т.п.), resolvedAbsPath
// дополнительно проверяется, что не вышел за пределы UPLOADS_DIR.
export function sanitizeFilename(name) {
  const base = String(name).replace(/[/\\]/g, '_').replace(/\.\./g, '_').trim();
  return base.slice(-150) || 'file';
}

function resolveAbsPath(relPath) {
  const absPath = path.join(UPLOADS_DIR, relPath);
  const normalizedRoot = path.join(UPLOADS_DIR, path.sep);
  if (!(absPath + path.sep).startsWith(normalizedRoot) && absPath !== UPLOADS_DIR) {
    throw new Error('Некорректный путь к файлу документа');
  }
  return absPath;
}

export function writeFile(relPath, data) {
  const absPath = resolveAbsPath(relPath);
  fs.mkdirSync(path.dirname(absPath), { recursive: true });
  fs.writeFileSync(absPath, data);
}

export function readFile(relPath, encoding) {
  const absPath = resolveAbsPath(relPath);
  return fs.readFileSync(absPath, encoding);
}

export function deleteFile(relPath) {
  if (!relPath) return;
  try {
    fs.rmSync(resolveAbsPath(relPath), { force: true });
  } catch {
    // файл уже мог быть удалён вручную — не критично для удаления записи в БД
  }
}
