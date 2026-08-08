import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { deals as seedDeals, tasks as seedTasks, callHistory as seedCallHistory } from './mockData.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_DB_PATH = path.join(__dirname, 'app.db');

let dbInstance = null;

/**
 * Открывает (и при первом запуске создаёт) файл SQLite. Синглтон на процесс —
 * несколько вызовов createDbAdapter() в рамках одного сервера переиспользуют
 * одно соединение. Путь к файлу настраивается через SQLITE_DB_PATH в .env,
 * по умолчанию — server/data/app.db (в .gitignore, не коммитится).
 */
export function getDb() {
  if (dbInstance) return dbInstance;
  const dbPath = process.env.SQLITE_DB_PATH || DEFAULT_DB_PATH;
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });

  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');

  runMigrations(db);
  seedIfEmpty(db);

  dbInstance = db;
  return db;
}

function runMigrations(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS deals (
      id TEXT PRIMARY KEY,
      address TEXT NOT NULL,
      client_name TEXT NOT NULL,
      client_phone TEXT,
      type TEXT NOT NULL,
      stage TEXT NOT NULL,
      budget INTEGER,
      manager TEXT,
      created_date TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
      id TEXT PRIMARY KEY,
      deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      description TEXT DEFAULT '',
      due_date TEXT,
      status TEXT NOT NULL DEFAULT 'open'
    );

    CREATE TABLE IF NOT EXISTS call_history (
      id TEXT PRIMARY KEY,
      deal_id TEXT REFERENCES deals(id) ON DELETE CASCADE,
      phone TEXT,
      direction TEXT,
      outcome TEXT,
      duration_sec INTEGER,
      date TEXT
    );

    -- Метаданные документа. content_file_path заполнен для сгенерированных
    -- текстовых документов (КП/смета/договор/акт/счёт) — сам файл лежит на
    -- диске в UPLOADS_DIR, здесь только путь. Для документов с прикреплёнными
    -- файлами (дизайн-проект/другой документ) content_file_path = NULL, а
    -- сами файлы — в таблице document_files (документ:файлы = 1:N).
    CREATE TABLE IF NOT EXISTS documents (
      id TEXT PRIMARY KEY,
      deal_id TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      type TEXT NOT NULL,
      title TEXT NOT NULL,
      status TEXT NOT NULL,
      content_file_path TEXT,
      meta_json TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS document_files (
      id TEXT PRIMARY KEY,
      document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      original_filename TEXT NOT NULL,
      stored_path TEXT NOT NULL,
      mime_type TEXT,
      size_bytes INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_deal ON tasks(deal_id);
    CREATE INDEX IF NOT EXISTS idx_calls_deal ON call_history(deal_id);
    CREATE INDEX IF NOT EXISTS idx_documents_deal ON documents(deal_id);
    CREATE INDEX IF NOT EXISTS idx_document_files_doc ON document_files(document_id);
  `);
}

// При первом запуске (пустая таблица deals) засеиваем той же тестовой выборкой,
// что использует mock-режим — так переход mock → sqlite не превращает демо в
// пустой экран, а сразу показывает знакомые объекты, уже по-настоящему сохранённые.
function seedIfEmpty(db) {
  const { count } = db.prepare('SELECT COUNT(*) AS count FROM deals').get();
  if (count > 0) return;

  const insertDeal = db.prepare(`
    INSERT INTO deals (id, address, client_name, client_phone, type, stage, budget, manager, created_date)
    VALUES (@id, @address, @clientName, @clientPhone, @type, @stage, @budget, @manager, @createdDate)
  `);
  const insertTask = db.prepare(`
    INSERT INTO tasks (id, deal_id, title, description, due_date, status)
    VALUES (@id, @dealId, @title, @description, @dueDate, @status)
  `);
  const insertCall = db.prepare(`
    INSERT INTO call_history (id, deal_id, phone, direction, outcome, duration_sec, date)
    VALUES (@id, @dealId, @phone, @direction, @outcome, @durationSec, @date)
  `);

  const seedAll = db.transaction(() => {
    for (const d of seedDeals) insertDeal.run(d);
    for (const t of seedTasks) insertTask.run(t);
    for (const c of seedCallHistory) insertCall.run(c);
  });
  seedAll();
  console.log('[db] SQLite инициализирована и засеяна тестовыми данными (первый запуск, файл создан по SQLITE_DB_PATH).');
}
