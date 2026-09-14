const DATA = JSON.parse(document.getElementById('appdata').textContent);
const SHEETS = JSON.parse(document.getElementById('appsheets').textContent);

/* ── Роли. Полномочия приходят из СУДИР и отображаются на внутреннюю модель ── */
const ROLES = {
  inspector: { id:'sudir:77001', name:'Кузнецова Марина Викторовна', pos:'Главный инспектор',
               dept:'Отдел надзора за жилищным строительством № 3', short:'КМ' },
  head:      { id:'sudir:77002', name:'Ерофеев Павел Сергеевич', pos:'Начальник отдела',
               dept:'Отдел надзора за жилищным строительством № 3', short:'ЕП' },
  auditor:   { id:'sudir:77006', name:'Шевчук Ольга Николаевна', pos:'Главный специалист',
               dept:'Контрольно-ревизионный отдел', short:'ШО' },
};

/* ── Инспекторы отдела и закреплённые объекты ───────────────────────────────
   Объекты без загруженного комплекта показаны честно: по ним анализ не
   выполнялся, поэтому гипотез нет.                                          */
const INSPECTORS = [
  { id:'sudir:77001', name:'Кузнецова М. В.', objects:['OBJ-001','OBJ-003','OBJ-004'] },
  { id:'sudir:77007', name:'Абдулов Р. М.',   objects:['OBJ-002','OBJ-005','OBJ-010'] },
  { id:'sudir:77008', name:'Соловьёв И. Н.',  objects:['OBJ-006','OBJ-007'] },
  { id:'sudir:77009', name:'Терехова А. Б.',  objects:['OBJ-008','OBJ-009'] },
];

const EXTRA_OBJECTS = [
  { id:'OBJ-004', name:'Жилой комплекс «Северное сияние», корпус 1', address:'г. Москва, ул. Дубнинская, вл. 40А',
    permit_number:'77-123457-012399-2024', stage:'Устройство фундаментной плиты',
    contractor:'ООО «СтройМонолитИнвест»', assigned_to:'sudir:77001', planned_completion:'2027-04-30' },
  { id:'OBJ-005', name:'Дошкольная образовательная организация на 250 мест', address:'г. Москва, поселение Сосенское, кв. 12',
    permit_number:'77-234568-023477-2024', stage:'Внутренние отделочные работы',
    contractor:'АО «Западный строительный трест»', assigned_to:'sudir:77007', planned_completion:'2026-06-30' },
  { id:'OBJ-006', name:'Административно-деловой центр с подземной автостоянкой', address:'г. Москва, Пресненская наб., вл. 14',
    permit_number:'77-456789-045678-2023', stage:'Монтаж навесного фасада',
    contractor:'ООО «ГрадСтройПроект»', assigned_to:'sudir:77008', planned_completion:'2026-11-15' },
  { id:'OBJ-007', name:'Реконструкция городской поликлиники № 218', address:'г. Москва, ул. Академика Комарова, д. 5, к. 1',
    permit_number:'77-456790-045690-2024', stage:'Усиление несущих конструкций',
    contractor:'ООО «РеставрацияСтрой»', assigned_to:'sudir:77008', planned_completion:'2026-03-20' },
  { id:'OBJ-008', name:'Многофункциональный комплекс с апартаментами', address:'г. Москва, Волгоградский просп., вл. 32',
    permit_number:'77-567890-056789-2024', stage:'Возведение надземной части, 8–9 этажи',
    contractor:'ООО «ПромСтройКомплект»', assigned_to:'sudir:77009', planned_completion:'2027-09-01' },
  { id:'OBJ-009', name:'Многоквартирный жилой дом с подземной автостоянкой', address:'г. Москва, ул. Лобачевского, вл. 118',
    permit_number:'77-567891-056801-2024', stage:'Устройство внутренних инженерных систем',
    contractor:'ООО «СтройМонолитИнвест»', assigned_to:'sudir:77009', planned_completion:'2026-12-25' },
  { id:'OBJ-010', name:'Физкультурно-оздоровительный комплекс с бассейном', address:'г. Москва, ул. Плещеева, вл. 8',
    permit_number:'77-234569-023488-2023', stage:'Пусконаладочные работы',
    contractor:'АО «Западный строительный трест»', assigned_to:'sudir:77007', planned_completion:'2025-11-30' },
];

/* Предписания на контроле — из истории проверок ИАИС ОГД. */
const PRESCRIPTIONS = [
  { object:'OBJ-001', number:'№ 2094/24 от 30.09.2024', deadline:'2024-11-15', status:'на контроле', inspector:'sudir:77001' },
  { object:'OBJ-003', number:'№ 0712/25 от 06.05.2025', deadline:'2025-06-16', status:'на контроле', inspector:'sudir:77001' },
  { object:'OBJ-006', number:'№ 1455/24 от 12.08.2024', deadline:'2024-09-30', status:'срок истёк', inspector:'sudir:77008' },
  { object:'OBJ-002', number:'№ 1802/24 от 26.08.2024', deadline:'2024-09-30', status:'устранено', inspector:'sudir:77007' },
  { object:'OBJ-009', number:'№ 0330/25 от 18.03.2025', deadline:'2025-04-28', status:'на контроле', inspector:'sudir:77009' },
];

