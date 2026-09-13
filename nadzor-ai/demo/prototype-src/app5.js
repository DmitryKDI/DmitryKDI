/* ── Классификатор расхождений ─────────────────────────────────────────── */
/** Запасной способ выгрузки: показать данные для копирования. */
function showExport(text) {
  modal('Выгрузка журнала расхождений',
    `<p style="font-size:12.5px; color:var(--ink-muted); margin-bottom:10px">
       Формат CSV с разделителем «;» — открывается в табличном редакторе.</p>
     <textarea id="exportBox" style="height:280px; font-family:'IBM Plex Mono',monospace; font-size:11.5px">${esc(text)}</textarea>
     <div class="btn-row" style="margin-top:10px"><button class="btn primary" id="copyBox">Скопировать</button></div>`);
  $('#copyBox').onclick = async () => {
    try { await navigator.clipboard.writeText(text); toast('Скопировано в буфер обмена'); }
    catch { $('#exportBox').select(); toast('Выделено — скопируйте сочетанием клавиш'); }
  };
}

function viewClassifier() {
  const groups = {};
  DATA.findings.forEach(f => { (groups[f.code] ||= []).push(f); });
  const CLASSES = DATA.classifier;
  return `<div class="card"><header><div><h2>Классификатор расхождений</h2>
    <p>Региональный справочник. Подключение нового субъекта Российской Федерации —
       загрузка нормативного пакета, а не доработка кода.</p></div></header>
    <div class="table-wrap"><table>
      <thead><tr><th>Код</th><th>Группа</th><th>Наименование</th><th>Переходы</th>
        <th>Критичность</th><th>Нормативное основание</th><th>Найдено в демо</th></tr></thead>
      <tbody>${CLASSES.map(c => `<tr>
        <td class="mono" style="font-weight:600">${esc(c.code)}</td>
        <td style="color:var(--ink-muted)">${esc(c.group)}</td>
        <td>${esc(c.title)}</td>
        <td class="mono" style="font-size:11.5px; color:var(--ink-muted)">${esc(c.transitions.join(', '))}</td>
        <td><span class="chip ${c.default_severity}">${SEV[c.default_severity]}</span></td>
        <td style="font-size:12px; color:var(--ink-muted)">${c.norm_refs.map(r => `${esc(r.doc)} п. ${esc(r.clause)}`).join('; ') || '—'}</td>
        <td class="num">${(groups[c.code] || []).length || '—'}</td>
      </tr>`).join('')}</tbody></table></div>
  </div>`;
}

/* ── Журнал аудита: настоящая цепочка SHA-256 ──────────────────────────── */
const AUDIT = [
  { actor:'sudir:77001', role:'inspector', action:'auth.login',        entity:'user',    id:'sudir:77001', payload:{провайдер:'СУДИР'} },
  { actor:'sudir:77001', role:'inspector', action:'analysis.run',      entity:'run',     id:'run-OBJ-001', payload:{объект:'OBJ-001', переходы:'T1,T3,T5'} },
  { actor:'system',      role:'admin',     action:'analysis.completed',entity:'run',     id:'run-OBJ-001', payload:{находок:16, провайдер:'mock'} },
  { actor:'sudir:77001', role:'inspector', action:'finding.assess',    entity:'finding', id:'H-1002',      payload:{вердикт:'confirmed'} },
  { actor:'sudir:77001', role:'inspector', action:'report.act',        entity:'object',  id:'OBJ-001',     payload:{находок:3} },
  { actor:'sudir:77002', role:'head_of_dept', action:'objects.review', entity:'department', id:'ОНЖС-3',   payload:{объектов:10} },
  { actor:'sudir:77005', role:'admin',     action:'admin.provider_changed', entity:'config', id:'providers.yaml', payload:{основной:'gigachat'} },
];
let CHAIN = null;

