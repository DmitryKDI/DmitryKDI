// Заготовка реального провайдера данных поверх Битрикс24 REST API (входящий вебхук).
// Реализует ТОТ ЖЕ интерфейс, что и mockAdapter.js — переключение делается только
// через DATA_ADAPTER_MODE=bitrix24 в .env, без изменения промптов, схем function
// calling или UI. См. README → "Как подключить реальный Битрикс24".
//
// ⚠️ Это каркас, не протестированный на реальном портале. Перед боевым запуском
// обязательно проверьте:
//  - соответствие STAGE_ID вашей воронке сделок (Настройки → CRM → Воронки продаж);
//  - ID пользовательских полей (UF_CRM_*), если тип сделки/объект/бюджет хранятся
//    в кастомных полях, а не в стандартных;
//  - способ привязки задачи к сделке (в разных порталах это разные UF-поля).

const BASE_URL = process.env.BITRIX24_WEBHOOK_URL;

async function callBitrix(method, params = {}) {
  if (!BASE_URL) {
    throw new Error(
      'BITRIX24_WEBHOOK_URL не задан в .env. Либо укажите вебхук, либо верните DATA_ADAPTER_MODE=mock',
    );
  }
  const url = `${BASE_URL.replace(/\/?$/, '/')}${method}.json`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  const data = await res.json();
  if (data.error) {
    throw new Error(`Битрикс24 (${method}): ${data.error_description || data.error}`);
  }
  return data.result;
}

// TODO: подгоните под реальные поля вашего портала.
function mapBitrixDealToInternal(raw) {
  return {
    id: String(raw.ID),
    address: raw.TITLE, // TODO: если адрес хранится в отдельном UF-поле — замените здесь
    clientName: raw.CONTACT_ID ? `Контакт #${raw.CONTACT_ID}` : '', // TODO: подтянуть crm.contact.get при необходимости
    clientPhone: '',
    type: 'ремонт', // TODO: сопоставить с UF-полем "тип работ" (ремонт/экспертиза), если оно заведено
    stage: raw.STAGE_ID,
    budget: raw.OPPORTUNITY ? Number(raw.OPPORTUNITY) : null,
    manager: raw.ASSIGNED_BY_ID ? `Менеджер #${raw.ASSIGNED_BY_ID}` : '',
    createdDate: raw.DATE_CREATE,
  };
}

function mapBitrixTaskToInternal(raw) {
  return {
    id: String(raw.id),
    dealId: '', // TODO: извлечь связанную сделку из UF_CRM_TASK/UF_TASK_WEBDAV_FILES в зависимости от настройки портала
    title: raw.title,
    description: raw.description || '',
    dueDate: raw.deadline,
    status: raw.status === '5' ? 'done' : 'open', // 5 = "Завершена" в стандартном статусе задач Б24
  };
}