const TRANSITIONS = {
  T1:'ПД ред. N → ПД ред. N+1', T2:'ПД → заключение экспертизы', T3:'ПД → РД',
  T4:'РД ред. N → РД ред. N+1', T5:'РД → ИД', T6:'ИД → ИД', T7:'Состояние → нормативная база',
};
const SEV = { critical:'Критическое', major:'Существенное', minor:'Незначительное' };

const S = {
  role:'inspector', view:'dashboard', obj:'OBJ-001', finding:null,
  assessed:{}, checked:{}, filters:{sev:'', tr:''},
  uploads:{ pd:[], rd:[], as_built:[] }, chosen:['T1','T3','T5'], analysing:false, stage:-1,
  tampered:false,
};

/* ── Вспомогательное ───────────────────────────────────────────────────── */
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const $ = (sel, root=document) => root.querySelector(sel);
const objById = (id) => DATA.objects.find(o => o.id === id) || EXTRA_OBJECTS.find(o => o.id === id);
const allObjects = () => [...DATA.objects, ...EXTRA_OBJECTS];
const analysedIds = () => DATA.objects.map(o => o.id);

function visibleObjects() {
  if (S.role === 'inspector') return allObjects().filter(o => (o.assigned_to || inspectorOf(o.id)) === ROLES.inspector.id);
  if (S.role === 'head') return allObjects();
  return [];
}
function inspectorOf(objectId) {
  const rec = INSPECTORS.find(i => i.objects.includes(objectId));
  return rec ? rec.id : '';
}
function findingsOf(objectId) {
  return DATA.findings.filter(f => f.object_id === objectId);
}
function visibleFindings() {
  const ids = new Set(visibleObjects().map(o => o.id));
  return DATA.findings.filter(f => ids.has(f.object_id));
}
function ruDate(iso) {
  if (!iso || iso === 'None') return '—';
  const [y,m,d] = iso.split('-');
  return d ? `${d}.${m}.${y}` : iso;
}
function place(el) {
  return [el.mark, el.axes && `оси ${el.axes}`, el.level && `отм. ${el.level}`, el.section]
    .filter(p => p && p !== '—' && p !== 'оси —' && p !== 'отм. —').join(', ');
}
function toast(text, undo) {
  const host = $('#toast');
  const box = document.createElement('div');
  box.innerHTML = esc(text) + (undo ? ' <button class="btn sm" style="margin-left:8px">Отменить</button>' : '');
  if (undo) box.querySelector('button').onclick = () => { undo(); box.remove(); render(); };
  host.append(box);
  setTimeout(() => box.remove(), 5200);
}
function go(view, param) {
  S.view = view;
  if (view === 'finding') S.finding = param;
  if (view === 'object') S.obj = param;
  window.scrollTo(0, 0);
  render();
}
function modal(title, html) {
  $('#modalHost').innerHTML =
    `<div class="modal" id="mdl"><div class="win"><header><h2>${esc(title)}</h2>
     <button class="btn sm" onclick="closeModal()">Закрыть</button></header>
     <div style="padding:16px">${html}</div></div></div>`;
  $('#mdl').addEventListener('click', (e) => { if (e.target.id === 'mdl') closeModal(); });
}
function closeModal(){ $('#modalHost').innerHTML = ''; }
window.closeModal = closeModal;

/* ── Лист документа с подсветкой области факта ──────────────────────────── */
function sheetHtml(ev) {
  const key = `${ev.doc_title}|${ev.page}`;
  const img = SHEETS[key];
  if (!img) return `<div class="empty">Лист документа недоступен в прототипе.</div>`;
  const k = 100 / 72;                       // лист отрисован при 100 точках на дюйм
  const [x0, y0, x1, y1] = ev.bbox;
  const st = `left:${(x0*k/img.w*100).toFixed(3)}%; top:${(y0*k/img.h*100).toFixed(3)}%;`
           + `width:${((x1-x0)*k/img.w*100).toFixed(3)}%; height:${((y1-y0)*k/img.h*100).toFixed(3)}%;`;
  return `<div class="sheet"><img src="${img.src}" alt="${esc(ev.doc_title)}, лист ${ev.page}">
          <span class="hl" style="${st}"></span></div>
          <p class="legend">${esc(ev.doc_title)} · лист ${ev.page} · извлечено: <b>${esc(ev.extracted)}</b></p>`;
}
window.openSheet = (findingId, index) => {
  const f = DATA.findings.find(x => x.public_id === findingId);
  const ev = f.evidence[index];
  modal(`${ev.doc_title}, лист ${ev.page}`, sheetHtml(ev));
};
