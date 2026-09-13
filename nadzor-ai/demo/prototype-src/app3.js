/* ── Новый анализ: объект → загрузка → переходы → прогресс ──────────────── */
const STAGES = ['Распознавание документов', 'Извлечение сущностей и координат',
  'Сравнение состояний', 'Сверка с нормативной базой', 'Перепроверка второй моделью', 'Готово'];

const DEMO_KIT = {
  pd: [{name:'2024-15-КР_изм2.pdf', size:71680}, {name:'2024-15-КР_изм3.pdf', size:75776}],
  rd: [{name:'2024-15-КЖ_изм1.pdf', size:69632}],
  as_built: [{name:'АОСР_42-53_комплект.pdf', size:412672}, {name:'Паспорта_качества.pdf', size:98304},
             {name:'Протоколы_испытаний.pdf', size:71680}, {name:'Общий_журнал_работ.pdf', size:53248},
             {name:'Выписка_СРО.pdf', size:38912}],
};

function zone(kind, title, hint) {
  const files = S.uploads[kind];
  return `<div class="dropzone" data-zone="${kind}">
      <h3>${esc(title)}</h3>
      <p>${esc(hint)}</p>
      <div class="btn-row" style="justify-content:center; margin-top:10px">
        <label class="btn sm">Выбрать файлы<input type="file" multiple hidden data-input="${kind}"></label>
      </div>
      ${files.length ? `<ul class="filelist">${files.map((f,i) => `
        <li><span class="chip accent">${kind === 'pd' ? 'ПД' : kind === 'rd' ? 'РД' : 'ИД'}</span>
            <span class="name">${esc(f.name)}</span>
            <span class="size">${(f.size/1024).toFixed(0)} КБ</span>
            <button class="btn sm" data-drop="${kind}:${i}">Убрать</button></li>`).join('')}</ul>` : ''}
    </div>`;
}

