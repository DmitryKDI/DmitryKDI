import { deals, tasks, callHistory, STAGES, nextTaskId } from './mockData.js';
import { GENERATED_DOC_TYPES, FILE_DOC_TYPES, computeSmetaBreakdown, generateSmetaContent } from '../documents/generators.js';

/**
 * Мок-реализация единого интерфейса адаптера данных (см. data/adapter.js).
 * Хранит состояние в памяти процесса — меняется по мере CRUD-операций из чата,
 * сбрасывается при перезапуске сервера. Подходит для демо, не для продакшена.
 */

// Документы тоже в памяти — намеренно ничего не пишем на диск в mock-режиме.
// Для реального сохранения файлов используйте DATA_ADAPTER_MODE=sqlite.
const documentsByDeal = {};
let docSeq = 0;
function nextDocId() {
  docSeq += 1;
  return `doc-${docSeq}`;
}
function getDocsFor(dealId) {
  if (!documentsByDeal[dealId]) documentsByDeal[dealId] = [];
  return documentsByDeal[dealId];
}
function findDocById(id) {
  for (const dealId of Object.keys(documentsByDeal)) {
    const doc = documentsByDeal[dealId].find((d) => d.id === id);
    if (doc) return doc;
  }
  return null;
}

export function createMockAdapter() {
  return {
    async listDeals({ stage, type, manager, ids } = {}) {
      let result = deals;
      if (ids && ids.length) result = result.filter((d) => ids.includes(d.id));
      if (stage) result = result.filter((d) => d.stage === stage);
      if (type) result = result.filter((d) => d.type === type);
      if (manager) result = result.filter((d) => d.manager === manager);
      return result;
    },

    async getDeal(id) {
      const deal = deals.find((d) => d.id === id);
      if (!deal) throw new Error(`Сделка ${id} не найдена`);
      return deal;
    },

    async updateDealStage(id, stage) {
      if (!STAGES.includes(stage)) {
        throw new Error(`Неизвестный этап "${stage}". Допустимые: ${STAGES.join(', ')}`);
      }
      const deal = deals.find((d) => d.id === id);
      if (!deal) throw new Error(`Сделка ${id} не найдена`);
      deal.stage = stage;
      return deal;
    },

    async listTasks({ dealId, status } = {}) {
      let result = tasks;
      if (dealId) result = result.filter((t) => t.dealId === dealId);
      if (status) result = result.filter((t) => t.status === status);
      return result;
    },

    async createTask({ dealId, title, description, dueDate }) {
      const deal = deals.find((d) => d.id === dealId);
      if (!deal) throw new Error(`Сделка ${dealId} не найдена — уточните id объекта`);
      if (!title || !title.trim()) throw new Error('Название задачи обязательно');
      const task = {
        id: nextTaskId(),
        dealId,
        title: title.trim(),
        description: description || '',
        dueDate: dueDate || null,
        status: 'open',
      };
      tasks.push(task);
      return task;
    },

    async deleteTask(id) {
      const idx = tasks.findIndex((t) => t.id === id);
      if (idx === -1) throw new Error(`Задача ${id} не найдена`);
      const [removed] = tasks.splice(idx, 1);
      return removed;
    },

    async listCallHistory() {
      return callHistory;
    },

    async getDashboardMetrics() {
      const now = new Date();
      const monthAgo = new Date(now);
      monthAgo.setDate(monthAgo.getDate() - 30);

      const newDealsThisMonth = deals.filter((d) => new Date(d.createdDate) >= monthAgo).length;

      const closedCount = deals.filter((d) => d.stage === 'завершено').length;
      const conversionRate = deals.length ? Math.round((closedCount / deals.length) * 100) : 0;

      const dealsByStage = STAGES.map((stage) => ({
        stage,
        count: deals.filter((d) => d.stage === stage).length,
      }));

      const managerLoad = {};
      for (const d of deals) {
        if (d.stage === 'завершено') continue;
        managerLoad[d.manager] = (managerLoad[d.manager] || 0) + 1;
      }
      const teamLoad = Object.entries(managerLoad).map(([manager, count]) => ({ manager, count }));

      const openTasksCount = tasks.filter((t) => t.status === 'open').length;

      return {
        newDealsThisMonth,
        conversionRate,
        activeDeals: deals.filter((d) => d.stage !== 'завершено').length,
        openTasksCount,
        dealsByStage,
        teamLoad,
      };
    },

    // --- Раздел "Продажи" (ролевой доступ, мок-режим) ---

    async listManagers() {
      return [...new Set(deals.map((d) => d.manager))];
    },

    /**
     * Сводная статистика по всем менеджерам — для роли "начальник отдела продаж".
     * Никаких персональных данных клиентов здесь не отдаём, только агрегаты.
     * periodDays — упрощение для демо: фильтруем по дате СОЗДАНИЯ сделки
     * (в реальном Битрикс24-адаптере правильнее фильтровать по дате закрытия).
     */
    async getSalesOverview({ periodDays = 30 } = {}) {
      const managers = [...new Set(deals.map((d) => d.manager))];
      const since = new Date();
      since.setDate(since.getDate() - periodDays);

      const ranking = managers.map((manager) => {
        const managerDeals = deals.filter((d) => d.manager === manager);
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

    /**
     * Закреплённые контакты конкретного менеджера — для роли "менеджер".
     * Возвращает ТОЛЬКО сделки/клиентов этого менеджера — не всей команды.
     */
    async getManagerContacts(managerName) {
      const managerDeals = deals.filter((d) => d.manager === managerName);
      if (!managerDeals.length) {
        throw new Error(`Менеджер "${managerName}" не найден или за ним нет закреплённых сделок`);
      }
      return managerDeals.map((d) => ({
        dealId: d.id,
        clientName: d.clientName,
        clientPhone: d.clientPhone,
        address: d.address,
        type: d.type,
        stage: d.stage,
        budget: d.budget,
      }));
    },

    // --- Документы объекта (в памяти, без записи на диск — см. dbAdapter.js для реального хранения) ---

    async listDocuments(dealId) {
      const deal = deals.find((d) => d.id === dealId);
      if (!deal) throw new Error(`Сделка ${dealId} не найдена`);
      return getDocsFor(dealId);
    },

    async createGeneratedDocument({ dealId, type }) {
      const deal = deals.find((d) => d.id === dealId);
      if (!deal) throw new Error(`Сделка ${dealId} не найдена`);
      const config = GENERATED_DOC_TYPES[type];
      if (!config) throw new Error(`Неизвестный тип документа "${type}"`);
      const doc = {
        id: nextDocId(),
        dealId,
        type,
        title: config.title,
        status: 'готово',
        downloadable: true,
        content: config.generate(deal),
        filename: `${type}-${deal.id}.${config.ext}`,
        createdDate: new Date().toISOString(),
        meta: null,
        files: [],
      };
      getDocsFor(dealId).unshift(doc);
      return doc;
    },

    async createSmetaDocument({ dealId, area, level }) {
      const deal = deals.find((d) => d.id === dealId);
      if (!deal) throw new Error(`Сделка ${dealId} не найдена`);
      const areaNum = Number(area);
      if (!areaNum || areaNum <= 0) throw new Error('Укажите площадь объекта (м²), больше нуля');
      const breakdown = computeSmetaBreakdown(areaNum, level);
      const doc = {
        id: nextDocId(),
        dealId,
        type: 'smeta',
        title: 'Смета',
        status: 'готово',
        downloadable: true,
        content: generateSmetaContent(deal, areaNum, breakdown),
        filename: `smeta-${deal.id}.csv`,
        createdDate: new Date().toISOString(),
        meta: { area: areaNum, levelLabel: breakdown.level.label, total: breakdown.total },
        files: [],
      };
      getDocsFor(dealId).unshift(doc);
      return doc;
    },

    async createFileDocument({ dealId, type, files }) {
      const deal = deals.find((d) => d.id === dealId);
      if (!deal) throw new Error(`Сделка ${dealId} не найдена`);
      const config = FILE_DOC_TYPES[type];
      if (!config) throw new Error(`Неизвестный тип документа "${type}"`);
      if (!files || !files.length) throw new Error('Нужен хотя бы один файл');
      const doc = {
        id: nextDocId(),
        dealId,
        type,
        title: config.title,
        status: config.status,
        downloadable: false,
        files: files.map((f) => ({ name: f.originalname, size: f.size })),
        createdDate: new Date().toISOString(),
        meta: null,
      };
      getDocsFor(dealId).unshift(doc);
      return doc;
    },

    async deleteDocument(id) {
      for (const dealId of Object.keys(documentsByDeal)) {
        const docs = documentsByDeal[dealId];
        const idx = docs.findIndex((d) => d.id === id);
        if (idx !== -1) return docs.splice(idx, 1)[0];
      }
      throw new Error(`Документ ${id} не найден`);
    },

    async getDocumentContent(id) {
      const doc = findDocById(id);
      if (!doc || !doc.content) throw new Error(`Документ ${id} не найден или не является сгенерированным файлом`);
      return { filename: doc.filename, data: doc.content };
    },

    async getDocumentFile() {
      throw new Error(
        'Скачивание вложенных файлов недоступно в демо-режиме (mock) — файлы там не сохраняются на диск. ' +
          'Переключитесь на DATA_ADAPTER_MODE=sqlite, чтобы файлы реально хранились и скачивались.',
      );
    },
  };
}
