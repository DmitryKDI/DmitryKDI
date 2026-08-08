// Генерация содержимого документов по объекту (сделке). Портировано из
// клиентского демо-прототипа (business-assistant-demo.html) — та же логика
// расчётов и текстовых шаблонов, здесь используется сервером при
// DATA_ADAPTER_MODE=sqlite (и в mock-режиме — см. mockAdapter.js).

function csvEscape(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function money(n) {
  if (n === null || n === undefined) return '—';
  return new Intl.NumberFormat('ru-RU').format(n) + ' ₽';
}

// Расчёт сметы по замерам: базовые ставки, руб/м² (уровень «стандарт»), и
// множители уровня отделки. Цифры — демо-калибровка под тестовые бюджеты
// сделок, не прайс-лист реальной компании.
const SMETA_RATES = [
  { key: 'demontage', label: 'Демонтаж', rate: 450 },
  { key: 'rough', label: 'Черновые работы', rate: 3800 },
  { key: 'engineering', label: 'Инженерные системы', rate: 2400 },
  { key: 'finish', label: 'Чистовая отделка', rate: 5200 },
  { key: 'cleanup', label: 'Уборка и сдача объекта', rate: 250 },
];

export const SMETA_LEVELS = [
  { key: 'econom', label: 'Эконом', mult: 0.75 },
  { key: 'standard', label: 'Стандарт', mult: 1 },
  { key: 'premium', label: 'Премиум', mult: 1.5 },
];

export function computeSmetaBreakdown(area, levelKey) {
  const level = SMETA_LEVELS.find((l) => l.key === levelKey) || SMETA_LEVELS[1];
  const rows = SMETA_RATES.map((r) => {
    const rate = Math.round(r.rate * level.mult);
    return { label: r.label, rate, subtotal: Math.round(rate * area) };
  });
  const total = rows.reduce((sum, r) => sum + r.subtotal, 0);
  return { level, rows, total };
}

export function generateKpContent(deal) {
  const budget = deal.budget || 500000;
  const rows = [
    ['Материалы', Math.round(budget * 0.4)],
    ['Работы бригады', Math.round(budget * 0.45)],
    ['Логистика и прочее', Math.round(budget * 0.15)],
  ];
  const lines = [
    'Коммерческое предложение',
    `Объект,${csvEscape(deal.address)}`,
    `Клиент,${csvEscape(deal.clientName)}`,
    `Тип работ,${deal.type}`,
    `Дата,${new Date().toLocaleDateString('ru-RU')}`,
    '',
    'Статья,Сумма',
  ];
  rows.forEach(([label, sum]) => lines.push(`${label},${sum}`));
  lines.push(`Итого,${budget}`);
  return '﻿' + lines.join('\n');
}

export function generateSmetaContent(deal, area, breakdown) {
  const lines = [
    'Смета по замерам',
    `Объект,${csvEscape(deal.address)}`,
    `Клиент,${csvEscape(deal.clientName)}`,
    `Площадь, м²,${area}`,
    `Уровень отделки,${csvEscape(breakdown.level.label)}`,
    `Дата,${new Date().toLocaleDateString('ru-RU')}`,
    '',
    'Этап работ,Ставка руб/м²,Площадь м²,Сумма',
  ];
  breakdown.rows.forEach((r) => lines.push(`${csvEscape(r.label)},${r.rate},${area},${r.subtotal}`));
  lines.push(`Итого,,,${breakdown.total}`);
  return '﻿' + lines.join('\n');
}

export function generateContractContent(deal) {
  const budget = deal.budget || 500000;
  const today = new Date().toLocaleDateString('ru-RU');
  const lines = [
    `ДОГОВОР ПОДРЯДА № ${deal.id}-${new Date().getFullYear()}`,
    `г. Москва                                                          ${today}`,
    '',
    'Исполнитель: ООО «Ремонт-Ассистент» (демо-реквизиты — заменить на реальные при внедрении)',
    `Заказчик: ${deal.clientName}, тел. ${deal.clientPhone}`,
    `Объект: ${deal.address}`,
    `Вид работ: ${deal.type}`,
    '',
    '1. Предмет договора',
    'Исполнитель обязуется выполнить работы на объекте, указанном выше, в соответствии со сметой, а Заказчик обязуется принять и оплатить работы.',
    '',
    '2. Стоимость и порядок расчётов',
    `Общая стоимость работ по договору составляет ${money(budget)}.`,
    'Порядок оплаты: 30% аванс, 40% по завершении черновых работ, 30% по сдаче объекта.',
    '',
    '3. Сроки выполнения',
    'Срок выполнения работ определяется графиком, согласованным сторонами дополнительно.',
    '',
    'Исполнитель: _______________________',
    'Заказчик: _______________________',
  ];
  return '﻿' + lines.join('\n');
}

export function generateActContent(deal) {
  const today = new Date().toLocaleDateString('ru-RU');
  const lines = [
    'АКТ ПРИЁМКИ ВЫПОЛНЕННЫХ РАБОТ',
    `к договору по объекту: ${deal.address}`,
    `Дата составления: ${today}`,
    '',
    `Заказчик: ${deal.clientName}`,
    'Исполнитель: ООО «Ремонт-Ассистент» (демо-реквизиты — заменить на реальные при внедрении)',
    '',
    'Работы выполнены в полном объёме, в соответствии со сметой и согласованным перечнем.',
    'Претензий по объёму, качеству и срокам выполнения работ Заказчик не имеет.',
    '',
    `Сумма по акту: ${money(deal.budget)}`,
    '',
    'Исполнитель: _______________________',
    'Заказчик: _______________________',
  ];
  return '﻿' + lines.join('\n');
}

export function generateInvoiceContent(deal) {
  const today = new Date().toLocaleDateString('ru-RU');
  const budget = deal.budget || 500000;
  const lines = [
    `СЧЁТ НА ОПЛАТУ № ${deal.id}`,
    `от ${today}`,
    '',
    'Поставщик: ООО «Ремонт-Ассистент» (демо-реквизиты — заменить на реальные при внедрении)',
    `Плательщик: ${deal.clientName}`,
    `Объект: ${deal.address}`,
    '',
    `Наименование: работы по договору (${deal.type})`,
    `Сумма: ${money(budget)}`,
    '',
    `Итого к оплате: ${money(budget)}`,
    '',
    'Счёт действителен 5 рабочих дней.',
  ];
  return '﻿' + lines.join('\n');
}

// Типы документов, генерируемых мгновенно (без вложений) — по одной кнопке.
export const GENERATED_DOC_TYPES = {
  kp: { title: 'КП', ext: 'csv', generate: generateKpContent },
  contract: { title: 'Договор', ext: 'txt', generate: generateContractContent },
  act: { title: 'Акт приёмки', ext: 'txt', generate: generateActContent },
  invoice: { title: 'Счёт на оплату', ext: 'txt', generate: generateInvoiceContent },
};

// Типы документов, для которых обязательно нужно прикрепить хотя бы один файл.
export const FILE_DOC_TYPES = {
  design: { title: 'Дизайн-проект', status: 'на согласовании' },
  other: { title: 'Другой документ', status: 'загружен' },
};
