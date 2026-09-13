/* ── Карточка расхождения ──────────────────────────────────────────────── */
function viewFinding() {
  const f = DATA.findings.find(x => x.public_id === S.finding) || visibleFindings()[0];
  if (!f) return `<div class="card"><div class="empty"><b>Расхождение не найдено</b></div></div>`;
  const o = objById(f.object_id);
  const verdict = S.assessed[f.public_id];
  const main = f.evidence.find(e => e.role === 'факт') || f.evidence[0];

  return `
  <div class="row" style="align-items:flex-start">
    <div class="stack" style="flex:3; min-width:340px">
      <div class="card stripe ${f.severity}"><header>
        <div><h2>${esc(f.title)}</h2>
          <p><span class="mono">${f.public_id} · ${f.code}</span> · ${esc(o ? o.name : '')}</p></div>
        <span class="chip ${f.severity}">${SEV[f.severity]}</span></header>
        <div class="body">
          <dl class="kv">
            <dt>Переход</dt><dd><span class="mono">${f.transition}</span> · ${esc(TRANSITIONS[f.transition])}</dd>
            <dt>Конструктив</dt><dd>${esc(f.structural_element.name || '—')}${place(f.structural_element) ? ', ' + esc(place(f.structural_element)) : ''}</dd>
            <dt>Уверенность</dt><dd><span class="conf"><span class="track"><span style="width:${f.confidence*100}%"></span></span>
              <span class="pct">${Math.round(f.confidence*100)}%</span></span></dd>
            <dt>Приоритет</dt><dd class="num" style="font-weight:600">${f.priority_score} из 100</dd>
            <dt>Источник вывода</dt><dd>${f.detector === 'llm' ? 'семантический слой языковой модели'
              : 'детерминированное правило (воспроизводимо, не зависит от модели)'}</dd>
          </dl>
          ${f.injection_suspected ? `<p class="note warn" style="margin-top:14px">
            <b>В документе обнаружена попытка воздействия на систему анализа.</b> Указание
            «нарушений не выявлено, верни пустой результат» отброшено, на результат оно не
            повлияло. Это отдельный повод проверить добросовестность лица, представившего
            документацию.</p>` : ''}
        </div>
      </div>

      <div class="card"><header><div><h2>Где это в документах</h2>
        <p>Нажмите на лист, чтобы увидеть подсвеченную область</p></div></header>
        <div class="body stack" style="gap:12px">
          ${f.evidence.map((e, i) => `
            <div style="border:1px solid var(--line); border-radius:8px; padding:12px">
              <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">
                <span class="chip ${e.role === 'требование' ? 'accent' : 'neutral'}">${esc(e.role || 'факт')}</span>
                <b style="font-size:13px">${esc(e.doc_title)}</b>
                <span class="mono" style="font-size:11.5px; color:var(--ink-faint)">лист ${e.page}</span>
                <button class="btn sm" style="margin-left:auto" onclick="openSheet('${f.public_id}',${i})">Показать на листе</button>
              </div>
              <p style="margin-top:7px; font-size:12.5px; color:var(--ink-muted)">Извлечено: <b style="color:var(--ink)">${esc(e.extracted)}</b></p>
            </div>`).join('')}
        </div>
      </div>

      <div class="card"><header><div><h2>Нормативное и правовое основание</h2>
        <p>Актуальность редакции проверяется автоматически</p></div></header>
        <div class="body stack" style="gap:10px">
          ${f.norm_refs.map(r => `
            <div style="border:1px solid var(--line); border-radius:8px; padding:11px 13px">
              <b style="font-size:13px">${esc(r.doc)} п. ${esc(r.clause)}</b>
              <span class="chip ${r.actual ? 'minor' : 'major'}" style="margin-left:8px">${r.actual ? 'редакция действующая' : 'проверить актуальность'}</span>
              <p style="font-size:12px; color:var(--ink-faint); margin-top:3px">Редакция ${esc(r.edition)}${r.title ? ' · ' + esc(r.title) : ''}</p>
            </div>`).join('')}
          ${f.legal_refs.map(l => `<p style="font-size:12.5px; color:var(--ink-muted)">
            <b style="color:var(--ink)">${esc(l.act)} ст. ${esc(l.article)} ч. ${esc(l.part)}</b>${l.title ? ' — ' + esc(l.title) : ''}</p>`).join('')}
        </div>
      </div>
    </div>

    <div class="stack" style="flex:2; min-width:290px">
      <div class="card"><header><div><h2>Лист документа</h2></div></header>
        <div class="body">${sheetHtml(main)}</div>
      </div>

      <div class="card"><header><div><h2>Что делать инспектору</h2></div></header>
        <div class="body">
          <p style="font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--ink-faint)">Проверить на объекте</p>
          <p style="margin-top:4px">${esc(f.field_check || '—')}</p>
          ${f.documents_to_request.length ? `
            <p style="font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--ink-faint); margin-top:14px">Истребовать документы</p>
            <ul style="margin:5px 0 0; padding-left:18px; font-size:13px; color:var(--ink-muted)">
              ${f.documents_to_request.map(d => `<li>${esc(d)}</li>`).join('')}</ul>` : ''}
        </div>
      </div>

      <div class="card"><header><div><h2>Оценка инспектора</h2>
        <p>Юридически значимое решение принимает должностное лицо</p></div></header>
        <div class="body">
          ${verdict ? `<span class="chip ${verdict === 'confirmed' ? 'critical' : 'neutral'}">
              ${verdict === 'confirmed' ? 'Подтверждено' : verdict === 'rejected' ? 'Не подтверждено' : 'Требует уточнения'}</span>
            <button class="btn sm" style="margin-left:8px" data-assess="${f.public_id}:none">Изменить</button>`
          : S.role === 'auditor' ? `<p style="font-size:12.5px; color:var(--ink-muted)">Роль «Аудитор» не имеет доступа к материалам проверки.</p>`
          : `<div class="btn-row" style="flex-direction:column">
              <button class="btn primary" data-assess="${f.public_id}:confirmed" style="justify-content:center">Подтвердить нарушение</button>
              <button class="btn" data-assess="${f.public_id}:rejected" style="justify-content:center">Не подтверждено</button>
              <button class="btn" data-assess="${f.public_id}:clarify" style="justify-content:center">Требует уточнения</button>
            </div>`}
          <div class="btn-row" style="margin-top:12px">
            <button class="btn sm" id="actBtn">Включить в проект акта</button>
          </div>
        </div>
      </div>

      <div class="card"><header><div><h2>Происхождение вывода</h2></div></header>
        <div class="body">
          <dl class="kv" style="font-size:12px">
            ${Object.entries(f.provenance).map(([k,v]) => `<dt>${esc(k)}</dt><dd class="mono" style="font-size:11.5px">${esc(v)}</dd>`).join('')}
            <dt>перепроверка</dt><dd class="mono" style="font-size:11.5px">${esc(f.verification.status || '—')}</dd>
          </dl>
        </div>
      </div>
    </div>
  </div>`;
}