async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}
async function buildChain() {
  const base = new Date('2026-08-18T09:12:00Z').getTime();
  let prev = '0'.repeat(64);
  const out = [];
  for (let i = 0; i < AUDIT.length; i++) {
    const rec = { seq:i+1, occurred_at:new Date(base + i * 1_140_000).toISOString().slice(0,19) + 'Z',
                  ...AUDIT[i], prev_hash:prev };
    rec.record_hash = await sha256(JSON.stringify(rec));
    prev = rec.record_hash;
    out.push(rec);
  }
  return out;
}
async function verifyChain(records) {
  let prev = '0'.repeat(64);
  for (const rec of records) {
    if (rec.prev_hash !== prev) return { valid:false, at:rec.seq, why:'нарушена связь с предыдущей записью' };
    const { record_hash, ...body } = rec;
    if (await sha256(JSON.stringify(body)) !== record_hash)
      return { valid:false, at:rec.seq, why:'содержимое записи изменено после сохранения' };
    prev = rec.record_hash;
  }
  return { valid:true, count:records.length, head:prev };
}

function viewAudit() {
  if (!CHAIN) return `<div class="card"><div class="empty">Загрузка журнала…</div></div>`;
  return `<div class="card"><header>
    <div><h2>Журнал аудита</h2>
      <p>Записи только добавляются. Каждая хранит хэш предыдущей: изменение любой записи
         обнаруживается проверкой. Хэши в этом прототипе считаются по-настоящему,
         алгоритмом SHA-256 в браузере.</p></div>
    <div class="btn-row">
      <button class="btn sm" id="tamperBtn">${S.tampered ? 'Восстановить запись' : 'Подделать запись № 4'}</button>
      <button class="btn sm primary" id="verifyBtn">Проверить целостность</button>
    </div></header>
    <div class="body" id="verifyResult" style="padding-bottom:0"></div>
    <div class="table-wrap"><table>
      <thead><tr><th>№</th><th>Время</th><th>Кто</th><th>Роль</th><th>Действие</th>
        <th>Объект записи</th><th>Сведения</th><th>Хэш предыдущей</th><th>Хэш записи</th></tr></thead>
      <tbody>${CHAIN.map(r => `<tr${S.tampered && r.seq === 4 ? ' class="stripe critical"' : ''}>
        <td class="num">${r.seq}</td>
        <td class="mono" style="font-size:11.5px">${r.occurred_at.replace('T',' ').replace('Z','')}</td>
        <td class="mono" style="font-size:11.5px">${esc(r.actor)}</td>
        <td><span class="chip neutral">${esc(r.role)}</span></td>
        <td>${esc(r.action)}</td>
        <td style="font-size:12px; color:var(--ink-muted)">${esc(r.entity)} ${esc(r.id)}</td>
        <td style="font-size:12px; color:var(--ink-muted)">${esc(Object.entries(r.payload).map(([k,v]) => k+': '+v).join(', '))}</td>
        <td class="mono" style="font-size:11px; color:var(--ink-faint)">${r.prev_hash.slice(0,14)}…</td>
        <td class="mono" style="font-size:11px; color:var(--ink-faint)">${r.record_hash.slice(0,14)}…</td>
      </tr>`).join('')}</tbody></table></div>
  </div>`;
}

/* ── Отрисовка и события ───────────────────────────────────────────────── */
const VIEWS = {
  dashboard: () => S.role === 'head' ? viewHeadDashboard() : S.role === 'auditor' ? viewAudit() : viewInspectorDashboard(),
  workload: viewHeadDashboard, analysis: viewAnalysis, findings: viewFindings, finding: viewFinding,
  attention: viewAttention, objects: viewObjects, object: viewObject, classifier: viewClassifier, audit: viewAudit,
};
const TITLES = { dashboard:S.role === 'head' ? 'Сводка по отделу' : 'Мои объекты', workload:'Нагрузка инспекторов',
  analysis:'Новый анализ', findings:'Журнал расхождений', finding:'Карточка расхождения',
  attention:'Карта внимания', objects:'Объекты', object:'Карточка объекта',
  classifier:'Классификатор расхождений', audit:'Журнал аудита' };

