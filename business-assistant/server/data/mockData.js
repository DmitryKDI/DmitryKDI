// Тестовый демонстрационный набор данных под нишу ремонта/строительства и
// строительно-технической экспертизы. Хранится в памяти процесса — сбрасывается
// при перезапуске сервера. Это ожидаемо для демо-режима (DATA_ADAPTER_MODE=mock).

export const STAGES = [
  'новая заявка',
  'замер/выезд',
  'смета/КП',
  'согласование',
  'в работе',
  'приёмка/сдача',
  'завершено',
];

export const DEAL_TYPES = ['ремонт', 'экспертиза'];

let dealSeq = 10;
let taskSeq = 20;

export const deals = [
  {
    id: 'D-1001',
    address: 'Москва, ул. Профсоюзная, 45, кв. 112',
    clientName: 'Смирнова Ольга Викторовна',
    clientPhone: '+7 (916) 123-45-67',
    type: 'ремонт',
    stage: 'в работе',
    budget: 1850000,
    manager: 'Игорь Костин',
    createdDate: '2026-06-02',
  },
  {
    id: 'D-1002',
    address: 'Москва, Ленинский проспект, 78, кв. 34',
    clientName: 'Ткаченко Павел Сергеевич',
    clientPhone: '+7 (925) 654-32-10',
    type: 'ремонт',
    stage: 'смета/КП',
    budget: 920000,
    manager: 'Марина Величко',
    createdDate: '2026-07-10',
  },
  {
    id: 'D-1003',
    address: 'МО, г. Красногорск, ул. Пионерская, 12',
    clientName: 'ООО "СтройИнвест"',
    clientPhone: '+7 (495) 777-11-22',
    type: 'экспертиза',
    stage: 'в работе',
    budget: 180000,
    manager: 'Игорь Костин',
    createdDate: '2026-07-18',
  },
  {
    id: 'D-1004',
    address: 'Москва, Кутузовский проспект, 26, кв. 9',
    clientName: 'Азарова Елена Дмитриевна',
    clientPhone: '+7 (903) 222-14-88',
    type: 'ремонт',
    stage: 'согласование',
    budget: 3200000,
    manager: 'Марина Величко',
    createdDate: '2026-07-22',
  },
  {
    id: 'D-1005',
    address: 'МО, г. Химки, мкр. Левобережный, 5, кв. 201',
    clientName: 'Родионов Артём Игоревич',
    clientPhone: '+7 (966) 340-19-52',
    type: 'экспертиза',
    stage: 'приёмка/сдача',
    budget: 95000,
    manager: 'Дарья Носова',
    createdDate: '2026-06-28',
  },
  {
    id: 'D-1006',
    address: 'Москва, ул. Новый Арбат, 15, кв. 5',
    clientName: 'Григорян Карен Ашотович',
    clientPhone: '+7 (910) 888-77-66',
    type: 'ремонт',
    stage: 'завершено',
    budget: 2450000,
    manager: 'Игорь Костин',
    createdDate: '2026-05-14',
  },
  {
    id: 'D-1007',
    address: 'МО, г. Балашиха, ул. Свердлова, 22',
    clientName: 'Панфилова Юлия Витальевна',
    clientPhone: '+7 (929) 456-78-90',
    type: 'ремонт',
    stage: 'новая заявка',
    budget: null,
    manager: 'Дарья Носова',
    createdDate: '2026-08-03',
  },
  {
    id: 'D-1008',
    address: 'Москва, Севастопольский проспект, 60, кв. 77',
    clientName: 'ИП Ковалёв Р.Н.',
    clientPhone: '+7 (495) 300-15-40',
    type: 'экспертиза',
    stage: 'новая заявка',
    budget: null,
    manager: 'Марина Величко',
    createdDate: '2026-08-05',
  },
  {
    id: 'D-1009',
    address: 'МО, г. Одинцово, Можайское шоссе, 155',
    clientName: 'Ефремов Станислав Юрьевич',
    clientPhone: '+7 (903) 611-22-09',
    type: 'ремонт',
    stage: 'замер/выезд',
    budget: null,
    manager: 'Дарья Носова',
    createdDate: '2026-08-01',
  },
  {
    id: 'D-1010',
    address: 'Москва, Ходынский бульвар, 4, кв. 140',
    clientName: 'Белякова Наталья Олеговна',
    clientPhone: '+7 (916) 999-33-21',
    type: 'ремонт',
    stage: 'приёмка/сдача',
    budget: 1620000,
    manager: 'Игорь Костин',
    createdDate: '2026-06-20',
  },
  {
    id: 'D-1011',
    address: 'Москва, Пресненская наб., 8, кв. 250',
    clientName: 'Дубровина Ирина Александровна',
    clientPhone: '+7 (925) 111-90-45',
    type: 'ремонт',
    stage: 'завершено',
    budget: 1340000,
    manager: 'Марина Величко',
    createdDate: '2026-07-01',
  },
  {
    id: 'D-1012',
    address: 'МО, г. Мытищи, Олимпийский проспект, 34',
    clientName: 'ООО "ГарантСтройЭксперт"',
    clientPhone: '+7 (495) 665-40-12',
    type: 'экспертиза',
    stage: 'завершено',
    budget: 120000,
    manager: 'Дарья Носова',
    createdDate: '2026-07-15',
  },
];

