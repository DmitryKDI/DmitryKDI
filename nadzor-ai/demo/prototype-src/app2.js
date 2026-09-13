/* ── Навигация ──────────────────────────────────────────────────────────── */
const MENU = {
  inspector: [
    ['Работа инспектора', [['dashboard','Мои объекты'], ['analysis','Новый анализ'],
      ['findings','Журнал расхождений'], ['attention','Карта внимания']]],
    ['Справочники', [['objects','Объекты надзора'], ['classifier','Классификатор расхождений']]],
  ],
  head: [
    ['Управление отделом', [['dashboard','Сводка по отделу'], ['workload','Нагрузка инспекторов'],
      ['findings','Журнал расхождений'], ['objects','Объекты отдела']]],
    ['Справочники', [['classifier','Классификатор расхождений']]],
  ],
  auditor: [
    ['Контроль', [['audit','Журнал аудита']]],
  ],
};

function renderNav() {
  const crit = visibleFindings().filter(f => f.severity === 'critical').length;
  $('#nav').innerHTML = MENU[S.role].map(([group, items]) => `
    <div class="nav-group"><p>${esc(group)}</p>
      ${items.map(([id, label]) => `
        <a href="#" data-go="${id}" class="${S.view === id ? 'on' : ''}">
          <span>${esc(label)}</span>
          ${id === 'findings' && crit ? `<span class="badge">${crit}</span>` : ''}
        </a>`).join('')}
    </div>`).join('');
}

/* ── Дашборд инспектора: его объекты и что горит ────────────────────────── */
function viewInspectorDashboard() {
  const objects = visibleObjects();
  const mine = visibleFindings();
  const crit = mine.filter(f => f.severity === 'critical');
  const analysed = objects.filter(o => analysedIds().includes(o.id));
  const assessed = Object.keys(S.assessed).length;
  const presc = PRESCRIPTIONS.filter(p => p.inspector === ROLES.inspector.id);

  const cards = objects.map(o => {
    const fs = findingsOf(o.id);
    const c = fs.filter(f => f.severity === 'critical').length;
    const has = analysedIds().includes(o.id);
    return `<div class="card stripe ${c ? 'critical' : has ? 'minor' : ''}" style="padding:0">
      <div style="padding:14px 16px">
        <div style="display:flex; gap:10px; align-items:flex-start">
          <div style="flex:1; min-width:0">
            <h3 style="font-size:14px"><a href="#" data-go="object" data-id="${o.id}">${esc(o.name)}</a></h3>
            <p style="font-size:12px; color:var(--ink-muted); margin-top:3px">${esc(o.address)}</p>
            <p style="font-size:12px; color:var(--ink-faint); margin-top:5px">${esc(o.stage || '')}</p>
          </div>
          ${c ? `<span class="chip critical">${c} критич.</span>`
              : has ? `<span class="chip minor">проверено</span>`
                    : `<span class="chip neutral">нет комплекта</span>`}
        </div>
        <div style="display:flex; gap:16px; margin-top:12px; font-size:12px; color:var(--ink-muted)">
          <span>Разрешение <span class="mono">${esc(o.permit_number)}</span></span>
          ${has ? `<span>Расхождений: <b class="num">${fs.length}</b></span>`
                : `<span style="color:var(--ink-faint)">Анализ не выполнялся</span>`}
        </div>
        <div class="btn-row" style="margin-top:12px">
          ${has ? `<button class="btn sm" data-go="findings" data-obj="${o.id}">Расхождения</button>
                   <button class="btn sm" data-go="attention" data-obj="${o.id}">Карта внимания</button>`
                : `<button class="btn sm primary" data-go="analysis" data-obj="${o.id}">Загрузить документацию</button>`}
        </div>
      </div></div>`;
  }).join('');

  return `
  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); margin-bottom:16px">
    <div class="tile"><p class="label">Объектов под надзором</p><p class="value">${objects.length}</p>
      <p class="sub">комплекты загружены по ${analysed.length}</p></div>
    <div class="tile crit"><p class="label">Критических расхождений</p><p class="value">${crit.length}</p>
      <p class="sub">требуют проверки на объекте</p></div>
    <div class="tile"><p class="label">Всего гипотез нарушений</p><p class="value">${mine.length}</p>
      <p class="sub">оценено инспектором: ${assessed}</p></div>
    <div class="tile"><p class="label">Предписаний на контроле</p><p class="value">${presc.filter(p=>p.status==='на контроле').length}</p>
      <p class="sub">ближайший срок ${ruDate(presc[0]?.deadline)}</p></div>
  </div>

  <div class="card" style="margin-bottom:16px"><header>
    <div><h2>Что смотреть в первую очередь</h2>
    <p>Три позиции с наибольшим приоритетом по всем вашим объектам</p></div>
    <button class="btn sm" data-go="attention">Полная карта внимания</button></header>
    <div class="body stack" style="gap:10px">
      ${[...mine].sort((a,b) => b.priority_score - a.priority_score).slice(0,3).map((f,i) => `
        <div class="att">
          <div class="n">${i+1}</div>
          <div style="min-width:0">
            <p style="font-weight:600; font-size:13px">${esc(f.structural_element.name || '')} ${esc(f.structural_element.mark || '')}</p>
            <p style="font-size:12px; color:var(--ink-faint)">${esc(place(f.structural_element))}</p>
            <p style="margin-top:6px">${esc(f.title)}</p>
            <p style="margin-top:6px; font-size:12.5px"><span style="color:var(--ink-faint); text-transform:uppercase; font-size:10.5px">Проверить: </span>${esc(f.field_check)}</p>
          </div>
          <div class="side">
            <span class="chip ${f.severity}">${SEV[f.severity]}</span>
            <span class="mono" style="font-size:11.5px; color:var(--ink-faint)">приоритет ${f.priority_score}</span>
            <button class="btn sm" data-go="finding" data-id="${f.public_id}">Открыть</button>
          </div>
        </div>`).join('')}
    </div>
  </div>

  <div class="card"><header><div><h2>Мои объекты</h2>
    <p>Объекты закреплены за вами в СУДИР; карточки заполнены сведениями из ИАИС ОГД</p></div></header>
    <div class="body"><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">${cards}</div></div>
  </div>`;
}