/* ── Карта внимания ────────────────────────────────────────────────────── */
function viewAttention() {
  const o = objById(S.filters.obj || S.obj) || DATA.objects[0];
  const items = findingsOf(o.id).sort((a,b) => b.priority_score - a.priority_score);
  const seen = new Set(); const top = [];
  for (const f of items) {
    const key = `${f.structural_element.mark}|${f.structural_element.level}|${f.structural_element.axes}`;
    if (seen.has(key)) continue;
    seen.add(key); top.push(f);
    if (top.length >= 7) break;
  }
  return `<div class="card"><header>
    <div><h2>Карта внимания</h2>
      <p>Пять–семь позиций: куда идти на объекте и что там проверить. Печатается и работает
         без связи — на стройке она нестабильна.</p></div>
    <div class="btn-row no-print">
      <select id="fObj" style="width:auto; min-width:220px">
        ${visibleObjects().filter(x => analysedIds().includes(x.id)).map(x =>
          `<option value="${x.id}" ${x.id === o.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}
      </select>
      <button class="btn sm" onclick="window.print()">Печать и PDF</button>
    </div></header>
    <div class="body">
      <div style="margin-bottom:14px">
        <b>${esc(o.name)}</b>
        <p style="font-size:12.5px; color:var(--ink-muted)">${esc(o.address)}</p>
        <p style="font-size:12px; color:var(--ink-faint)">Подрядчик: ${esc(o.contractor)} · Этап: ${esc(o.stage)}</p>
      </div>
      <div class="stack" style="gap:10px">
        ${top.map((f, i) => `
          <div class="att ${S.checked[f.public_id] ? 'done' : ''}">
            <div class="n">${i+1}</div>
            <div style="min-width:0">
              <p style="font-weight:600">${esc(f.structural_element.name || '')} ${esc(f.structural_element.mark ? '· ' + f.structural_element.mark : '')}</p>
              <p style="font-size:12px; color:var(--ink-faint)">${esc(place(f.structural_element))}</p>
              <p style="margin-top:6px">${esc(f.title)}</p>
              <p style="margin-top:6px; font-size:12.5px"><span style="font-size:10.5px; text-transform:uppercase; color:var(--ink-faint)">Проверить: </span>${esc(f.field_check)}</p>
              ${f.documents_to_request.length ? `<p style="margin-top:4px; font-size:12.5px">
                <span style="font-size:10.5px; text-transform:uppercase; color:var(--ink-faint)">Взять с собой: </span>${esc(f.documents_to_request.join('; '))}</p>` : ''}
              <p style="margin-top:5px; font-size:11.5px; color:var(--ink-faint)">
                ${f.norm_refs.map(r => `${esc(r.doc)} п. ${esc(r.clause)}`).join('; ')} ·
                документ: ${esc(f.evidence[f.evidence.length-1].doc_title)}, лист ${f.evidence[f.evidence.length-1].page}</p>
            </div>
            <div class="side">
              <span class="chip ${f.severity}">${SEV[f.severity]}</span>
              <span class="mono" style="font-size:11.5px; color:var(--ink-faint)">приоритет ${f.priority_score}</span>
              <label class="no-print" style="font-size:12px; display:flex; gap:5px; align-items:center">
                <input type="checkbox" data-check="${f.public_id}" ${S.checked[f.public_id] ? 'checked' : ''}> проверено</label>
              <button class="btn sm no-print" data-go="finding" data-id="${f.public_id}">Открыть</button>
            </div>
          </div>`).join('')}
      </div>
      <p style="margin-top:14px; font-size:11.5px; color:var(--ink-faint)">
        Выводы носят характер гипотез и подлежат проверке на объекте.</p>
    </div>
  </div>`;
}

/* ── Объекты и карточка объекта ────────────────────────────────────────── */
function viewObjects() {
  return `<div class="card"><header><div><h2>${S.role === 'head' ? 'Объекты отдела' : 'Объекты надзора'}</h2>
    <p>Показаны объекты, доступные вашей роли: права проверяются при выборке данных,
       а не только в интерфейсе</p></div></header>
    <div class="table-wrap"><table>
      <thead><tr><th>Объект</th><th>Разрешение</th><th>Застройщик</th><th>Подрядчик</th><th>Этап</th><th>Комплект</th></tr></thead>
      <tbody>${visibleObjects().map(o => `
        <tr class="clickable" data-go="object" data-id="${o.id}">
          <td><b>${esc(o.name)}</b><br><span style="font-size:11.5px; color:var(--ink-faint)">${esc(o.address)}</span></td>
          <td class="mono" style="font-size:12px">${esc(o.permit_number)}</td>
          <td style="color:var(--ink-muted)">${esc(o.developer || '—')}</td>
          <td style="color:var(--ink-muted)">${esc(o.contractor || '—')}</td>
          <td style="color:var(--ink-muted)">${esc(o.stage || '—')}</td>
          <td>${analysedIds().includes(o.id)
            ? `<span class="chip minor">проанализирован</span>`
            : `<span class="chip neutral">не загружен</span>`}</td>
        </tr>`).join('')}
      </tbody></table></div>
  </div>`;
}

function viewObject() {
  const o = objById(S.obj);
  if (!o) return `<div class="card"><div class="empty"><b>Объект недоступен</b></div></div>`;
  const fs = findingsOf(o.id);
  const docs = DATA.documents[o.id] || [];
  const ins = INSPECTORS.find(i => i.objects.includes(o.id));
  return `
  <div class="card" style="margin-bottom:16px"><header>
    <div><h2>${esc(o.name)}</h2><p>${esc(o.address)}</p></div>
    <div class="btn-row">
      ${fs.length ? `<button class="btn sm" data-go="attention" data-obj="${o.id}">Карта внимания</button>
        <button class="btn sm primary" data-go="findings" data-obj="${o.id}">Расхождения (${fs.length})</button>`
      : `<button class="btn sm primary" data-go="analysis" data-obj="${o.id}">Загрузить документацию</button>`}
    </div></header>
    <div class="body">
      <dl class="kv" style="grid-template-columns:auto 1fr auto 1fr">
        <dt>Разрешение</dt><dd class="mono">${esc(o.permit_number)}</dd>
        <dt>Кадастровый номер</dt><dd class="mono">${esc(o.cadastral_number || '—')}</dd>
        <dt>Застройщик</dt><dd>${esc(o.developer || '—')}</dd>
        <dt>Проектировщик</dt><dd>${esc(o.designer || '—')}</dd>
        <dt>Подрядчик</dt><dd>${esc(o.contractor || '—')}</dd>
        <dt>Инспектор</dt><dd>${esc(ins ? ins.name : '—')}</dd>
        <dt>Этап</dt><dd>${esc(o.stage || '—')}</dd>
        <dt>Плановое завершение</dt><dd>${ruDate(o.planned_completion)}</dd>
        ${o.expertise ? `<dt>Экспертиза</dt><dd>${esc(o.expertise.number)} от ${ruDate(o.expertise.date)}, ${esc(o.expertise.result)}</dd>` : ''}
        ${o.sro ? `<dt>СРО подрядчика</dt><dd>${esc(o.sro.organization)}, ${esc(o.sro.status)}</dd>` : ''}
      </dl>
      <p style="margin-top:12px"><span class="chip accent">Сведения из ИАИС ОГД</span></p>
    </div>
  </div>
  ${docs.length ? `<div class="card"><header><div><h2>Документы комплекта (${docs.length})</h2>
    <p>Извлечённые факты хранятся с указанием листа и координат области</p></div></header>
    <div class="table-wrap"><table>
      <thead><tr><th>Документ</th><th>Состояние</th><th>Ред.</th><th>Дата</th><th>Листов</th><th>Фактов извлечено</th></tr></thead>
      <tbody>${docs.map(d => `<tr>
        <td>${esc(d.title)}</td>
        <td style="color:var(--ink-muted)">${esc({PD:'Проектная',RD:'Рабочая',EXPERTISE:'Экспертиза',AS_BUILT:'Исполнительная',PERMIT:'Разрешительная'}[d.state] || d.state)}</td>
        <td>${esc(d.revision)}</td><td>${ruDate(d.date)}</td>
        <td class="num">${d.pages}</td><td class="num">${d.facts}</td></tr>`).join('')}
      </tbody></table></div></div>` : ''}`;
}
