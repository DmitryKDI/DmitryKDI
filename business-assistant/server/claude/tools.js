import { STAGES, DEAL_TYPES } from '../data/mockData.js';
import { generateDealsExcelReport } from '../reports/excelReport.js';

export const tools = [
  {
    name: 'get_deals_status',
    description:
      'Получить список сделок (объектов) с их статусом: этап, тип работ, бюджет, ' +
      'ответственный менеджер. Можно отфильтровать по этапу/типу/менеджеру. ' +
      'Используй для вопросов "что по сделкам", "сколько объектов в работе", ' +
      '"какие сделки у менеджера X" и т.п.',
    input_schema: {
      type: 'object',
      properties: {
        stage: { type: 'string', enum: STAGES, description: 'Фильтр по этапу сделки' },
        type: { type: 'string', enum: DEAL_TYPES, description: 'Фильтр по типу работ' },
        manager: { type: 'string', description: 'Фильтр по имени ответственного менеджера' },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'list_tasks',
    description:
      'Получить список задач. Можно отфильтровать по сделке (deal_id) и/или статусу ' +
      '(открыта/выполнена). Используй для "какие у меня задачи", "что нужно сделать по объекту X".',
    input_schema: {
      type: 'object',
      properties: {
        deal_id: { type: 'string', description: 'ID сделки, например D-1001' },
        status: { type: 'string', enum: ['open', 'done'], description: 'Статус задачи' },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'create_task',
    description:
      'Создать новую задачу, привязанную к сделке. Обязательно нужен deal_id — если ' +
      'пользователь не назвал сделку явно, сначала уточни, к какому объекту привязать задачу.',
    input_schema: {
      type: 'object',
      properties: {
        deal_id: { type: 'string', description: 'ID сделки, например D-1001' },
        title: { type: 'string', description: 'Краткое название задачи' },
        description: { type: 'string', description: 'Подробности задачи (опционально)' },
        due_date: { type: 'string', description: 'Срок в формате YYYY-MM-DD (опционально)' },
      },
      required: ['deal_id', 'title'],
      additionalProperties: false,
    },
  },
  {
    name: 'delete_task',
    description: 'Удалить задачу по её ID (например T-2001).',
    input_schema: {
      type: 'object',
      properties: {
        task_id: { type: 'string', description: 'ID задачи, например T-2001' },
      },
      required: ['task_id'],
      additionalProperties: false,
    },
  },
  {
    name: 'update_deal_stage',
    description: 'Изменить этап сделки (объекта) на один из допустимых этапов.',
    input_schema: {
      type: 'object',
      properties: {
        deal_id: { type: 'string', description: 'ID сделки, например D-1001' },
        stage: { type: 'string', enum: STAGES, description: 'Новый этап сделки' },
      },
      required: ['deal_id', 'stage'],
      additionalProperties: false,
    },
  },
  {
    name: 'generate_excel_report',
    description:
      'Сформировать Excel-отчёт (.xlsx) по сделкам — для КП или отчёта о ходе работ. ' +
      'Если deal_ids не указаны — отчёт будет по всем сделкам, подходящим под фильтр stage/type. ' +
      'Вызывай, когда пользователь просит "выгрузить", "сформировать отчёт", "прислать эксель", "сделай КП".',
    input_schema: {
      type: 'object',
      properties: {
        deal_ids: {
          type: 'array',
          items: { type: 'string' },
          description: 'Конкретные ID сделок для отчёта (опционально)',
        },
        stage: { type: 'string', enum: STAGES, description: 'Фильтр по этапу, если deal_ids не заданы' },
        type: { type: 'string', enum: DEAL_TYPES, description: 'Фильтр по типу работ, если deal_ids не заданы' },
        fields: {
          type: 'array',
          items: {
            type: 'string',
            enum: ['id', 'address', 'clientName', 'clientPhone', 'type', 'stage', 'budget', 'manager', 'createdDate'],
          },
          description: 'Какие колонки включить в отчёт (опционально, по умолчанию — основной набор)',
        },
      },
      additionalProperties: false,
    },
  },
];

/**
 * Выполняет вызов инструмента поверх адаптера данных. Возвращает JSON-совместимый
 * объект (уходит в tool_result как строка) плюс, для generate_excel_report, кладёт
 * метаданные отчёта в reportsCollector — так фронт получает ссылку на скачивание
 * независимо от того, упомянет ли модель её текстом.
 */
export async function executeTool(name, input, adapter, reportsCollector) {
  switch (name) {
    case 'get_deals_status':
      return adapter.listDeals({ stage: input.stage, type: input.type, manager: input.manager });

    case 'list_tasks':
      return adapter.listTasks({ dealId: input.deal_id, status: input.status });

    case 'create_task':
      return adapter.createTask({
        dealId: input.deal_id,
        title: input.title,
        description: input.description,
        dueDate: input.due_date,
      });

    case 'delete_task':
      return adapter.deleteTask(input.task_id);

    case 'update_deal_stage':
      return adapter.updateDealStage(input.deal_id, input.stage);

    case 'generate_excel_report': {
      const deals = await adapter.listDeals({
        ids: input.deal_ids,
        stage: input.stage,
        type: input.type,
      });
      const { filename } = await generateDealsExcelReport(deals, input.fields);
      const report = { reportUrl: `/api/reports/${filename}`, filename, dealCount: deals.length };
      reportsCollector?.push(report);
      return report;
    }

    default:
      throw new Error(`Неизвестный инструмент: ${name}`);
  }
}