export function createBitrix24Adapter() {
  return {
    async listDeals({ stage, type, manager, ids } = {}) {
      const filter = {};
      if (ids && ids.length) filter.ID = ids;
      if (stage) filter.STAGE_ID = stage;
      // TODO: type/manager обычно фильтруются через кастомные поля — добавьте после уточнения ID полей
      const raw = await callBitrix('crm.deal.list', {
        filter,
        select: ['ID', 'TITLE', 'STAGE_ID', 'OPPORTUNITY', 'ASSIGNED_BY_ID', 'CONTACT_ID', 'DATE_CREATE'],
      });
      let result = (raw || []).map(mapBitrixDealToInternal);
      if (type) result = result.filter((d) => d.type === type);
      if (manager) result = result.filter((d) => d.manager === manager);
      return result;
    },

    async getDeal(id) {
      const raw = await callBitrix('crm.deal.get', { id });
      return mapBitrixDealToInternal(raw);
    },

    async updateDealStage(id, stage) {
      await callBitrix('crm.deal.update', { id, fields: { STAGE_ID: stage } });
      return this.getDeal(id);
    },

    async listTasks({ dealId, status } = {}) {
      const filter = {};
      // TODO: замените на реальное поле привязки задачи к сделке в вашем портале
      if (dealId) filter.UF_CRM_TASK = [`D_${dealId}`];
      if (status === 'done') filter.STATUS = 5;
      if (status === 'open') filter['!STATUS'] = 5;
      const raw = await callBitrix('tasks.task.list', { filter });
      return (raw?.tasks || []).map(mapBitrixTaskToInternal);
    },

    async createTask({ dealId, title, description, dueDate }) {
      const raw = await callBitrix('tasks.task.add', {
        fields: {
          TITLE: title,
          DESCRIPTION: description || '',
          DEADLINE: dueDate || undefined,
          UF_CRM_TASK: [`D_${dealId}`], // TODO: см. примечание выше
        },
      });
      return mapBitrixTaskToInternal({ ...raw.task, id: raw.task?.id ?? raw });
    },

    async deleteTask(id) {
      await callBitrix('tasks.task.delete', { taskId: id });
      return { id, deleted: true };
    },

    async listCallHistory() {
      throw new Error(
        'История звонков приходит из LPTracker (Этап 5), не из Битрикс24. См. lpTrackerAdapter.js',
      );
    },

    async getDashboardMetrics() {
      const allDeals = await this.listDeals({});
      const now = new Date();
      const monthAgo = new Date(now);
      monthAgo.setDate(monthAgo.getDate() - 30);
      const newDealsThisMonth = allDeals.filter((d) => d.createdDate && new Date(d.createdDate) >= monthAgo).length;
      // TODO: замените 'завершено' на реальный финальный STAGE_ID вашей воронки
      const closedCount = allDeals.filter((d) => d.stage === 'завершено').length;
      const conversionRate = allDeals.length ? Math.round((closedCount / allDeals.length) * 100) : 0;
      const stageGroups = {};
      for (const d of allDeals) stageGroups[d.stage] = (stageGroups[d.stage] || 0) + 1;
      return {
        newDealsThisMonth,
        conversionRate,
        activeDeals: allDeals.filter((d) => d.stage !== 'завершено').length,
        openTasksCount: null, // TODO: посчитать через tasks.task.list с фильтром по всем сделкам
        dealsByStage: Object.entries(stageGroups).map(([stage, count]) => ({ stage, count })),
        teamLoad: [], // TODO: агрегировать по ASSIGNED_BY_ID
      };
    },

    // --- Раздел "Продажи" (ролевой доступ) ---
    // ⚠️ Персональные данные (телефоны клиентов) — обязательно проверьте на бэкенде,
    // что вызывающий пользователь действительно имеет роль "менеджер X" (Этап 6,
    // реальная авторизация), прежде чем отдавать getManagerContacts(X). В демо-режиме
    // (mock) роль передаётся клиентом без проверки — это нормально ТОЛЬКО для демо.

    async listManagers() {
      // TODO: список менеджеров лучше брать из user.get (сотрудники отдела продаж),
      // а не из выборки сделок — здесь это упрощение для скелета.
      const raw = await callBitrix('crm.deal.list', {
        select: ['ASSIGNED_BY_ID'],
        filter: { CLOSED: 'N' },
      });
      return [...new Set((raw || []).map((d) => `Менеджер #${d.ASSIGNED_BY_ID}`))];
    },

    async getSalesOverview({ periodDays = 30 } = {}) {
      // TODO: STAGE_ID замените на реальный ID финальной "успешно закрытой" стадии
      // вашей воронки (обычно WON), а фильтр по дате — на CLOSEDATE, а не DATE_CREATE.
      const won = await callBitrix('crm.deal.list', {
        filter: { STAGE_ID: 'WON' },
        select: ['ASSIGNED_BY_ID', 'OPPORTUNITY', 'DATE_CREATE'],
      });
      const byManager = {};
      for (const d of won || []) {
        const key = d.ASSIGNED_BY_ID;
        byManager[key] ??= { manager: `Менеджер #${key}`, dealsWonCount: 0, dealsWonSum: 0 };
        byManager[key].dealsWonCount += 1;
        byManager[key].dealsWonSum += Number(d.OPPORTUNITY || 0);
      }
      const ranking = Object.values(byManager).sort((a, b) => b.dealsWonSum - a.dealsWonSum);
      return { periodDays, ranking };
    },

    async getManagerContacts(managerName) {
      // TODO: managerName сейчас строка вида "Менеджер #123" из listManagers() выше —
      // распарсите реальный user ID и отфильтруйте по ASSIGNED_BY_ID; подтяните
      // связанные crm.contact.get для имени/телефона клиента.
      throw new Error(
        'getManagerContacts не реализован для Битрикс24 — см. TODO в bitrix24Adapter.js',
      );
    },

    // --- Документы объекта ---
    // TODO: не реализовано. В Битрикс24 файлы сделки обычно хранятся через
    // crm.deal.update (поле UF_CRM_*_FILES) или отдельным модулем «Диск»
    // (disk.folder.*, disk.file.*) — привязка сделки к папке на Диске делается
    // вручную/через доп. интеграцию, единого стандартного способа нет. Пока
    // используйте DATA_ADAPTER_MODE=sqlite для реального хранения документов —
    // см. README → «Хранение документов и база данных».
    async listDocuments() {
      throw new Error('Документы для Битрикс24 не реализованы — см. TODO в bitrix24Adapter.js');
    },
    async createGeneratedDocument() {
      throw new Error('Документы для Битрикс24 не реализованы — см. TODO в bitrix24Adapter.js');
    },
    async createSmetaDocument() {
      throw new Error('Документы для Битрикс24 не реализованы — см. TODO в bitrix24Adapter.js');
    },
    async createFileDocument() {
      throw new Error('Документы для Битрикс24 не реализованы — см. TODO в bitrix24Adapter.js');
    },
    async deleteDocument() {
      throw new Error('Документы для Битрикс24 не реализованы — см. TODO в bitrix24Adapter.js');
    },
    async getDocumentContent() {
      throw new Error('Документы для Битрикс24 не реализованы — см. TODO в bitrix24Adapter.js');
    },
    async getDocumentFile() {
      throw new Error('Документы для Битрикс24 не реализованы — см. TODO в bitrix24Adapter.js');
    },
  };
}