/* ── Дашборд начальника отдела: у кого сколько ──────────────────────────── */
function viewHeadDashboard() {
  const rows = INSPECTORS.map(ins => {
    const objs = ins.objects;
    const fs = DATA.findings.filter(f => objs.includes(f.object_id));
    const analysed = objs.filter(o => analysedIds().includes(o));
    const presc = PRESCRIPTIONS.filter(p => p.inspector === ins.id);
    return {
      ins, objects: objs.length, analysed: analysed.length,
      findings: fs.length, critical: fs.filter(f => f.severity === 'critical').length,
      control: presc.filter(p => p.status === 'на контроле').length,
      overdue: presc.filter(p => p.status === 'срок истёк').length,
    };
  });
  const maxObjects = Math.max(...rows.map(r => r.objects));
  const totals = rows.reduce((a, r) => ({
    objects:a.objects+r.objects, analysed:a.analysed+r.analysed, findings:a.findings+r.findings,
    critical:a.critical+r.critical, control:a.control+r.control, overdue:a.overdue+r.overdue,
  }), {objects:0,analysed:0,findings:0,critical:0,control:0,overdue:0});

  return `
  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); margin-bottom:16px">
    <div class="tile"><p class="label">Объектов в отделе</p><p class="value">${totals.objects}</p>
      <p class="sub">комплекты загружены по ${totals.analysed}</p></div>
    <div class="tile"><p class="label">Инспекторов</p><p class="value">${INSPECTORS.length}</p>
      <p class="sub">в среднем ${(totals.objects/INSPECTORS.length).toFixed(1)} объекта на человека</p></div>
    <div class="tile crit"><p class="label">Критических расхождений</p><p class="value">${totals.critical}</p>
      <p class="sub">всего гипотез: ${totals.findings}</p></div>
    <div class="tile"><p class="label">Предписаний на контроле</p><p class="value">${totals.control}</p>
      <p class="sub">${totals.overdue ? 'с истёкшим сроком: ' + totals.overdue : 'просроченных нет'}</p></div>
  </div>

  <div class="card" style="margin-bottom:16px"><header>
    <div><h2>Нагрузка инспекторов</h2>
    <p>Сколько объектов у каждого, по скольким загружены комплекты и что найдено</p></div>
    <button class="btn sm no-print" onclick="window.print()">Печать</button></header>
    <div class="table-wrap"><table class="workload">
      <thead><tr>
        <th>Инспектор</th><th>Объектов</th><th>Проанализировано</th><th>Гипотез</th>
        <th>Критических</th><th>Предписаний на контроле</th><th>Распределение</th>
      </tr></thead>
      <tbody>${rows.map(r => `
        <tr class="stripe ${r.overdue ? 'critical' : ''}">
          <td><b>${esc(r.ins.name)}</b><br><span class="mono" style="font-size:11px; color:var(--ink-faint)">${esc(r.ins.id)}</span></td>
          <td class="num">${r.objects}</td>
          <td class="num">${r.analysed}${r.analysed < r.objects ? `<span style="color:var(--ink-faint)"> из ${r.objects}</span>` : ''}</td>
          <td class="num">${r.findings || '—'}</td>
          <td class="num" style="font-weight:600; color:${r.critical ? 'var(--critical)' : 'inherit'}">${r.critical || '—'}</td>
          <td class="num">${r.control}${r.overdue ? ` <span class="chip critical">просрочено ${r.overdue}</span>` : ''}</td>
          <td><div class="barcell"><span class="track"><span style="width:${r.objects/maxObjects*100}%; background:var(--accent)"></span></span></div></td>
        </tr>`).join('')}
      </tbody>
    </table></div>
  </div>

  <div class="row">
    <div class="card" style="flex:2; min-width:320px"><header><div><h2>Объекты отдела</h2>
      <p>Сортировка по числу критических расхождений</p></div></header>
      <div class="table-wrap"><table>
        <thead><tr><th>Объект</th><th>Инспектор</th><th>Этап</th><th>Критич.</th><th>Гипотез</th></tr></thead>
        <tbody>${allObjects().map(o => {
          const fs = findingsOf(o.id);
          const c = fs.filter(f => f.severity === 'critical').length;
          const ins = INSPECTORS.find(i => i.objects.includes(o.id));
          return { o, fs, c, ins };
        }).sort((a,b) => b.c - a.c).map(({o,fs,c,ins}) => `
          <tr class="clickable stripe ${c ? 'critical' : ''}" data-go="object" data-id="${o.id}">
            <td><b>${esc(o.name)}</b><br><span style="font-size:11.5px; color:var(--ink-faint)">${esc(o.address)}</span></td>
            <td>${esc(ins ? ins.name : '—')}</td>
            <td style="color:var(--ink-muted)">${esc(o.stage || '—')}</td>
            <td class="num" style="color:${c ? 'var(--critical)' : 'inherit'}; font-weight:${c?600:400}">${c || '—'}</td>
            <td class="num">${fs.length || '—'}</td>
          </tr>`).join('')}
        </tbody></table></div>
    </div>

    <div class="card" style="flex:1; min-width:280px"><header><div><h2>Предписания</h2>
      <p>Сроки устранения и состояние</p></div></header>
      <div class="body stack" style="gap:10px">
        ${PRESCRIPTIONS.map(p => {
          const o = objById(p.object);
          const tone = p.status === 'срок истёк' ? 'critical' : p.status === 'устранено' ? 'minor' : 'major';
          return `<div style="border:1px solid var(--line); border-radius:8px; padding:10px 12px">
            <div style="display:flex; justify-content:space-between; gap:8px">
              <b style="font-size:12.5px">${esc(p.number)}</b><span class="chip ${tone}">${esc(p.status)}</span>
            </div>
            <p style="font-size:12px; color:var(--ink-muted); margin-top:4px">${esc(o ? o.name : p.object)}</p>
            <p style="font-size:12px; color:var(--ink-faint); margin-top:2px">Срок: ${ruDate(p.deadline)}</p>
          </div>`;
        }).join('')}
      </div>
    </div>
  </div>`;
}