function viewAnalysis() {
  const o = objById(S.obj);
  const has = analysedIds().includes(S.obj);
  const ready = S.uploads.pd.length && (S.uploads.rd.length || S.uploads.as_built.length);
  return `
  <div class="row" style="align-items:flex-start">
    <div class="stack" style="flex:2; min-width:340px">

      <div class="card"><header><div><h2>1. Объект капитального строительства</h2>
        <p>Введите номер разрешения — карточка заполнится из ИАИС ОГД. Объект можно выбрать
           и из закреплённых за вами в СУДИР.</p></div></header>
        <div class="body">
          <label class="field" for="permit">Номер разрешения на строительство <i>*</i></label>
          <div style="display:flex; gap:8px; flex-wrap:wrap">
            <input type="text" id="permit" placeholder="77-123456-012345-2024"
                   value="${esc(o ? o.permit_number : '')}" style="flex:1; min-width:220px">
            <button class="btn primary" id="lookupBtn">Найти в ИАИС ОГД</button>
          </div>
          <label class="field" for="objSel" style="margin-top:14px">Или выберите из своих объектов</label>
          <select id="objSel">${visibleObjects().map(x =>
            `<option value="${x.id}" ${x.id === S.obj ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select>
          ${o ? `<dl class="kv" style="margin-top:16px">
            <dt>Адрес</dt><dd>${esc(o.address)}</dd>
            <dt>Застройщик</dt><dd>${esc(o.developer || '—')}</dd>
            <dt>Подрядчик</dt><dd>${esc(o.contractor || '—')}</dd>
            <dt>Этап</dt><dd>${esc(o.stage || '—')}</dd>
            <dt>Экспертиза</dt><dd>${o.expertise ? esc(o.expertise.number) + ' от ' + ruDate(o.expertise.date) : '—'}</dd>
          </dl>
          <p style="margin-top:12px"><span class="chip accent">Сведения подтверждены ИАИС ОГД</span></p>` : ''}
        </div>
      </div>

      <div class="card"><header><div><h2>2. Документация</h2>
        <p>Тип документа определяется по содержимому, редакция и дата — по основной надписи
           ГОСТ Р 21.1101. Перетащите файлы в нужную зону или выберите вручную.</p></div>
        <button class="btn sm" id="demoKit">Подставить демонстрационный комплект</button></header>
        <div class="body stack" style="gap:12px">
          ${zone('pd', 'Проектная документация, стадия П', 'Все редакции — система сравнит их между собой')}
          ${zone('rd', 'Рабочая документация', 'Марки КЖ, АР, ЭОМ и другие')}
          ${zone('as_built', 'Исполнительная документация', 'Акты освидетельствования, журналы, паспорта, протоколы')}
        </div>
      </div>

      <div class="card"><header><div><h2>3. Что сравнивать</h2>
        <p>Отмечены переходы, доступные по составу загруженного</p></div></header>
        <div class="body stack" style="gap:8px">
          ${[['T1','Сравнить редакции проектной документации между собой', S.uploads.pd.length >= 2],
             ['T3','Сравнить рабочую документацию с проектной', S.uploads.pd.length && S.uploads.rd.length],
             ['T5','Сравнить исполнительную документацию с рабочей', S.uploads.rd.length && S.uploads.as_built.length],
             ['T2','Проверить учёт замечаний экспертизы', S.uploads.pd.length]]
            .map(([code, label, avail]) => `
            <label style="display:flex; gap:10px; align-items:flex-start; border:1px solid var(--line);
                          border-radius:8px; padding:10px 12px; ${avail ? '' : 'opacity:.5'}">
              <input type="checkbox" data-tr="${code}" ${S.chosen.includes(code) && avail ? 'checked' : ''}
                     ${avail ? '' : 'disabled'} style="margin-top:3px">
              <span><b class="mono">${code}</b> · ${esc(label)}
              ${avail ? '' : '<span style="display:block; font-size:11.5px; color:var(--ink-faint)">недостаточно загруженных документов</span>'}</span>
            </label>`).join('')}
        </div>
      </div>
    </div>

    <div class="stack" style="flex:1; min-width:280px">
      <div class="card"><header><div><h2>Запуск</h2></div></header>
        <div class="body">
          <button class="btn primary" id="runBtn" style="width:100%; justify-content:center"
                  ${ready && !S.analysing ? '' : 'disabled'}>
            ${S.analysing ? 'Выполняется…' : 'Запустить анализ'}</button>
          <ul class="steps" style="margin-top:14px">
            ${STAGES.map((label, i) => `
              <li class="${S.stage > i ? 'done' : S.stage === i ? 'now' : ''}">
                <span class="dot">${S.stage > i ? '✓' : i+1}</span>${esc(label)}</li>`).join('')}
          </ul>
          ${S.analysing ? `<div class="bar" style="margin-top:12px"><span style="width:${Math.max(S.stage,0)/(STAGES.length-1)*100}%"></span></div>` : ''}
          ${has && S.stage < 0 ? `<p class="note" style="margin-top:14px">По этому объекту анализ уже
            выполнялся. <a href="#" data-go="findings">Открыть журнал расхождений</a>.</p>` : ''}
        </div>
      </div>

      <div class="card"><header><div><h2>Как это работает</h2></div></header>
        <div class="body" style="font-size:12.5px; color:var(--ink-muted); display:flex; flex-direction:column; gap:8px">
          <p>Система сравнивает не «проект с проектом», а любые два состояния объекта.
             Один механизм закрывает и стадию проектирования, и производство работ.</p>
          <p>Каждый вывод содержит ссылку на пункт норматива и координаты области на листе
             документа: инспектор проверяет вывод за секунды, а не ищет заново.</p>
          <p><b style="color:var(--ink)">Система формирует гипотезы, а не заключения.</b>
             Решение принимает должностное лицо.</p>
        </div>
      </div>
    </div>
  </div>`;
}

function startAnalysis() {
  S.analysing = true; S.stage = 0; render();
  const tick = () => {
    S.stage += 1;
    if (S.stage >= STAGES.length - 1) {
      S.analysing = false; S.stage = STAGES.length - 1;
      render();
      toast(`Анализ завершён: расхождений — ${findingsOf(S.obj).length}`);
      setTimeout(() => { S.stage = -1; go('findings'); }, 700);
      return;
    }
    render();
    setTimeout(tick, 620);
  };
  setTimeout(tick, 620);
}

/* ── Журнал расхождений: обязательно файл и лист ────────────────────────── */
function viewFindings() {
  let rows = visibleFindings();
  if (S.role === 'inspector' || S.role === 'head') {
    if (S.filters.obj) rows = rows.filter(f => f.object_id === S.filters.obj);
  }
  if (S.filters.sev) rows = rows.filter(f => f.severity === S.filters.sev);
  if (S.filters.tr) rows = rows.filter(f => f.transition === S.filters.tr);
  rows = [...rows].sort((a,b) => b.priority_score - a.priority_score);

  const body = rows.map(f => {
    // Требование и факт — обязательно разные листы, иначе сравнивать нечего.
    const req = f.evidence.find(e => e.role === 'требование') || f.evidence[0];
    const idxReq = f.evidence.indexOf(req);
    const fact = f.evidence.find((e, i) => i !== idxReq) || req;
    const idxFact = f.evidence.indexOf(fact);
    const cut = (t) => t.length > 96 ? t.slice(0, 95) + '…' : t;
    const verdict = S.assessed[f.public_id];
    return `<tr class="stripe ${f.severity}">
      <td>
        <b><a href="#" data-go="finding" data-id="${f.public_id}">${esc(f.title)}</a></b>
        <div style="margin-top:4px; display:flex; gap:6px; flex-wrap:wrap; align-items:center">
          <span class="mono" style="font-size:11px; color:var(--ink-faint)">${f.public_id} · ${f.code}</span>
          <span class="chip neutral" title="${esc(TRANSITIONS[f.transition])}">${f.transition}</span>
          ${f.injection_suspected ? '<span class="chip major">попытка воздействия на систему</span>' : ''}
        </div>
      </td>
      <td style="min-width:150px">
        <b style="font-weight:500">${esc(f.structural_element.name || '—')}</b>
        <div style="font-size:11.5px; color:var(--ink-faint)">${esc(place(f.structural_element))}</div>
      </td>
      <td style="min-width:200px"><div class="docref">
        <b>${esc(req.doc_title)}</b>
        <span class="page" onclick="openSheet('${f.public_id}',${idxReq})">лист ${req.page} · показать</span>
        <span class="val">${esc(cut(req.extracted))}</span></div></td>
      <td style="min-width:200px"><div class="docref">
        <b>${esc(fact.doc_title)}</b>
        <span class="page" onclick="openSheet('${f.public_id}',${idxFact})">лист ${fact.page} · показать</span>
        <span class="val">${esc(cut(fact.extracted))}</span></div></td>
      <td><span class="chip ${f.severity}">${SEV[f.severity]}</span>
        <div class="conf" style="margin-top:6px"><span class="track"><span style="width:${f.confidence*100}%"></span></span>
        <span class="pct">${Math.round(f.confidence*100)}%</span></div></td>
      <td class="num" style="font-weight:600">${f.priority_score}</td>
      <td>${verdict ? `<span class="chip ${verdict === 'confirmed' ? 'critical' : 'neutral'}">${
          verdict === 'confirmed' ? 'подтверждено' : verdict === 'rejected' ? 'не подтверждено' : 'уточнить'}</span>`
        : '<span style="font-size:11.5px; color:var(--ink-faint)">не оценено</span>'}</td>
    </tr>`;
  }).join('');

  return `
  <div class="card"><header>
    <div><h2>Журнал расхождений</h2>
      <p>Для каждого расхождения указан документ и лист, на котором оно находится — инспектор
         открывает страницу и проверяет вывод сам</p></div>
    <div class="btn-row no-print">
      <button class="btn sm" onclick="window.print()">Печать</button>
      <button class="btn sm" id="csvBtn">Выгрузить в CSV</button>
    </div></header>
    <div class="body" style="padding-bottom:0">
      <div class="btn-row" style="margin-bottom:12px">
        <select id="fObj" style="width:auto; min-width:200px">
          <option value="">Все объекты</option>
          ${visibleObjects().filter(o => analysedIds().includes(o.id)).map(o =>
            `<option value="${o.id}" ${S.filters.obj === o.id ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}
        </select>
        <select id="fSev" style="width:auto">
          <option value="">Любая критичность</option>
          ${Object.entries(SEV).map(([k,v]) => `<option value="${k}" ${S.filters.sev===k?'selected':''}>${v}</option>`).join('')}
        </select>
        <select id="fTr" style="width:auto">
          <option value="">Все переходы</option>
          ${Object.entries(TRANSITIONS).map(([k,v]) => `<option value="${k}" ${S.filters.tr===k?'selected':''}>${k} · ${v}</option>`).join('')}
        </select>
        <span style="align-self:center; font-size:12.5px; color:var(--ink-muted)">Найдено: <b class="num">${rows.length}</b></span>
      </div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Расхождение</th><th>Конструктив</th><th>Требование: документ и лист</th>
        <th>Факт: документ и лист</th><th>Критичность и уверенность</th><th>Приоритет</th><th>Оценка</th>
      </tr></thead>
      <tbody>${body || `<tr><td colspan="7"><div class="empty"><b>Расхождений не найдено</b>
        Снимите фильтры или загрузите комплект документации по объекту.</div></td></tr>`}</tbody>
    </table></div>
  </div>`;
}
