import { createMockAdapter } from './mockAdapter.js';
import { createBitrix24Adapter } from './bitrix24Adapter.js';

/**
 * Единый интерфейс адаптера данных. И ассистент (function calling), и роуты
 * дашборда/продаж работают ТОЛЬКО через этот интерфейс — никогда не обращаются
 * к мок-данным или к Битрикс24 напрямую. Переключение реализации — через
 * DATA_ADAPTER_MODE в .env, без изменения промптов/схем/UI.
 *
 * Интерфейс (реализуют оба провайдера — mockAdapter.js и bitrix24Adapter.js):
 *   listDeals({ stage?, type?, manager?, ids? })      -> Deal[]
 *   getDeal(id)                                        -> Deal
 *   updateDealStage(id, stage)                         -> Deal
 *   listTasks({ dealId?, status? })                    -> Task[]
 *   createTask({ dealId, title, description?, dueDate?}) -> Task
 *   deleteTask(id)                                      -> Task
 *   listCallHistory()                                   -> CallRecord[]
 *   getDashboardMetrics()                                -> Metrics
 *   listManagers()                                       -> string[]
 *   getSalesOverview({ periodDays? })                    -> { periodDays, ranking[] }
 *   getManagerContacts(managerName)                      -> Contact[]
 */
export function createDataAdapter() {
  const mode = (process.env.DATA_ADAPTER_MODE || 'mock').toLowerCase();
  if (mode === 'bitrix24') {
    return createBitrix24Adapter();
  }
  if (mode !== 'mock') {
    console.warn(`Неизвестный DATA_ADAPTER_MODE="${mode}", использую mock`);
  }
  return createMockAdapter();
}