function render() {
  const role = ROLES[S.role];
  $('#roleName').textContent = role.name;
  $('#rolePos').textContent = role.pos;
  $('#roleAv').textContent = role.short;
  renderNav();
  const title = S.view === 'dashboard' && S.role === 'head' ? 'Сводка по отделу'
              : S.view === 'dashboard' ? 'Мои объекты' : (TITLES[S.view] || '');
  $('#view').innerHTML = `<div class="crumbs"><a href="#" data-go="dashboard">Главная</a><span>/</span>
    <span style="color:var(--ink-muted)">${esc(title)}</span></div>` + (VIEWS[S.view] || VIEWS.dashboard)();
  wire();
}

function wire() {
  document.querySelectorAll('[data-go]').forEach(el => {
    el.onclick = (e) => {
      e.preventDefault();
      const v = el.dataset.go;
      if (el.dataset.obj) { S.obj = el.dataset.obj; S.filters.obj = el.dataset.obj; }
      go(v, el.dataset.id);
    };
  });
  document.querySelectorAll('[data-assess]').forEach(el => {
    el.onclick = () => {
      const [id, verdict] = el.dataset.assess.split(':');
      const prev = S.assessed[id];
      if (verdict === 'none') delete S.assessed[id]; else S.assessed[id] = verdict;
      render();
      if (verdict !== 'none') toast('Оценка сохранена и записана в журнал аудита',
        () => { if (prev) S.assessed[id] = prev; else delete S.assessed[id]; });
    };
  });
  document.querySelectorAll('[data-check]').forEach(el => {
    el.onchange = () => { S.checked[el.dataset.check] = el.checked; render(); };
  });

  const fObj = $('#fObj'), fSev = $('#fSev'), fTr = $('#fTr');
  if (fObj) fObj.onchange = () => { S.filters.obj = fObj.value; S.obj = fObj.value || S.obj; render(); };
  if (fSev) fSev.onchange = () => { S.filters.sev = fSev.value; render(); };
  if (fTr) fTr.onchange = () => { S.filters.tr = fTr.value; render(); };

  const csv = $('#csvBtn');
  if (csv) csv.onclick = () => {
    const head = ['Идентификатор','Код','Переход','Критичность','Уверенность','Приоритет','Описание','Документ','Лист'];
    const rows = visibleFindings().map(f => {
      const e = f.evidence[f.evidence.length-1];
      return [f.public_id, f.code, f.transition, SEV[f.severity], f.confidence, f.priority_score,
              f.title.replace(/;/g, ','), e.doc_title, e.page];
    });
    const text = '\ufeff' + [head, ...rows].map(r => r.join(';')).join('\n');
    // Страница открыта из файла — сохраняем сразу; во встроенном просмотре
    // сохранение недоступно, поэтому показываем данные для копирования.
    const standalone = (() => { try { return window.self === window.top; } catch { return false; } })();
    if (standalone) {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type:'text/csv;charset=utf-8' }));
      a.download = 'rashozhdeniya.csv';
      a.click();
      URL.revokeObjectURL(a.href);
      toast('Выгрузка сохранена');
      return;
    }
    showExport(text);
  };

  const act = $('#actBtn');
  if (act) act.onclick = () => {
    modal('Проект акта проверки', `<p class="note"><b>В работающей системе</b> здесь формируется
      файл DOCX с реквизитами объекта, перечнем подтверждённых расхождений, ссылками на пункты
      нормативов и приложением доказательств с указанием листа и координат области.</p>
      <p style="margin-top:12px; font-size:13px; color:var(--ink-muted)">Документ формируется как
      проект и требует подтверждения должностным лицом: система не совершает юридически
      значимых действий.</p>`);
  };

  const lookup = $('#lookupBtn');
  if (lookup) lookup.onclick = () => {
    const value = $('#permit').value.trim();
    const found = allObjects().find(o => o.permit_number === value);
    if (found) { S.obj = found.id; render(); toast('Карточка объекта заполнена по данным ИАИС ОГД'); }
    else toast('Объект по такому разрешению не найден. Проверьте номер или выберите объект из списка.');
  };
  const objSel = $('#objSel');
  if (objSel) objSel.onchange = () => { S.obj = objSel.value; render(); };

  const kit = $('#demoKit');
  if (kit) kit.onclick = () => {
    S.uploads = { pd:[...DEMO_KIT.pd], rd:[...DEMO_KIT.rd], as_built:[...DEMO_KIT.as_built] };
    render(); toast('Комплект подставлен: 8 файлов');
  };
  document.querySelectorAll('[data-input]').forEach(input => {
    input.onchange = () => {
      const kind = input.dataset.input;
      S.uploads[kind].push(...[...input.files].map(f => ({name:f.name, size:f.size})));
      render(); toast(`Добавлено файлов: ${input.files.length}`);
    };
  });
  document.querySelectorAll('[data-drop]').forEach(btn => {
    btn.onclick = () => {
      const [kind, i] = btn.dataset.drop.split(':');
      S.uploads[kind].splice(Number(i), 1); render();
    };
  });
  document.querySelectorAll('[data-zone]').forEach(zoneEl => {
    const kind = zoneEl.dataset.zone;
    zoneEl.ondragover = (e) => { e.preventDefault(); zoneEl.classList.add('over'); };
    zoneEl.ondragleave = () => zoneEl.classList.remove('over');
    zoneEl.ondrop = (e) => {
      e.preventDefault(); zoneEl.classList.remove('over');
      const files = [...e.dataTransfer.files].map(f => ({name:f.name, size:f.size}));
      S.uploads[kind].push(...files);
      render(); toast(`Добавлено файлов: ${files.length}`);
    };
  });
  document.querySelectorAll('[data-tr]').forEach(cb => {
    cb.onchange = () => {
      const code = cb.dataset.tr;
      S.chosen = cb.checked ? [...new Set([...S.chosen, code])] : S.chosen.filter(c => c !== code);
    };
  });
  const run = $('#runBtn');
  if (run) run.onclick = startAnalysis;

  const verify = $('#verifyBtn');
  if (verify) verify.onclick = async () => {
    const res = await verifyChain(CHAIN);
    $('#verifyResult').innerHTML = res.valid
      ? `<p class="note" style="border-left-color:var(--ok); background:var(--ok-soft)">
         <b>Цепочка целостна.</b> Проверено записей: ${res.count}. Признаков изменения журнала нет.</p>`
      : `<p class="note warn" style="border-left-color:var(--critical); background:var(--critical-soft)">
         <b>Целостность нарушена на записи № ${res.at}.</b> ${esc(res.why)}. Журнал не может
         считаться достоверным до выяснения обстоятельств.</p>`;
  };
  const tamper = $('#tamperBtn');
  if (tamper) tamper.onclick = () => {
    S.tampered = !S.tampered;
    CHAIN[3].payload = S.tampered ? { вердикт:'rejected' } : { вердикт:'confirmed' };
    render();
    $('#verifyResult').innerHTML = `<p class="note warn">Запись изменена в обход системы.
      Нажмите «Проверить целостность», чтобы увидеть результат.</p>`;
  };
}

$('#roleBtn').onclick = () => {
  const order = ['inspector', 'head', 'auditor'];
  S.role = order[(order.indexOf(S.role) + 1) % order.length];
  S.view = S.role === 'auditor' ? 'audit' : 'dashboard';
  S.filters = { sev:'', tr:'', obj:'' };
  render();
  toast(`Роль переключена: ${ROLES[S.role].pos}`);
};
$('#themeBtn').onclick = () => {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : cur === 'light' ? 'dark' : 
    (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', next);
};

buildChain().then(c => { CHAIN = c; if (S.view === 'audit') render(); });
render();
