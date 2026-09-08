import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  backendApi, CORRECTION_KINDS, pageImageUrl, type BackendDocument, type BackendFinding,
  type BackendPdRun, type BackendSettings, type LlmCheck, type ReviewMessage,
} from '../backendApi'
import { useApp, type PendingUpload } from '../store'
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
  title, subtitle, docs, run, onStart, pending, llmCheck,
}: {
  title: string
  subtitle: string
  docs: BackendDocument[]
  run: BackendPdRun | undefined
  onStart: () => void
  pending: boolean
  llmCheck?: LlmCheck
}) {
  const running = run?.status === 'running' || pending
  return (
    <SectionCard title={title} subtitle={subtitle}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={docs.length === 0 || running}
          onClick={onStart}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
          {running ? 'Разбираем…' : 'Разобрать документацию'}
        </button>
        {docs.length === 0 && <span className="text-xs text-ink-muted">Сначала загрузите документы выше.</span>}
        {/* Г.91 — состояние связи видно ДО запуска: разбор тома это десятки
            вызовов и минуты, а при оборванной связи каждый молча вернул бы
            пустой результат. */}
        {llmCheck && (
          <span className={`text-xs ${llmCheck.reachable ? 'text-ink-muted' : 'text-danger'}`}>
            {llmCheck.reachable ? `ИИ (${llmCheck.provider}): связь есть` : `ИИ (${llmCheck.provider}): ${llmCheck.message}`}
            {/* Состояние проверки сертификата видно ВСЕГДА, а не только при
                ошибке: «проверка отключена» не должно выглядеть так же, как
                «всё в порядке». */}
            {llmCheck.tls && (
              <span className={llmCheck.tls.includes('ОТКЛЮЧЕНА') ? ' text-critical' : ' text-ink-faint'}>
                {' · '}{llmCheck.tls}
              </span>
            )}
          </span>
        )}
      </div>

      {!run && (
        <p className="text-sm text-ink-muted">
          Загрузите документы и нажмите кнопку. Настраивать ничего не нужно: правила извлечения и модель заданы в системе.
        </p>
      )}
      {run?.status === 'running' && <Skeleton rows={3} />}
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
              disabled={cleanup.isPending} onClick={() => cleanup.mutate(false)}>
              Убрать по сроку хранения
            </button>
            <button type="button" className="btn-ghost px-3 py-1.5 text-sm"
              disabled={cleanup.isPending} onClick={() => cleanup.mutate(true)}>
              Освободить кэш
            </button>
          </div>
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
    pushToast, analysisBeforeDocs: beforeDocs, analysisAfterDocs: afterDocs,
    analysisPendingBefore: pendingBefore, analysisPendingAfter: pendingAfter,
    analysisRunId: runId, analysisRunStatus: runStatus,
    setAnalysisDocs, setAnalysisPending, setAnalysisRunId,
  } = useApp()

  const settings = useQuery({ queryKey: ['backend-settings'], queryFn: backendApi.getSettings })
  const [form, setForm] = useState<BackendSettings>({ provider: 'anthropic', base_url: '', model: '', api_key: '' })
  useEffect(() => { if (settings.data) setForm(settings.data) }, [settings.data])

  const saveSettings = useMutation({
    mutationFn: () => backendApi.updateSettings(form),
    onSuccess: () => { pushToast('Настройки ИИ сохранены'); queryClient.invalidateQueries({ queryKey: ['backend-settings'] }) },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось сохранить настройки', 'error'),
  })

  const uploadTo = async (side: 'before' | 'after', files: FileList | null) => {
    if (!files || !files.length) return
    const setDocs = (updater: Parameters<typeof setAnalysisDocs>[1]) => setAnalysisDocs(side, updater)
    const setPending = (updater: Parameters<typeof setAnalysisPending>[1]) => setAnalysisPending(side, updater)

    // Разбор PDF на бэкенде идёт синхронно в одном HTTP-запросе (см.
    // packages/backend/app/main.py, upload_document) — сотни листов могут
    // занять десятки секунд без единого промежуточного ответа сервера.
    // Показываем всю партию в очереди сразу, а не молчим до первого ответа.
    const fileList = Array.from(files)
    const queued: PendingUpload[] = fileList.map((f) => ({ name: f.name, startedAt: Date.now() }))
    setPending((prev) => [...prev, ...queued])

    for (let i = 0; i < fileList.length; i++) {
      try {
        const doc = await backendApi.uploadDocument(side, fileList[i])
        setDocs((prev) => [...prev, doc])
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

  const removeFrom = async (side: 'before' | 'after', id: number) => {
    try {
      await backendApi.deleteDocument(id)
    } catch { /* уже удалён или недоступен — всё равно убираем из списка */ }
    setAnalysisDocs(side, (prev) => prev.filter((d) => d.id !== id))
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
  const [pdRunId, setPdRunId] = useState<number | null>(null)
  const [rdRunId, setRdRunId] = useState<number | null>(null)
  const pdRun = useQuery({
    queryKey: ['pd-run', pdRunId],
    queryFn: () => backendApi.getPdRun(pdRunId as number),
    enabled: pdRunId !== null,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  })
  // Г.95 — разбор РД идёт ТЕМ ЖЕ механизмом, отличается только стороной
  // комплекта: отдельного пути для рабочей документации в коде нет.
  const rdRun = useQuery({
    queryKey: ['pd-run', rdRunId],
    queryFn: () => backendApi.getPdRun(rdRunId as number),
    enabled: rdRunId !== null,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  })
  const llmCheck = useQuery({ queryKey: ['llm-check'], queryFn: backendApi.checkLlm })
  // Г.97 — ручная правка раздела: после ответа сервера список файлов
  // перечитывается, чтобы источник («указан вручную») был виден сразу.
  const setSection = useMutation({
    mutationFn: ({ id, code }: { id: number; code: string | null }) =>
      backendApi.updateDocumentSection(id, code),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['backend-documents'] }) },
  })
  const startPdRun = useMutation({
    mutationFn: () => backendApi.createPdRun(beforeDocs.map((d) => d.id), 'before'),
    onSuccess: (run) => setPdRunId(run.id),
  })
  const startRdRun = useMutation({
    mutationFn: () => backendApi.createPdRun(afterDocs.map((d) => d.id), 'after'),
    onSuccess: (run) => setRdRunId(run.id),
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
  const [complianceRunId, setComplianceRunId] = useState<number | null>(null)
  const complianceRun = useQuery({
    queryKey: ['compliance-run', complianceRunId],
    queryFn: () => backendApi.getComplianceRun(complianceRunId as number),
    enabled: complianceRunId !== null,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })
  const startCompliance = useMutation({
    mutationFn: () => backendApi.createComplianceRun(pdRunId as number, afterDocs.map((d) => d.id)),
    onSuccess: (run) => setComplianceRunId(run.id),
  })

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
          onStart={() => startPdRun.mutate()} llmCheck={llmCheck.data} />

        <ParseCard
          title="2. Разбор рабочей документации"
          subtitle="Тот же механизм, другая сторона комплекта. Если связного текста нет — показывается состав тома: листы чертежей, таблицы"
          docs={afterDocs} run={rdRun.data} pending={startRdRun.isPending}
          onStart={() => startRdRun.mutate()} />

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
            <label className="block text-xs text-ink-faint">Ключ API</label>
            <input className="input" type="password" value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
            {form.provider === 'gigachat' && (
              <p className="text-xs text-ink-faint">
                Base64-строка от «Client ID:Client Secret» (реквизиты приложения GigaChat API), не сам пароль.
              </p>
            )}
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
