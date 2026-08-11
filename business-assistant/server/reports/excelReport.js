import ExcelJS from 'exceljs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const REPORTS_DIR = path.join(__dirname, '..', 'generated-reports');

const COLUMN_DEFS = {
  id: { header: 'ID сделки', key: 'id', width: 12 },
  address: { header: 'Объект / адрес', key: 'address', width: 40 },
  clientName: { header: 'Клиент', key: 'clientName', width: 28 },
  clientPhone: { header: 'Телефон', key: 'clientPhone', width: 18 },
  type: { header: 'Тип работ', key: 'type', width: 14 },
  stage: { header: 'Этап', key: 'stage', width: 16 },
  budget: { header: 'Бюджет, ₽', key: 'budget', width: 16 },
  manager: { header: 'Менеджер', key: 'manager', width: 20 },
  createdDate: { header: 'Дата создания', key: 'createdDate', width: 16 },
};

const DEFAULT_FIELDS = ['address', 'clientName', 'type', 'stage', 'budget', 'manager', 'createdDate'];

/**
 * Формирует .xlsx с данными по сделкам и сохраняет в REPORTS_DIR.
 * @param {object[]} deals
 * @param {string[]} [fields] — какие колонки включить (по умолчанию DEFAULT_FIELDS)
 * @returns {{ filename: string, filePath: string }}
 */
export async function generateDealsExcelReport(deals, fields) {
  const chosenFields = (fields && fields.length ? fields : DEFAULT_FIELDS).filter((f) => COLUMN_DEFS[f]);
  const columns = (chosenFields.length ? chosenFields : DEFAULT_FIELDS).map((f) => COLUMN_DEFS[f]);

  const workbook = new ExcelJS.Workbook();
  workbook.creator = 'ИИ-бизнес-ассистент (демо)';
  workbook.created = new Date();

  const sheet = workbook.addWorksheet('Сделки');
  sheet.columns = columns;
  sheet.getRow(1).font = { bold: true };
  sheet.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFE8ECF7' } };

  for (const deal of deals) {
    sheet.addRow(deal);
  }

  const budgetColIndex = columns.findIndex((c) => c.key === 'budget');
  if (budgetColIndex !== -1) {
    sheet.getColumn(budgetColIndex + 1).numFmt = '#,##0 ₽';
  }

  const filename = `report-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.xlsx`;
  const filePath = path.join(REPORTS_DIR, filename);
  await workbook.xlsx.writeFile(filePath);

  return { filename, filePath };
}
