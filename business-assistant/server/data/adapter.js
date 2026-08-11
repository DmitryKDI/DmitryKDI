import { createMockAdapter } from './mockAdapter.js';
import { createBitrix24Adapter } from './bitrix24Adapter.js';
import { createDbAdapter } from './dbAdapter.js';

/**
 * Единый интерфейс адаптера данных. И ассистент (function calling), и роуты
 * дашборда/продаж/документов работают ТОЛЬКО через этот интерфейс — никогда
 * не обращаются к мок-данным, SQLite или Битрикс24 напрямую. Переключение
 * реализации — через DATA_ADAPTER_MODE в .env, без изменения промптов/схем/UI.
 *
 * Интерфейс (реализуют все три провайдера — mockAdapter.js, dbAdapter.js,
 * bitrix24Adapter.js; последний — с TODO-заглушками для документов):
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
 *
 *   -- Документы объекта (см. README → "Хранение документов и база данных") --
 *   listDocuments(dealId)                                -> Document[]
 *   createGeneratedDocument({ dealId, type })             -> Document   // type: kp|contract|act|invoice
 *   createSmetaDocument({ dealId, area, level })          -> Document   // level: econom|standard|premium
 *   createFileDocument({ dealId, type, files })           -> Document   // type: design|other; files: multer[]
 *   deleteDocument(id)                                    -> Document
 *   getDocumentContent(id)                                -> { filename, data: string }  // сгенерированный текст/CSV
 *   getDocumentFile(fileId)                               -> { filename, mimeType, data: Buffer } // загруженный файл
 */
export function createDataAdapter() {
  const mode = (process.env.DATA_ADAPTER_MODE || 'mock').toLowerCase();
  if (mode === 'bitrix24') {
    return createBitrix24Adapter();
  }
  if (mode === 'sqlite') {
    return createDbAdapter();
  }
  if (mode !== 'mock') {
    console.warn(`Неизвестный DATA_ADAPTER_MODE="${mode}", использую mock`);
  }
  return createMockAdapter();
}
