import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  backendApi, CORRECTION_KINDS, pageImageUrl, type BackendDocument, type BackendFinding,
  type BackendComplianceRun, type BackendPdRun, type BackendSettings, type LlmCheck,
  type ReviewMessage,
} from '../backendApi'
import { useApp, type PendingUpload } from '../store'
import { DOCUMENTS_KEY, useDocuments } from '../useDocuments'
import { Chip, Empty, SectionCard, SeverityChip, Skeleton } from '../components/ui'

const PAGE_KIND_LABELS: Record<'drawing' | 'text', string> = { drawing: 'чертёж', text: 'текст' }

// Г.71 — набор провайдеров сокращён до двух (packages/backend/app/llm.py):
// Anthropic (сам инструмент) и GigaChat. Раньше здесь были ещё local/Ollama,
// OpenAI, Google, YandexGPT — их бэкенд больше не принимает вообще.
const PROVIDER_LABELS: Record<BackendSettings['provider'], string> = {
  anthropic: 'Anthropic (Claude)', gigachat: 'GigaChat',
}

// Зеркало PROVIDER_DEFAULT_MODELS из packages/backend/app/llm.py — реальные,
// а не выдуманные идентификаторы моделей, чтобы поле "Модель" не было полем
// вслепую. Список подсказок, а не жёсткий выбор: свою модель ввести
// по-прежнему можно (input + datalist), пустое поле берёт дефолт провайдера.
const MODEL_OPTIONS: Record<BackendSettings['provider'], string[]> = {
  anthropic: ['claude-sonnet-5', 'claude-opus-5', 'claude-haiku-4-5-20251001'],
  gigachat: ['GigaChat-2-Pro', 'GigaChat-2-Max', 'GigaChat-2'],
}

const PROVIDER_DEFAULT_MODEL: Record<BackendSettings['provider'], string> = {
  anthropic: 'claude-sonnet-5', gigachat: 'GigaChat-2-Pro',
}

const CLASSIFICATION_SOURCE_LABELS: Record<string, string> = {
  filename: 'по имени файла', title_page: 'по титульному листу', stamp_text: 'по штампу (текст)',
  stamp_vision: 'по штампу (зрение)', none: 'не определён', manual: 'указан вручную',
  filename_section_number: 'по номеру раздела в имени',
}

/**
 * Раздел документа: что определила программа и возможность поправить (Г.97).
 * Автоопределение остаётся, но последнее слово за инспектором: не опознанный
 * файл иначе уходит в разбор без раздела, а привязка требования к разделу —
 * прямое требование пользователя (Г.86).
 */
