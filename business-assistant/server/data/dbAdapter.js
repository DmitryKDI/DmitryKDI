import crypto from 'node:crypto';
import path from 'node:path';
import { getDb } from './db.js';
import { STAGES } from './mockData.js';
import { ensureUploadsDir, sanitizeFilename, writeFile, readFile, deleteFile } from '../documents/store.js';
import { GENERATED_DOC_TYPES, FILE_DOC_TYPES, computeSmetaBreakdown, generateSmetaContent } from '../documents/generators.js';

function rowToDeal(row) {
  return {
    id: row.id,
    address: row.address,
    clientName: row.client_name,
    clientPhone: row.client_phone,
    type: row.type,
    stage: row.stage,
    budget: row.budget,
    manager: row.manager,
    createdDate: row.created_date,
  };
}

function rowToTask(row) {
  return {
    id: row.id,
    dealId: row.deal_id,
    title: row.title,
    description: row.description || '',
    dueDate: row.due_date,
    status: row.status,
  };
}

function rowToCall(row) {
  return {
    id: row.id,
    dealId: row.deal_id,
    phone: row.phone,
    direction: row.direction,
    outcome: row.outcome,
    durationSec: row.duration_sec,
    date: row.date,
  };
}

function rowToDocument(row, fileRows) {
  return {
    id: row.id,
    dealId: row.deal_id,
    type: row.type,
    title: row.title,
    status: row.status,
    downloadable: !!row.content_file_path,
    createdDate: row.created_at,
    meta: row.meta_json ? JSON.parse(row.meta_json) : null,
    files: (fileRows || []).map((f) => ({
      id: f.id,
      name: f.original_filename,
      size: f.size_bytes,
      mimeType: f.mime_type,
    })),
  };
}

/**
 * SQLite-адаптер — реализует ТОТ ЖЕ интерфейс, что mockAdapter.js и
 * bitrix24Adapter.js (см. data/adapter.js), но с реальным сохранением на
 * диск: объекты/сделки/задачи/звонки/документы переживают перезапуск сервера.
 * Включается через DATA_ADAPTER_MODE=sqlite в .env — ни промпты, ни схемы
 * function calling, ни React-компоненты не меняются.
 */