export const tasks = [
  { id: 'T-2001', dealId: 'D-1001', title: 'Согласовать поставку плитки с клиентом', description: '', dueDate: '2026-08-10', status: 'open' },
  { id: 'T-2002', dealId: 'D-1001', title: 'Приёмка чернового пола бригадой', description: '', dueDate: '2026-08-12', status: 'open' },
  { id: 'T-2003', dealId: 'D-1002', title: 'Подготовить смету', description: 'Смета на черновые + чистовые работы', dueDate: '2026-08-08', status: 'open' },
  { id: 'T-2004', dealId: 'D-1003', title: 'Провести экспертизу и оформить заключение', description: 'Обследование несущих конструкций', dueDate: '2026-08-09', status: 'open' },
  { id: 'T-2005', dealId: 'D-1004', title: 'Согласовать материалы с клиентом', description: '', dueDate: '2026-08-07', status: 'open' },
  { id: 'T-2006', dealId: 'D-1005', title: 'Направить заключение экспертизы клиенту', description: '', dueDate: '2026-08-06', status: 'open' },
  { id: 'T-2007', dealId: 'D-1007', title: 'Выехать на замер', description: '', dueDate: '2026-08-06', status: 'open' },
  { id: 'T-2008', dealId: 'D-1008', title: 'Уточнить объём экспертизы у клиента', description: '', dueDate: '2026-08-07', status: 'open' },
  { id: 'T-2009', dealId: 'D-1009', title: 'Выехать на замер', description: '', dueDate: '2026-08-05', status: 'done' },
  { id: 'T-2010', dealId: 'D-1010', title: 'Финальная приёмка объекта клиентом', description: '', dueDate: '2026-08-04', status: 'open' },
  { id: 'T-2011', dealId: 'D-1006', title: 'Закрывающие документы по объекту', description: '', dueDate: '2026-05-20', status: 'done' },
];

export const callHistory = [
  { id: 'C-3001', dealId: 'D-1002', phone: '+7 (925) 654-32-10', direction: 'исходящий', outcome: 'договорились о встрече', durationSec: 184, date: '2026-08-05' },
  { id: 'C-3002', dealId: 'D-1007', phone: '+7 (929) 456-78-90', direction: 'исходящий', outcome: 'новая заявка подтверждена', durationSec: 96, date: '2026-08-03' },
  { id: 'C-3003', dealId: 'D-1004', phone: '+7 (903) 222-14-88', direction: 'входящий', outcome: 'уточнение по материалам', durationSec: 240, date: '2026-08-02' },
  { id: 'C-3004', dealId: 'D-1005', phone: '+7 (966) 340-19-52', direction: 'исходящий', outcome: 'напоминание о приёмке', durationSec: 58, date: '2026-08-01' },
  { id: 'C-3005', dealId: 'D-1009', phone: '+7 (903) 611-22-09', direction: 'исходящий', outcome: 'замер согласован', durationSec: 132, date: '2026-07-31' },
];

export function nextDealId() {
  dealSeq += 1;
  return `D-1${String(dealSeq).padStart(3, '0')}`;
}

export function nextTaskId() {
  taskSeq += 1;
  return `T-2${String(taskSeq).padStart(3, '0')}`;
}