function DisciplineBadge({ doc, onChange }: {
  doc: BackendDocument
  onChange?: (code: string | null) => void
}) {
  const manual = doc.classification_source === 'manual'
  return (
    <span className="inline-flex items-center gap-1">
      {doc.discipline_code ? (
        <Chip tone={manual ? 'accent' : 'accent'}>
          {doc.discipline_code} · {CLASSIFICATION_SOURCE_LABELS[doc.classification_source ?? ''] ?? doc.classification_source}
        </Chip>
      ) : (
        <Chip tone="warn">раздел не определён</Chip>
      )}
      {onChange && (
        <select
          aria-label="Раздел документа"
          value={manual ? (doc.discipline_code ?? '') : ''}
          onChange={(e) => onChange(e.target.value || null)}
          className="rounded border border-surface-line bg-surface px-1 py-0.5 text-[11px] text-ink-muted">
          <option value="">{doc.discipline_code ? 'вернуть автоопределение' : 'указать раздел…'}</option>
          {DISCIPLINE_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      )}
    </span>
  )
}

// Список кодов разделов для ручного выбора. Держится здесь, а не приходит с
// сервера: это короткий справочник по ГОСТ Р 21.1101, меняется вместе с
// реестром на бэкенде, и лишний запрос ради него не нужен.
const DISCIPLINE_OPTIONS = [
  'ГП', 'АР', 'АС', 'КР', 'КЖ', 'КМ', 'ОВ', 'ВК', 'НВК', 'НВ', 'ВВ', 'ЭОМ', 'ЭС', 'СС',
  'АПС', 'ОПС', 'СКС', 'СКУД', 'АУПТ', 'ИТП', 'ВТ', 'ТХ', 'ПОС', 'ООС', 'ПБ',
]

function UploadZone({
  title, subtitle, docs, onFiles, onRemove, onSetSection, pending,
}: {
  title: string; subtitle: string; docs: BackendDocument[]
  onFiles: (files: FileList | null) => void; onRemove: (id: number) => void
  onSetSection: (id: number, code: string | null) => void
  pending: PendingUpload[]
}) {
  const [dragOver, setDragOver] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  // Тикает, пока есть хоть один файл в очереди — секундомер у "Обрабатывается…"
  // это доказательство, что процесс идёт, а не завис: большой PDF (сотни
  // листов) разбирается синхронно на бэкенде и может занимать десятки секунд
  // без единого промежуточного ответа сервера.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!pending.length) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [pending.length])

  return (
    <SectionCard title={title} subtitle={subtitle}>
      <div
        className={`rounded-md border border-dashed p-4 text-center transition-colors
          ${dragOver ? 'border-accent bg-accent/5' : 'border-surface-line bg-surface-muted/60'}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); onFiles(e.dataTransfer.files) }}
      >
        <p className="text-sm font-medium">Перетащите PDF сюда</p>
        {/* Кнопка выбора не блокируется во время загрузки: файлы уходят на
            сервер по одному в очереди, поэтому можно докидывать ещё, не
            дожидаясь конца текущей — именно это раньше выглядело как "не
            получается добавить ещё". */}
        <button type="button" className="btn-ghost mt-3" onClick={() => fileInput.current?.click()}>
          Выбрать файлы
        </button>
        <input ref={fileInput} type="file" multiple hidden accept=".pdf"
          onChange={(e) => { onFiles(e.target.files); e.target.value = '' }} />
      </div>
      {(docs.length > 0 || pending.length > 0) && (
        <ul className="mt-3 space-y-2">
          {docs.map((doc) => (
            <li key={doc.id} className="rounded-md border border-surface-line px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 flex-1 truncate" title={doc.name}>
                  {doc.status === 'parsing' && <span className="text-ink-faint">Обрабатывается… </span>}
                  {doc.status === 'error' && <span className="text-critical">Ошибка · </span>}
                  {doc.name} <span className="text-ink-faint">
                    · {doc.pages} л.{doc.size ? ` · ${formatSize(doc.size)}` : ''}
                    {/* Разрезание тяжёлого тома на части — внутреннее устройство
                        хранения, но инспектору важно понимать, почему такой том
                        обрабатывается дольше. Нумерация листов при этом
                        остаётся исходной, и это сказано прямо. */}
                    {doc.parts_count > 1 && ` · ${doc.parts_count} частей (нумерация листов исходная)`}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <DisciplineBadge doc={doc} onChange={(code) => onSetSection(doc.id, code)} />
                  <button className="text-xs text-ink-faint hover:text-critical" onClick={() => onRemove(doc.id)} aria-label="Удалить">✕</button>
                </span>
              </div>
              {doc.status === 'error' && doc.classification_source && (
                // Причина падения разбора — то, что реально нужно, чтобы понять,
                // стоит ли просто перезалить файл (например, был запаролен) или
                // дело в самом файле; раньше терялась, оставалась только "Ошибка".
                <p className="mt-1 text-xs text-critical">{doc.classification_source}</p>
              )}
            </li>
          ))}
          {pending.map((p, i) => (
            <li key={`${p.name}-${p.startedAt}`}
              className="flex items-center gap-2 rounded-md border border-dashed border-surface-line px-3 py-2 text-sm text-ink-faint">
              <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-accent border-t-transparent" />
              <span className="min-w-0 flex-1 truncate" title={p.name}>{p.name}</span>
              <span className="shrink-0">
                {i === 0 ? `обрабатывается… ${Math.max(0, Math.round((now - p.startedAt) / 1000))} с` : 'в очереди'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  )
}

const REVIEW_LABELS: Record<BackendFinding['reviewed_status'], string> = {
  new: 'Новое', confirmed: 'Подтверждено', rejected: 'Отклонено',
}

function FindingRow({ finding, onReview }: { finding: BackendFinding; onReview: (status: BackendFinding['reviewed_status']) => void }) {
  const photoUrl = finding.after_document_id && finding.after_page
    ? pageImageUrl(finding.after_document_id, finding.after_page) : null
  return (
    <li className="rounded-md border border-surface-line p-3 text-sm">
      <div className="flex flex-wrap items-start gap-3">
        {photoUrl && (
          <a href={photoUrl} target="_blank" rel="noreferrer" className="shrink-0" title="Открыть лист целиком">
            <img src={photoUrl} alt={`Лист ${finding.after_page}`}
              className="h-24 w-24 rounded border border-surface-line object-cover object-top" />
          </a>
        )}
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            {finding.severity && <SeverityChip value={finding.severity} />}
            <Chip tone={finding.kind === 'vision' ? 'accent' : 'neutral'}>
              {finding.kind === 'vision' ? 'визуально' : 'текст'}
            </Chip>
            {finding.label && <span className="font-medium">{finding.label}</span>}
            {finding.after_page && (
              <span className="text-xs text-ink-faint">лист {finding.after_page}</span>
            )}
            {finding.reviewed_status !== 'new' && <Chip>{REVIEW_LABELS[finding.reviewed_status]}</Chip>}
          </div>
          <p className="text-ink-muted">{finding.change_text}</p>
          {finding.field_check && (
            // Ради этой строки инспектор и открывает список: она говорит, что
            // сделать на объекте, а не что различается на бумаге.
            <p className="mt-1.5 border-l-2 border-accent-line pl-2 text-xs text-ink-muted">
              <span className="font-medium text-ink">На объекте: </span>{finding.field_check}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            className={`btn-ghost px-2 py-1 text-xs ${finding.reviewed_status === 'confirmed' ? 'border-accent text-accent' : ''}`}
            onClick={() => onReview('confirmed')}>Подтвердить</button>
          <button
            className={`btn-ghost px-2 py-1 text-xs ${finding.reviewed_status === 'rejected' ? 'border-critical text-critical' : ''}`}
            onClick={() => onReview('rejected')}>Отклонить</button>
        </div>
      </div>
    </li>
  )
}

const SEVERITY_RANK: Record<BackendFinding['severity'], number> = { critical: 0, major: 1, minor: 2, '': 3 }

function FindingGroup({ label, findings, onReview }: {
  label: string; findings: BackendFinding[]
  onReview: (id: number, status: BackendFinding['reviewed_status']) => void
}) {
  const [open, setOpen] = useState(false)
  const worst = findings.reduce((a, b) => (SEVERITY_RANK[a.severity] <= SEVERITY_RANK[b.severity] ? a : b))
  return (
    <li className="rounded-md border border-surface-line">
      <button className="flex w-full flex-wrap items-center gap-2 p-3 text-left text-sm" onClick={() => setOpen((v) => !v)}>
        <span className="text-ink-faint">{open ? '▾' : '▸'}</span>
        {worst.severity && <SeverityChip value={worst.severity} />}
        <span className="font-medium">{label || '(без метки)'}</span>
        <Chip>{findings.length}</Chip>
      </button>
      {open && (
        <ul className="space-y-2 border-t border-surface-line p-3 pt-2">
          {findings.map((f) => (
            <FindingRow key={f.id} finding={f} onReview={(status) => onReview(f.id, status)} />
          ))}
        </ul>
      )}
    </li>
  )
}

/**
 * Карточка разбора комплекта — ОДНА на обе стороны (Г.95). Проектная и
 * рабочая документация разбираются одним механизмом; отдельного компонента
 * «разбор РД» нет намеренно, чтобы различие не завелось там, где его нет.
 */
function ParseCard({
  title, subtitle, docs, run, onStart, pending, llmCheck, selected, onSelect,
  onStop, stopping,
}: {
  title: string
  subtitle: string
  docs: BackendDocument[]
  run: BackendPdRun | undefined
  onStart: () => void
  pending: boolean
  llmCheck?: LlmCheck
  selected: number[]
  onSelect: (ids: number[]) => void
  onStop?: () => void
  stopping?: boolean
}) {
  const running = run?.status === 'running' || pending
  // Пустой выбор означает «все загруженные»: инспектор, которому нужен весь
  // комплект, не должен ничего отмечать. Отметки нужны там, где том тяжёлый
  // и разбирать нужно не всё сразу.
  const chosen = selected.length ? docs.filter((d) => selected.includes(d.id)) : docs
  const toggle = (id: number) => {
    const base = selected.length ? selected : docs.map((d) => d.id)
    const next = base.includes(id) ? base.filter((x) => x !== id) : [...base, id]
    onSelect(next.length === docs.length ? [] : next)
  }
  return (
    <SectionCard title={title} subtitle={subtitle}>
      {docs.length > 1 && (
        <div className="mb-3 rounded-md border border-surface-line p-2">
          <div className="mb-1 flex items-center justify-between text-xs text-ink-muted">
            <span>Что разбирать — выбрано {chosen.length} из {docs.length}</span>
            <button type="button" className="text-xs text-accent hover:underline"
                    onClick={() => onSelect([])}>все</button>
          </div>
          <ul className="space-y-1">
            {docs.map((doc) => (
              <li key={doc.id}>
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input type="checkbox" checked={chosen.some((d) => d.id === doc.id)}
                         disabled={running} onChange={() => toggle(doc.id)} />
                  <span className="min-w-0 flex-1 truncate" title={doc.name}>{doc.name}</span>
                  <span className="text-xs text-ink-faint">{doc.pages} л.</span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={chosen.length === 0 || running}
          onClick={onStart}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
          {running ? 'Разбираем…' : docs.length > 1 && chosen.length < docs.length
            ? `Разобрать выбранное (${chosen.length})` : 'Разобрать документацию'}
        </button>
        {docs.length === 0 && <span className="text-xs text-ink-muted">Сначала загрузите документы выше.</span>}
        {/* Г.91 — состояние связи видно ДО запуска: разбор тома это десятки
            вызовов и минуты, а при оборванной связи каждый молча вернул бы
            пустой результат. */}
        {/* Инспектору нужен один факт: работает ИИ или нет. Какой это
            провайдер, каким набором проверяется сертификат и текст сбоя —
            вопросы администратора, они остались в ответе /llm-check и в
            карточке настроек, но с рабочего экрана убраны. */}
        {llmCheck && (
          <span className={`text-xs ${llmCheck.reachable ? 'text-ink-muted' : 'text-danger'}`}
                title={llmCheck.reachable ? undefined : llmCheck.message}>
            {llmCheck.reachable ? 'ИИ работает' : 'ИИ не отвечает — разбор будет неполным'}
          </span>
        )}
      </div>

      {!run && (
        <p className="text-sm text-ink-muted">
          Загрузите документы и нажмите кнопку. Настраивать ничего не нужно: правила извлечения и модель заданы в системе.
        </p>
      )}
      {run?.status === 'running' && (
        <RunProgress run={run} onStop={onStop} stopping={stopping} />
      )}
      {run?.status === 'cancelled' && (
        <div className="rounded-md border border-surface-line bg-surface-muted/60 px-3 py-2 text-sm text-ink-muted">
          Остановлено инспектором. Результат неполный — запустите заново, когда будете готовы.
          {run.composition && (
            <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-xs">{run.composition}</pre>
          )}
        </div>
      )}
      {run?.status === 'error' && (
        <div className="space-y-2">
          <div className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-sm text-danger">{run.error}</div>
          {/* Г.98 — состав считается до обращения к модели и полезен сам по
              себе: показываем его даже когда требования извлечь не удалось. */}
          {run.composition && (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-surface-line bg-surface-muted/60 p-3 text-xs leading-relaxed text-ink">
              {run.composition}
            </pre>
          )}
        </div>
      )}
      {run?.status === 'done' && (
        <div className="space-y-2">
          <p className="text-xs text-ink-muted">
            Извлечено требований: <span className="font-medium text-ink">{run.requirements_total}</span>
            {' · '}ИИ: {run.provider}
            {run.store_run_id !== null && <> · разбор №{run.store_run_id} сохранён для сверки</>}
          </p>
          {/* Г.95 — состав показывается всегда: у рабочей документации текста
              почти нет по природе, и пустая сводка без состава неотличима от сбоя. */}
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-surface-line bg-surface-muted/60 p-3 text-xs leading-relaxed text-ink">
            {run.composition}
          </pre>
          {run.requirements_total > 0 ? (
            <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-md border border-surface-line bg-surface-muted/60 p-3 text-xs leading-relaxed text-ink">
              {run.summary}
            </pre>
          ) : (
            <p className="text-sm text-ink-muted">
              Требований из текста не извлечено — см. состав выше: если связного текста в томе нет,
              это ожидаемо, проверять нужно по листам.
            </p>
          )}
        </div>
      )}
    </SectionCard>
  )
}


/**
 * Полоса прогресса, которая не врёт (Г.112).
 *
 * Три правила, каждое против типового обмана:
 *
 * 1. Доля берётся из ФАКТА — сколько пачек страниц сервер уже прошёл.
 *    Пока сервер не сказал, сколько их всего, полоса не рисуется вовсе:
 *    ползунок, ползущий «примерно», выглядит как работа там, где её
 *    измерить ещё нечем.
 * 2. Остаток считается по СКОРОСТИ ЭТОГО прогона: прошедшее время делённое
 *    на сделанное. Никаких средних по прошлым запускам — том на 40 листов
 *    и том на 700 идут с разной скоростью.
 * 3. Оценка появляется не сразу: по одной пачке скорость ещё не измерена,
 *    и любое число было бы выдумкой. До этого честно пишем «оцениваю».
 */
function RunProgress({ run, onStop, stopping }: {
  run: BackendPdRun | BackendComplianceRun
  onStop?: () => void
  stopping?: boolean
}) {
  const total = run.units_total || 0
  const done = run.units_done || 0
  const started = run.started_at ? new Date(run.started_at + 'Z').getTime() : null
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const share = total > 0 ? Math.min(1, done / total) : null
  const elapsed = started ? Math.max(0, now - started) : null
  // Две пройденные единицы — минимум, на котором скорость вообще есть.
  const eta = (elapsed && share !== null && done >= 2 && done < total)
    ? Math.round((elapsed / done) * (total - done) / 1000)
    : null

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 text-xs text-ink-muted">
        <span>{run.stage || 'идёт разбор'}</span>
        <span className="flex items-center gap-2">
          {share !== null && <span>{done} из {total}</span>}
          {onStop && (
            // Остановка не мгновенная: исполнитель прервётся на ближайшей
            // безопасной точке (Г.114). Кнопка говорит это состоянием, а не
            // делает вид, что всё прекратилось по нажатию.
            <button type="button" onClick={onStop} disabled={stopping}
              className="rounded border border-surface-line px-2 py-0.5 text-xs text-ink-muted hover:text-danger disabled:opacity-50">
              {stopping ? 'останавливаю…' : 'Остановить'}
            </button>
          )}
        </span>
      </div>
      {share !== null ? (
        <div className="h-2 w-full overflow-hidden rounded-full bg-surface-muted">
          <div className="h-full rounded-full bg-accent transition-[width] duration-500"
               style={{ width: `${Math.round(share * 100)}%` }} />
        </div>
      ) : (
        // Полосы нет намеренно: доля ещё не измерена, и рисовать движение
        // означало бы показывать прогресс там, где его нечем посчитать.
        <div className="h-2 w-full overflow-hidden rounded-full bg-surface-muted">
          <div className="h-full w-1/3 animate-pulse rounded-full bg-accent/40" />
        </div>
      )}
      <p className="text-xs text-ink-faint">
        {elapsed !== null && <>идёт {formatDuration(Math.round(elapsed / 1000))}</>}
        {eta !== null
          ? <> · осталось примерно {formatDuration(eta)}</>
          : share !== null && done < total ? <> · оцениваю оставшееся время</> : null}
      </p>
    </div>
  )
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} с`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} мин`
  return `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1073741824).toFixed(1)} ГБ`
  if (bytes >= 1024 * 1024) return `${(bytes / 1048576).toFixed(1)} МБ`
  return `${Math.max(1, Math.round(bytes / 1024))} КБ`
}

/** Хранилище оригиналов: сколько занято и что можно освободить.
 *
 *  Оригиналы и кэш показаны РАЗДЕЛЬНО намеренно: кэш удаляется в любой
 *  момент без потери данных (файлы восстановятся из базы), оригиналы — нет.
 *  Одной цифрой «сколько на диске» этого не сказать, а решение принимает
 *  администратор. */
/** Версия работающего кода (Г.113).
 *
 *  Заведено после того, как в браузере несколько раз открывалась старая
 *  версия: отличить «код на диске не обновился» от «страница из кэша» было
 *  нечем, и любое объяснение выглядело отговоркой. Дата и заголовок
 *  последнего коммита читаются с сервера, то есть показывают именно тот
 *  код, который сейчас отвечает на запросы, а не то, что лежит в браузере.
 */
function VersionCard() {
  const version = useQuery({ queryKey: ['backend-version'], queryFn: backendApi.getVersion })
  const v = version.data
  return (
    <SectionCard title="Версия">
      {!v ? <Skeleton rows={1} /> : (
        <div className="space-y-1 text-xs text-ink-muted">
          <div className="flex justify-between gap-2">
            <span>Код на сервере</span>
            <span className="font-mono">{v.commit || '—'} · {v.date || '—'}</span>
          </div>
          <p className="text-ink-faint">{v.subject}</p>
          <p className="text-ink-faint">
            Если дата старая — обновите проект: система работает тем кодом, что лежит на диске.
          </p>
        </div>
      )}
    </SectionCard>
  )
}

function StorageCard() {
  const queryClient = useQueryClient()
  const { pushToast } = useApp()
  const storage = useQuery({ queryKey: ['backend-storage'], queryFn: backendApi.getStorage })
  const cleanup = useMutation({
    mutationFn: (dropCache: boolean) => backendApi.cleanupStorage(dropCache),
    onSuccess: (r) => {
      pushToast(`Удалено оригиналов: ${r.removed_files}, освобождено ${formatSize(r.freed_bytes + r.cache_freed_bytes)}`)
      queryClient.invalidateQueries({ queryKey: ['backend-storage'] })
    },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось выполнить уборку', 'error'),
  })
  // Взять том из хранилища в проверку без повторной загрузки (Г.114).
  const reuse = useMutation({
    mutationFn: ({ digest, side }: { digest: string; side: 'before' | 'after' }) =>
      backendApi.documentFromStorage(digest, side),
    onSuccess: (doc) => {
      pushToast(`«${doc.name}» добавлен в ${doc.side === 'before' ? 'ПД' : 'РД'}`)
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY })
    },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось взять файл', 'error'),
  })
  const [showFiles, setShowFiles] = useState(false)
  // Список запрашивается только когда его открыли: в хранилище бывают сотни
  // записей, и тянуть их ради двух цифр в свёрнутой карточке незачем.
  const files = useQuery({
    queryKey: ['backend-storage-files'],
    queryFn: backendApi.getStorageFiles,
    enabled: showFiles,
  })
  const data = storage.data
  return (
    <SectionCard title="Хранилище документов">
      {!data ? <Skeleton rows={2} /> : (
        <div className="space-y-2 text-sm">
          <div className="flex justify-between"><span className="text-ink-muted">Оригиналов в базе</span>
            <span>{data.files} · {formatSize(data.bytes)}</span></div>
          <div className="flex justify-between"><span className="text-ink-muted">Кэш на диске (восстановимый)</span>
            <span>{data.cache_files} · {formatSize(data.cache_bytes)}</span></div>
          <div className="flex justify-between"><span className="text-ink-muted">Документов в работе</span>
            <span>{data.documents}</span></div>
          <div className="flex justify-between"><span className="text-ink-muted">Срок хранения</span>
            <span>{data.retention_days} дн.</span></div>
          <p className="rounded-md border border-surface-line bg-surface-muted/60 px-3 py-2 text-xs text-ink-muted">
            Оригинал, на который ссылается загруженный документ, не удаляется никогда —
            сколько бы ни стоял срок хранения. Одинаковые файлы хранятся один раз.
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-ghost px-3 py-1.5 text-sm"
              onClick={() => setShowFiles((v) => !v)}>
              {showFiles ? 'Скрыть список' : 'Показать, что лежит'}
            </button>
            <button type="button" className="btn-ghost px-3 py-1.5 text-sm"
              disabled={cleanup.isPending} onClick={() => cleanup.mutate(false)}>
              Убрать по сроку хранения
            </button>
            <button type="button" className="btn-ghost px-3 py-1.5 text-sm"
              disabled={cleanup.isPending} onClick={() => cleanup.mutate(true)}>
              Освободить кэш
            </button>
          </div>
          {showFiles && (
            <div className="max-h-72 overflow-auto rounded-md border border-surface-line">
              {!files.data ? <Skeleton rows={3} /> : files.data.length === 0 ? (
                <p className="p-3 text-xs text-ink-muted">Хранилище пусто.</p>
              ) : (
                <ul className="divide-y divide-surface-line text-xs">
                  {files.data.map((f) => (
                    <li key={f.digest} className="px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        {/* Строка — ТОМ, а не кусок тома (Г.114). Тяжёлый файл
                            режется на части, и раньше каждая часть стояла
                            отдельной безымянной строкой: один документ выглядел
                            как десяток неизвестных файлов. Число частей — в
                            скобках: это устройство хранения, а не документы. */}
                        <span className="min-w-0 flex-1 truncate">
                          {f.documents.length ? f.documents.join(', ') : 'ничей — уйдёт по сроку хранения'}
                          {f.parts > 0 && ` (${f.parts} ч.)`}
                        </span>
                        <span className="whitespace-nowrap text-ink-faint">
                          {formatSize(f.total_size || f.size)}{f.pages ? ` · ${f.pages} л.` : ''}
                        </span>
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-ink-faint">
                        <span>
                          {f.digest.slice(0, 12)} · обращались {new Date(f.used_at + 'Z').toLocaleDateString()}
                          {f.cached ? ' · есть в кэше' : ' · только в базе'}
                        </span>
                        {/* Файл уже лежит здесь, а его разбор — в памяти:
                            заставлять инспектора снова искать том на диске и
                            снова его загружать незачем (Г.114). */}
                        <span className="ml-auto flex gap-1">
                          <button type="button" disabled={reuse.isPending}
                            onClick={() => reuse.mutate({ digest: f.digest, side: 'before' })}
                            className="rounded border border-surface-line px-2 py-0.5 hover:text-accent disabled:opacity-50">
                            в ПД
                          </button>
                          <button type="button" disabled={reuse.isPending}
                            onClick={() => reuse.mutate({ digest: f.digest, side: 'after' })}
                            className="rounded border border-surface-line px-2 py-0.5 hover:text-accent disabled:opacity-50">
                            в РД
                          </button>
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </SectionCard>
  )
}

export default function NewAnalysis() {
  const queryClient = useQueryClient()
  // Загруженные документы, очередь и id прогона живут в общем сторе, а не в
  // useState: переход на другую вкладку меню размонтирует этот компонент, и
  // локальное состояние (в том числе идущий анализ) терялось бы целиком.
  const {
    pushToast,
    analysisPendingBefore: pendingBefore, analysisPendingAfter: pendingAfter,
    analysisRunId: runId, analysisRunStatus: runStatus,
    setAnalysisPending, setAnalysisRunId,
    pdRunId, pdRunStatus, rdRunStatus, complianceRunStatus, complianceRunId,
    pdSelected, rdSelected, setPdRunId, setComplianceRunId, setSelected,
  } = useApp()
  // Документы — с сервера (Г.114). В сторе остались только очередь загрузки
  // и номера прогонов: прогон живёт на сервере и не должен сбрасываться от
  // обновления страницы, а список документов не должен её переживать.
  const { before: beforeDocs, after: afterDocs } = useDocuments()

  const settings = useQuery({ queryKey: ['backend-settings'], queryFn: backendApi.getSettings })
  const [form, setForm] = useState<BackendSettings>({ provider: 'gigachat', base_url: '', model: '' })
  useEffect(() => { if (settings.data) setForm(settings.data) }, [settings.data])

  const saveSettings = useMutation({
    mutationFn: () => backendApi.updateSettings(form),
    onSuccess: () => { pushToast('Настройки ИИ сохранены'); queryClient.invalidateQueries({ queryKey: ['backend-settings'] }) },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось сохранить настройки', 'error'),
  })

  const uploadTo = async (side: 'before' | 'after', files: FileList | null) => {
    if (!files || !files.length) return
    const setPending = (updater: Parameters<typeof setAnalysisPending>[1]) => setAnalysisPending(side, updater)

    // Ответ на загрузку приходит сразу: сервер сохраняет файл и отдаёт
    // документ со статусом «разбирается», а сам разбор идёт в фоне (Г.114).
    // Очередь показывает то, что ещё физически передаётся на сервер.
    const fileList = Array.from(files)
    const queued: PendingUpload[] = fileList.map((f) => ({ name: f.name, startedAt: Date.now() }))
    setPending((prev) => [...prev, ...queued])

    for (let i = 0; i < fileList.length; i++) {
      try {
        const doc = await backendApi.uploadDocument(side, fileList[i])
        await queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY })
        if (doc.status === 'error') {
          pushToast(`«${doc.name}»: не удалось разобрать документ`, 'error')
        }
      } catch (e) {
        pushToast(e instanceof Error ? e.message : 'Не удалось загрузить файл', 'error')
      }
      const done = queued[i]
      setPending((prev) => prev.filter((p) => p !== done))
    }
  }

  const removeFrom = async (_side: 'before' | 'after', id: number) => {
    try {
      await backendApi.deleteDocument(id)
    } catch (e) {
      pushToast(e instanceof Error ? e.message : 'Не удалось удалить документ', 'error')
    }
    // Список перечитывается с сервера, а не правится в браузере: иначе
    // неудавшееся удаление выглядело бы как удавшееся (Г.114).
    await queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY })
  }

  const run = useMutation({
    mutationFn: () => backendApi.createAnalysisRun(beforeDocs.map((d) => d.id), afterDocs.map((d) => d.id)),
    onSuccess: (data) => setAnalysisRunId(data.id),
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось запустить анализ', 'error'),
  })

  // Прогресс прогона (runStatus) опрашивается фоново в сторе (см. store.ts,
  // pollAnalysisRun) — не здесь: этот компонент размонтируется при уходе на
  // другую вкладку меню, а прогон должен продолжаться независимо от того,
  // какой экран сейчас открыт.
  const findings = useQuery({
    queryKey: ['backend-findings', runId],
    queryFn: () => backendApi.listFindings(runId as number),
    enabled: runId !== null && runStatus?.status === 'done',
  })

  // Список пар листов с исходом по каждой — данные о работе ИИ, а не только
  // итог; загружается только по клику "Подробности по листам", чтобы не
  // тянуть сотни строк на каждый прогон, если инспектору это не нужно.
  const [pairsOpen, setPairsOpen] = useState(false)
  const [pairsFilter, setPairsFilter] = useState('')
  const pagePairs = useQuery({
    queryKey: ['backend-pairs', runId],
    queryFn: () => backendApi.listPagePairs(runId as number),
    enabled: runId !== null && runStatus?.status === 'done' && pairsOpen,
  })

  const reviewFinding = useMutation({
    mutationFn: ({ id, status }: { id: number; status: BackendFinding['reviewed_status'] }) =>
      backendApi.updateFinding(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backend-findings', runId] }),
  })

  const readyToRun = beforeDocs.some((d) => d.status === 'ok') && afterDocs.some((d) => d.status === 'ok')
    && !beforeDocs.some((d) => d.status === 'parsing') && !afterDocs.some((d) => d.status === 'parsing')

  const items = findings.data ?? []
  const significantCount = items.filter((f) => f.reviewed_status !== 'rejected').length

  // Сотни находок плоским списком не позволяют увидеть, что за ними на
  // самом деле систематическая проблема одного типа, повторённая на многих
  // листах, а не сто разных нарушений — группировка по label (краткий код
  // из промпта, см. vision.py) сворачивает повторы в одну строку со счётчиком.
  // Г.94 — стадия 1: разбор ПД одной кнопкой. Отдельно от сравнения с РД и
  // ПЕРЕД ним: разбор ПД самодостаточен, рабочей документации на половине
  // объектов нет вовсе, и её отсутствие не должно мешать получить сводку.
  // Г.112 — прогон и его ход живут в общем сторе и опрашиваются оттуда,
  // а не в useQuery этого экрана: разбор тома идёт минутами, и переход на
  // другую вкладку меню не должен ни прерывать наблюдение, ни терять
  // результат. Г.95 — разбор РД идёт ТЕМ ЖЕ механизмом, отличается только
  // стороной комплекта: отдельного пути для рабочей документации нет.
  const pdRun = { data: pdRunStatus ?? undefined }
  const rdRun = { data: rdRunStatus ?? undefined }
  const llmCheck = useQuery({ queryKey: ['llm-check'], queryFn: backendApi.checkLlm })
  // Г.97 — ручная правка раздела: после ответа сервера список файлов
  // перечитывается, чтобы источник («указан вручную») был виден сразу.
  const setSection = useMutation({
    mutationFn: ({ id, code }: { id: number; code: string | null }) =>
      backendApi.updateDocumentSection(id, code),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY }) },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось задать раздел', 'error'),
  })
  // Пустой выбор означает «все загруженные» — см. ParseCard.
  const idsFor = (docs: BackendDocument[], selected: number[]) =>
    selected.length ? docs.filter((d) => selected.includes(d.id)).map((d) => d.id)
                    : docs.map((d) => d.id)
  const startPdRun = useMutation({
    mutationFn: () => backendApi.createPdRun(idsFor(beforeDocs, pdSelected), 'before'),
    onSuccess: (run) => setPdRunId('before', run.id),
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось запустить разбор', 'error'),
  })
  const startRdRun = useMutation({
    mutationFn: () => backendApi.createPdRun(idsFor(afterDocs, rdSelected), 'after'),
    onSuccess: (run) => setPdRunId('after', run.id),
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось запустить разбор', 'error'),
  })

  /**
 * Окно разбора результата сверки с моделью (Г.100).
 *
 * Предложение пользователя: инспектор указывает модели на недочёты прямо в
 * разделе проверки. Здесь важно, чего окно НЕ обещает: дообучения весов у нас
 * нет (Г.75), и подпись это говорит прямо. Замечание становится примером в
 * промпте только после отдельного решения человека и только на ДРУГИХ
 * объектах (Г.11/Г.12) — гейт стоит на сервере, но пользователю о нём сказано
 * здесь, иначе кнопка «в примеры» читается как «применить сейчас».
 */
function ReviewDialog({ runId }: { runId: number }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [kind, setKind] = useState('')
  const messages = useQuery({
    queryKey: ['review-messages', runId],
    queryFn: () => backendApi.getReviewMessages(runId),
  })
  const send = useMutation({
    mutationFn: () => backendApi.addReviewMessage(runId, text, kind),
    onSuccess: () => {
      setText('')
      setKind('')
      qc.invalidateQueries({ queryKey: ['review-messages', runId] })
    },
  })
  const approve = useMutation({
    mutationFn: ({ id, approved }: { id: number; approved: boolean }) =>
      backendApi.approveReviewMessage(id, approved),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['review-messages', runId] }),
  })

  return (
    <div className="mt-4 border-t border-surface-line pt-3">
      <h4 className="text-sm font-medium text-ink">Разбор результата с ИИ</h4>
      <p className="mt-1 text-xs text-ink-muted">
        Спросите, почему пункт помечен так, или укажите на ошибку. Замечание сохраняется всегда,
        даже когда модель недоступна. Отмеченное как замечание попадает в журнал наблюдений и,
        по вашему отдельному решению, — в подсказки модели на <span className="font-medium">других</span> объектах.
        Веса модели при этом не меняются: это подстановка примера в запрос, а не обучение.
      </p>

      <div className="mt-3 space-y-2">
        {messages.data?.length === 0 && (
          <p className="text-xs text-ink-muted">Разговор пока не начат.</p>
        )}
        {messages.data?.map((m: ReviewMessage) => (
          <div key={m.id}
            className={m.role === 'inspector'
              ? 'rounded-md border border-surface-line bg-surface px-3 py-2'
              : 'rounded-md border border-surface-line bg-surface-muted/60 px-3 py-2'}>
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-muted">
              <span className="font-medium text-ink">
                {m.role === 'inspector' ? 'Инспектор' : 'ИИ'}
              </span>
              {m.kind && <Chip>{m.kind}</Chip>}
              {m.kind && (
                <button type="button"
                  onClick={() => approve.mutate({ id: m.id, approved: !m.approved })}
                  className="rounded border border-surface-line px-1.5 py-0.5 hover:bg-surface-muted">
                  {m.approved ? '✓ в подсказках модели — отозвать' : 'Взять в подсказки модели'}
                </button>
              )}
            </div>
            {m.text && <p className="mt-1 whitespace-pre-wrap text-sm text-ink">{m.text}</p>}
            {/* Г.10 — «ответа нет» и «связи не было» требуют от инспектора
                разного, поэтому причина названа словами, а не пустотой. */}
            {!m.text && m.no_answer_reason && (
              <p className="mt-1 text-xs italic text-ink-muted">{m.no_answer_reason}</p>
            )}
          </div>
        ))}
      </div>

      <div className="mt-3 space-y-2">
        <textarea
          value={text} onChange={(e) => setText(e.target.value)} rows={3}
          placeholder="Например: «это не нарушение — клапан есть в спецификации на листе 14»"
          className="w-full rounded-md border border-surface-line bg-surface px-3 py-2 text-sm text-ink" />
        <div className="flex flex-wrap items-center gap-2">
          <select value={kind} onChange={(e) => setKind(e.target.value)}
            className="rounded-md border border-surface-line bg-surface px-2 py-1 text-xs text-ink">
            <option value="">вопрос (в журнал наблюдений не идёт)</option>
            {CORRECTION_KINDS.map((k) => <option key={k} value={k}>замечание: {k}</option>)}
          </select>
          <button type="button" disabled={!text.trim() || send.isPending}
            onClick={() => send.mutate()}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            {send.isPending ? 'Отправляем…' : 'Отправить'}
          </button>
        </div>
      </div>
    </div>
  )
}

// Г.96 — третья кнопка: сверка по СОХРАНЁННОМУ разбору ПД.
  // Сверка тоже опрашивается из стора (Г.112): она идёт минутами и не
  // должна обрываться переходом на другую вкладку.
  const complianceRun = { data: complianceRunStatus ?? undefined }
  const startCompliance = useMutation({
    mutationFn: () => // Сверке нужен номер ПРОГОНА разбора (не запись в хранилище): сервер
      // сам возьмёт из него сохранённые требования.
      backendApi.createComplianceRun(pdRunId as number, idsFor(afterDocs, rdSelected)),
    onSuccess: (run) => setComplianceRunId(run.id),
  })

  // Остановка длинного дела (Г.114). Отдельная мутация на каждый вид: они
  // ходят по разным адресам, а сообщение об отказе должно называть своё.
  const stopRun = {
    pd: useMutation({
      mutationFn: () => backendApi.cancelPdRun(pdRunId as number),
      onSuccess: (r) => pushToast(r.detail),
      onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось остановить', 'error'),
    }),
    rd: useMutation({
      mutationFn: () => backendApi.cancelPdRun(rdRunStatus?.id as number),
      onSuccess: (r) => pushToast(r.detail),
      onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось остановить', 'error'),
    }),
    compliance: useMutation({
      mutationFn: () => backendApi.cancelComplianceRun(complianceRunId as number),
      onSuccess: (r) => pushToast(r.detail),
      onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось остановить', 'error'),
    }),
    analysis: useMutation({
      mutationFn: () => backendApi.cancelAnalysisRun(runId as number),
      onSuccess: (r) => pushToast(r.detail),
      onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось остановить', 'error'),
    }),
  }

  const [groupByLabel, setGroupByLabel] = useState(false)
  const groups = (() => {
    const map = new Map<string, BackendFinding[]>()
    for (const f of items) {
      const key = f.label || ''
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(f)
    }
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length)
  })()

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <UploadZone title="1. Проектная документация (ПД)" subtitle="Комплект «до» — эталон, с которым сравниваем"
          docs={beforeDocs} onFiles={(f) => uploadTo('before', f)} onRemove={(id) => removeFrom('before', id)}
          onSetSection={(id, code) => setSection.mutate({ id, code })}
          pending={pendingBefore} />
        <UploadZone title="2. Рабочая / исполнительная документация (РД/ИД)" subtitle="Комплект «после» — что проверяем на соответствие"
          docs={afterDocs} onFiles={(f) => uploadTo('after', f)} onRemove={(id) => removeFrom('after', id)}
          onSetSection={(id, code) => setSection.mutate({ id, code })}
          pending={pendingAfter} />

        <ParseCard
          title="1. Разбор проектной документации"
          subtitle="Требования и способы производства работ + состав тома. Рабочая документация не нужна: её отсутствие не ошибка"
          docs={beforeDocs} run={pdRun.data} pending={startPdRun.isPending}
          onStart={() => startPdRun.mutate()} llmCheck={llmCheck.data}
          selected={pdSelected} onSelect={(ids) => setSelected('before', ids)}
          onStop={() => stopRun.pd.mutate()} stopping={stopRun.pd.isPending} />

        <ParseCard
          title="2. Разбор рабочей документации"
          subtitle="Тот же механизм, другая сторона комплекта. Если связного текста нет — показывается состав тома: листы чертежей, таблицы"
          docs={afterDocs} run={rdRun.data} pending={startRdRun.isPending}
          onStart={() => startRdRun.mutate()}
          selected={rdSelected} onSelect={(ids) => setSelected('after', ids)}
          onStop={() => stopRun.rd.mutate()} stopping={stopRun.rd.isPending} />

        <SectionCard
          title="3. Соответствие РД требованиям ПД"
          subtitle="По списку требований из разбора ПД: что подтверждается текстом или чертежами РД, а что нужно посмотреть глазами">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={!pdRun.data?.store_run_id || afterDocs.length === 0
                        || complianceRun.data?.status === 'running' || startCompliance.isPending}
              onClick={() => startCompliance.mutate()}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
              {complianceRun.data?.status === 'running' || startCompliance.isPending ? 'Сверяем…' : 'Сверить'}
            </button>
            {complianceRun.data?.status === 'running' && (
              <button type="button" onClick={() => stopRun.compliance.mutate()}
                disabled={stopRun.compliance.isPending}
                className="rounded-md border border-surface-line px-3 py-1.5 text-sm text-ink-muted hover:text-danger disabled:opacity-50">
                {stopRun.compliance.isPending ? 'останавливаю…' : 'Остановить'}
              </button>
            )}
            {!pdRun.data?.store_run_id && (
              <span className="text-xs text-ink-muted">Сначала выполните разбор проектной документации.</span>
            )}
            {pdRun.data?.store_run_id && afterDocs.length === 0 && (
              <span className="text-xs text-ink-muted">Загрузите рабочую документацию.</span>
            )}
          </div>

          {/* Прямое предупреждение пользователя: в РД текста мало по природе,
              поэтому «не подтверждено» здесь ожидаемо и НЕ является нарушением
              (Б.6/Г.96). Оговорка стоит до цифр, а не после. */}
          <p className="mb-3 rounded-md border border-surface-line bg-surface-muted/60 px-3 py-2 text-xs text-ink-muted">
            Результат — гипотезы для проверки, не заключение о нарушениях. В рабочей документации
            связного текста мало по природе, поэтому «требует проверки» означает «подтверждения не
            нашлось в доступных данных», а решение принимает инспектор.
          </p>

          {complianceRun.data?.status === 'running' && <Skeleton rows={3} />}
          {complianceRun.data?.status === 'error' && (
            <div className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-sm text-danger">
              {complianceRun.data.error}
            </div>
          )}
          {complianceRun.data?.status === 'done' && (
            <div className="space-y-2">
              <p className="text-xs text-ink-muted">
                Требований проверено: <span className="font-medium text-ink">{complianceRun.data.requirements_total}</span>
                {' · '}ИИ: {complianceRun.data.provider}
              </p>
              <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-md border border-surface-line bg-surface-muted/60 p-3 text-xs leading-relaxed text-ink">
                {complianceRun.data.report}
              </pre>
            </div>
          )}
          {complianceRunId !== null && complianceRun.data?.status === 'done' && (
            <ReviewDialog runId={complianceRunId} />
          )}
        </SectionCard>

        <SectionCard title="Оценка расхождений" subtitle="Автоматический подбор пар листов по разделу (шифру), затем сравнение — без разбивки по помещениям">
          {runId === null && <p className="text-sm text-ink-muted">Запустите анализ, чтобы увидеть расхождения.</p>}
          {/* Данные о работе ИИ — какой провайдер считал и сколько пар реально
              дошло до ответа, — а не только итоговый список находок: без
              этого "существенных расхождений не найдено" неотличимо на глаз
              от "ИИ не ответил ни разу". */}
          {runId !== null && runStatus && runStatus.provider && (
            <div className="mb-3 rounded-md border border-surface-line bg-surface-muted/60 px-3 py-2 text-xs text-ink-muted">
              <p>
                ИИ: <span className="font-medium text-ink">{PROVIDER_LABELS[runStatus.provider as BackendSettings['provider']] ?? runStatus.provider}</span>
                {runStatus.model && <> · {runStatus.model}</>}
                {' · '}пар листов сверено: {runStatus.pairs_llm_ok} из {runStatus.pairs_total || '…'}
                {runStatus.pairs_llm_error > 0 && (
                  <span className="ml-1 font-medium text-critical">· сбоев ИИ: {runStatus.pairs_llm_error}</span>
                )}
              </p>
              {runStatus.status === 'done' && (
                <button className="btn-ghost mt-2 px-2 py-1 text-xs" onClick={() => setPairsOpen((v) => !v)}>
                  {pairsOpen ? 'Скрыть подробности по листам' : 'Подробности по листам'}
                </button>
              )}
              {pairsOpen && runStatus.status === 'done' && (
                pagePairs.isLoading ? <Skeleton rows={2} /> : (
                  <>
                    <input className="input mt-2 h-7 text-xs" placeholder="Фильтр: имя файла или номер листа"
                      value={pairsFilter} onChange={(e) => setPairsFilter(e.target.value)} />
                    <ul className="mt-2 max-h-64 space-y-1 overflow-y-auto">
                      {(pagePairs.data ?? [])
                        .filter((p) => {
                          const q = pairsFilter.trim().toLowerCase()
                          if (!q) return true
                          return p.before_document_name.toLowerCase().includes(q)
                            || p.after_document_name.toLowerCase().includes(q)
                            || String(p.before_page).includes(q) || String(p.after_page).includes(q)
                        })
                        .map((p) => (
                          <li key={p.id}
                            className="flex items-center justify-between gap-2 border-t border-surface-line/60 pt-1 first:border-t-0 first:pt-0">
                            <span className="min-w-0 truncate" title={`${p.before_document_name} → ${p.after_document_name}`}>
                              «{p.before_document_name}» стр.{p.before_page} → «{p.after_document_name}» стр.{p.after_page}
                              {' · '}{PAGE_KIND_LABELS[p.page_kind]}
                              {p.discipline_mismatch && ' · раздел не совпал'}
                            </span>
                            <span className={`shrink-0 ${p.llm_status === 'ok' ? 'text-ink-faint' : 'text-critical'}`} title={p.llm_error ?? undefined}>
                              {p.llm_status === 'ok' ? '✓' : `✕ ${p.llm_error ?? 'ошибка'}`}
                            </span>
                          </li>
                        ))}
                    </ul>
                  </>
                )
              )}
            </div>
          )}
          {runId !== null && runStatus && runStatus.status === 'running' && (
            <div>
              <Skeleton rows={3} />
              <p className="mt-2 text-xs text-ink-faint">
                Обработано пар листов: {runStatus.pairs_done} из {runStatus.pairs_total || '…'}
              </p>
            </div>
          )}
          {runId !== null && runStatus?.status === 'error' && (
            <Empty title="Анализ завершился с ошибкой" hint={runStatus.error ?? undefined} />
          )}
          {runId !== null && runStatus?.status === 'done' && (
            findings.isLoading ? <Skeleton rows={3} /> : items.length === 0 ? (
              <Empty title="Существенных расхождений не найдено" hint={
                runStatus.pairs_llm_error > 0
                  ? `По ${runStatus.pairs_llm_error} из ${runStatus.pairs_total} пар ИИ не ответил — это не то же самое, что «расхождений нет». См. подробности по листам выше.`
                  : 'Проверенные пары листов совпадают по содержанию.'
              } />
            ) : (
              <>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium">
                    Итог: {significantCount} из {items.length} пунктов реально стоит проверить.
                  </p>
                  {items.length > 5 && (
                    <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setGroupByLabel((v) => !v)}>
                      {groupByLabel ? 'Список' : `Группировать по типу (${groups.length})`}
                    </button>
                  )}
                </div>
                {groupByLabel ? (
                  <ul className="space-y-2">
                    {groups.map(([label, group]) => (
                      <FindingGroup key={label} label={label} findings={group}
                        onReview={(id, status) => reviewFinding.mutate({ id, status })} />
                    ))}
                  </ul>
                ) : (
                  <ul className="space-y-2">
                    {items.map((f) => (
                      <FindingRow key={f.id} finding={f}
                        onReview={(status) => reviewFinding.mutate({ id: f.id, status })} />
                    ))}
                  </ul>
                )}
              </>
            )
          )}
        </SectionCard>
      </div>

      <div className="space-y-4">
        <SectionCard title="Настроить ИИ" collapsible defaultOpen={false}>
          <div className="space-y-2">
            <label className="block text-xs text-ink-faint">Провайдер</label>
            <select className="input" value={form.provider}
              onChange={(e) => setForm({ ...form, provider: e.target.value as BackendSettings['provider'] })}>
              {Object.entries(PROVIDER_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            {/* Поля ключа здесь нет намеренно (Г.112): ключ задаётся один раз
                при развёртывании и инспектор его не вводит и не видит. Секрет,
                который нельзя ввести в интерфейсе, нельзя и подсмотреть с
                чужого экрана (Б.5). Показывается только факт: задан или нет.
                Способ назван файлом, а не переменной окружения (Г.113): ключ,
                введённый когда-то в поле, жил в базе и пропал при её
                пересборке — файл в каталоге переживает и обновление, и базу. */}
            <p className="text-xs text-ink-faint">
              Ключ: {settings.data?.api_key_set
                ? 'задан при развёртывании'
                : 'НЕ ЗАДАН — положите файл ключа из личного кабинета в каталог secrets/ рядом с проектом и перезапустите сервер'}
            </p>
            <label className="block text-xs text-ink-faint">Модель</label>
            <input className="input" list="model-suggestions" placeholder={PROVIDER_DEFAULT_MODEL[form.provider]}
              value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
            <datalist id="model-suggestions">
              {MODEL_OPTIONS[form.provider].map((m) => <option key={m} value={m} />)}
            </datalist>
            <button className="btn-primary w-full justify-center" disabled={saveSettings.isPending}
              onClick={() => saveSettings.mutate()}>
              {saveSettings.isPending ? 'Сохранение…' : 'Сохранить'}
            </button>
            <p className="text-xs text-ink-faint">
              Локальная модель по умолчанию — данные не покидают компьютер. Внешний API — быстрее, но данные уходят наружу.
            </p>
          </div>
        </SectionCard>

        <StorageCard />

        <VersionCard />

        <SectionCard title="Запуск анализа">
          <button className="btn-primary w-full justify-center"
            disabled={!readyToRun || run.isPending || runStatus?.status === 'running'}
            onClick={() => run.mutate()}>
            {run.isPending || runStatus?.status === 'running' ? 'Выполняется…' : 'Запустить анализ'}
          </button>
          {!readyToRun && (
            <p className="mt-2 text-xs text-ink-faint">Загрузите хотя бы по одному разобранному файлу с каждой стороны.</p>
          )}
        </SectionCard>
      </div>
    </div>
  )
}