export function createDbAdapter() {
  const db = getDb();
  ensureUploadsDir();

  const stmt = {
    listDeals: db.prepare('SELECT * FROM deals'),
    getDeal: db.prepare('SELECT * FROM deals WHERE id = ?'),
    updateDealStage: db.prepare('UPDATE deals SET stage = ? WHERE id = ?'),

    listTasks: db.prepare('SELECT * FROM tasks'),
    getTask: db.prepare('SELECT * FROM tasks WHERE id = ?'),
    insertTask: db.prepare(
      "INSERT INTO tasks (id, deal_id, title, description, due_date, status) VALUES (?, ?, ?, ?, ?, 'open')",
    ),
    deleteTask: db.prepare('DELETE FROM tasks WHERE id = ?'),

    listCalls: db.prepare('SELECT * FROM call_history'),

    listDocumentsByDeal: db.prepare('SELECT * FROM documents WHERE deal_id = ? ORDER BY created_at DESC'),
    getDocument: db.prepare('SELECT * FROM documents WHERE id = ?'),
    insertDocument: db.prepare(`
      INSERT INTO documents (id, deal_id, type, title, status, content_file_path, meta_json, created_at)
      VALUES (@id, @dealId, @type, @title, @status, @contentFilePath, @metaJson, @createdAt)
    `),
    deleteDocument: db.prepare('DELETE FROM documents WHERE id = ?'),

    listFilesByDocument: db.prepare('SELECT * FROM document_files WHERE document_id = ?'),
    getDocumentFile: db.prepare('SELECT * FROM document_files WHERE id = ?'),
    insertDocumentFile: db.prepare(`
      INSERT INTO document_files (id, document_id, original_filename, stored_path, mime_type, size_bytes)
      VALUES (@id, @documentId, @originalFilename, @storedPath, @mimeType, @sizeBytes)
    `),
  };

  const adapter = {
    async listDeals({ stage, type, manager, ids } = {}) {
      let rows = stmt.listDeals.all();
      if (ids && ids.length) rows = rows.filter((r) => ids.includes(r.id));
      if (stage) rows = rows.filter((r) => r.stage === stage);
      if (type) rows = rows.filter((r) => r.type === type);
      if (manager) rows = rows.filter((r) => r.manager === manager);
      return rows.map(rowToDeal);
    },

    async getDeal(id) {
      const row = stmt.getDeal.get(id);
      if (!row) throw new Error(`Сделка ${id} не найдена`);
      return rowToDeal(row);
    },

    async updateDealStage(id, stage) {
      if (!STAGES.includes(stage)) {
        throw new Error(`Неизвестный этап "${stage}". Допустимые: ${STAGES.join(', ')}`);
      }
      const info = stmt.updateDealStage.run(stage, id);
      if (info.changes === 0) throw new Error(`Сделка ${id} не найдена`);
      return adapter.getDeal(id);
    },

    async listTasks({ dealId, status } = {}) {
      let rows = stmt.listTasks.all();
      if (dealId) rows = rows.filter((r) => r.deal_id === dealId);
      if (status) rows = rows.filter((r) => r.status === status);
      return rows.map(rowToTask);
    },

    async createTask({ dealId, title, description, dueDate }) {
      const deal = stmt.getDeal.get(dealId);
      if (!deal) throw new Error(`Сделка ${dealId} не найдена — уточните id объекта`);
      if (!title || !title.trim()) throw new Error('Название задачи обязательно');
      const id = `T-${crypto.randomUUID().slice(0, 8)}`;
      stmt.insertTask.run(id, dealId, title.trim(), description || '', dueDate || null);
      return rowToTask(stmt.getTask.get(id));
    },

    async deleteTask(id) {
      const row = stmt.getTask.get(id);
      if (!row) throw new Error(`Задача ${id} не найдена`);
      stmt.deleteTask.run(id);
      return rowToTask(row);
    },

    async listCallHistory() {
      return stmt.listCalls.all().map(rowToCall);
    },

    async getDashboardMetrics() {
      const allDeals = await adapter.listDeals({});
      const allTasks = await adapter.listTasks({});
      const now = new Date();
      const monthAgo = new Date(now);
      monthAgo.setDate(monthAgo.getDate() - 30);

      const newDealsThisMonth = allDeals.filter((d) => new Date(d.createdDate) >= monthAgo).length;
      const closedCount = allDeals.filter((d) => d.stage === 'завершено').length;
      const conversionRate = allDeals.length ? Math.round((closedCount / allDeals.length) * 100) : 0;

      const dealsByStage = STAGES.map((s) => ({
        stage: s,
        count: allDeals.filter((d) => d.stage === s).length,
      }));

      const managerLoad = {};
      for (const d of allDeals) {
        if (d.stage === 'завершено') continue;
        managerLoad[d.manager] = (managerLoad[d.manager] || 0) + 1;
      }
      const teamLoad = Object.entries(managerLoad).map(([manager, count]) => ({ manager, count }));

      return {
        newDealsThisMonth,
        conversionRate,
        activeDeals: allDeals.filter((d) => d.stage !== 'завершено').length,
        openTasksCount: allTasks.filter((t) => t.status === 'open').length,
        dealsByStage,
        teamLoad,
      };
    },

    // --- Раздел "Продажи" ---

    async listManagers() {
      const rows = stmt.listDeals.all();
      return [...new Set(rows.map((r) => r.manager))];
    },

    async getSalesOverview({ periodDays = 30 } = {}) {
      const allDeals = await adapter.listDeals({});
      const managers = [...new Set(allDeals.map((d) => d.manager))];
      const since = new Date();
      since.setDate(since.getDate() - periodDays);

      const ranking = managers.map((manager) => {
        const managerDeals = allDeals.filter((d) => d.manager === manager);
        const won = managerDeals.filter((d) => d.stage === 'завершено');
        const wonInPeriod = won.filter((d) => new Date(d.createdDate) >= since);
        return {
          manager,
          dealsWonCount: won.length,
          dealsWonSum: won.reduce((sum, d) => sum + (d.budget || 0), 0),
          dealsWonInPeriod: wonInPeriod.length,
          activeDealsCount: managerDeals.filter((d) => d.stage !== 'завершено').length,
        };
      });
      ranking.sort((a, b) => b.dealsWonSum - a.dealsWonSum);

      return { periodDays, ranking };
    },

    async getManagerContacts(managerName) {
      const allDeals = await adapter.listDeals({ manager: managerName });
      if (!allDeals.length) {
        throw new Error(`Менеджер "${managerName}" не найден или за ним нет закреплённых сделок`);
      }
      return allDeals.map((d) => ({
        dealId: d.id,
        clientName: d.clientName,
        clientPhone: d.clientPhone,
        address: d.address,
        type: d.type,
        stage: d.stage,
        budget: d.budget,
      }));
    },

    // --- Документы объекта ---
    // Метаданные — в SQLite (таблицы documents/document_files), сами байты —
    // на диске под UPLOADS_DIR (см. documents/store.js).

    async listDocuments(dealId) {
      const deal = stmt.getDeal.get(dealId);
      if (!deal) throw new Error(`Сделка ${dealId} не найдена`);
      const docs = stmt.listDocumentsByDeal.all(dealId);
      return docs.map((d) => rowToDocument(d, stmt.listFilesByDocument.all(d.id)));
    },

    // type: kp | contract | act | invoice — генерируются мгновенно, без вложений.
    async createGeneratedDocument({ dealId, type }) {
      const dealRow = stmt.getDeal.get(dealId);
      if (!dealRow) throw new Error(`Сделка ${dealId} не найдена`);
      const config = GENERATED_DOC_TYPES[type];
      if (!config) throw new Error(`Неизвестный тип документа "${type}"`);

      const deal = rowToDeal(dealRow);
      const id = crypto.randomUUID();
      const relPath = path.join(dealId, `${id}.${config.ext}`);
      writeFile(relPath, config.generate(deal));

      stmt.insertDocument.run({
        id,
        dealId,
        type,
        title: config.title,
        status: 'готово',
        contentFilePath: relPath,
        metaJson: null,
        createdAt: new Date().toISOString(),
      });
      return rowToDocument(stmt.getDocument.get(id), []);
    },

    // Смета по замерам: area (м²) обязателен, level — econom|standard|premium.
    async createSmetaDocument({ dealId, area, level }) {
      const dealRow = stmt.getDeal.get(dealId);
      if (!dealRow) throw new Error(`Сделка ${dealId} не найдена`);
      const areaNum = Number(area);
      if (!areaNum || areaNum <= 0) throw new Error('Укажите площадь объекта (м²), больше нуля');

      const deal = rowToDeal(dealRow);
      const breakdown = computeSmetaBreakdown(areaNum, level);
      const id = crypto.randomUUID();
      const relPath = path.join(dealId, `${id}.csv`);
      writeFile(relPath, generateSmetaContent(deal, areaNum, breakdown));

      stmt.insertDocument.run({
        id,
        dealId,
        type: 'smeta',
        title: 'Смета',
        status: 'готово',
        contentFilePath: relPath,
        metaJson: JSON.stringify({ area: areaNum, levelLabel: breakdown.level.label, total: breakdown.total }),
        createdAt: new Date().toISOString(),
      });
      return rowToDocument(stmt.getDocument.get(id), []);
    },

    // type: design | other — требуют минимум один файл. files: multer-объекты
    // с полем .buffer (memoryStorage) — байты пишутся на диск здесь.
    async createFileDocument({ dealId, type, files }) {
      const dealRow = stmt.getDeal.get(dealId);
      if (!dealRow) throw new Error(`Сделка ${dealId} не найдена`);
      const config = FILE_DOC_TYPES[type];
      if (!config) throw new Error(`Неизвестный тип документа "${type}"`);
      if (!files || !files.length) throw new Error('Нужен хотя бы один файл');

      const id = crypto.randomUUID();
      stmt.insertDocument.run({
        id,
        dealId,
        type,
        title: config.title,
        status: config.status,
        contentFilePath: null,
        metaJson: null,
        createdAt: new Date().toISOString(),
      });

      for (const f of files) {
        const fileId = crypto.randomUUID();
        const relPath = path.join(dealId, `${id}-${fileId}-${sanitizeFilename(f.originalname)}`);
        writeFile(relPath, f.buffer);
        stmt.insertDocumentFile.run({
          id: fileId,
          documentId: id,
          originalFilename: f.originalname,
          storedPath: relPath,
          mimeType: f.mimetype || null,
          sizeBytes: f.size || (f.buffer ? f.buffer.length : null),
        });
      }
      return rowToDocument(stmt.getDocument.get(id), stmt.listFilesByDocument.all(id));
    },

    async deleteDocument(id) {
      const row = stmt.getDocument.get(id);
      if (!row) throw new Error(`Документ ${id} не найден`);
      const fileRows = stmt.listFilesByDocument.all(id);
      const result = rowToDocument(row, fileRows);

      if (row.content_file_path) deleteFile(row.content_file_path);
      for (const f of fileRows) deleteFile(f.stored_path);
      stmt.deleteDocument.run(id); // ON DELETE CASCADE удалит связанные document_files

      return result;
    },

    // Содержимое сгенерированного текстового документа (для скачивания).
    async getDocumentContent(id) {
      const row = stmt.getDocument.get(id);
      if (!row || !row.content_file_path) {
        throw new Error(`Документ ${id} не найден или не является сгенерированным файлом`);
      }
      const data = readFile(row.content_file_path, 'utf8');
      const ext = path.extname(row.content_file_path).slice(1);
      return { filename: `${row.type}-${row.deal_id}.${ext}`, data };
    },

    // Байты загруженного файла-вложения (для скачивания).
    async getDocumentFile(fileId) {
      const row = stmt.getDocumentFile.get(fileId);
      if (!row) throw new Error(`Файл ${fileId} не найден`);
      const data = readFile(row.stored_path);
      return { filename: row.original_filename, mimeType: row.mime_type || 'application/octet-stream', data };
    },
  };

  return adapter;
}
