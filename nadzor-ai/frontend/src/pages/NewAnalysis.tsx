import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  backendApi, pageImageUrl, type BackendDocument, type BackendFinding, type BackendSettings,
} from '../backendApi'
import { useApp, type PendingUpload } from '../store'
import { Chip, Empty, SectionCard, SeverityChip, Skeleton } from '../components/ui'

const PAGE_KIND_LABELS: Record<'drawing' | 'text', string> = { drawing: 'чертёж', text: 'текст' }

const PROVIDER_LABELS: Record<BackendSettings['provider'], string> = {
  local: 'Локальная модель (Ollama)', openai: 'OpenAI', anthropic: 'Anthropic', google: 'Google',
  yandexgpt: 'YandexGPT',
}

// Зеркало PROVIDER_DEFAULT_MODELS/MODEL_OPTIONS из packages/backend/app/llm.py —
// реальные, а не выдуманные идентификаторы моделей, чтобы поле "Модель" не было
// полем вслепую. Список подсказок, а не жёсткий выбор: свою модель ввести
// по-прежнему можно (input + datalist), пустое поле берёт дефолт провайдера.
const MODEL_OPTIONS: Record<BackendSettings['provider'], string[]> = {
  local: ['qwen2.5vl:7b', 'qwen2.5vl:32b', 'llama3.2-vision:11b', 'minicpm-v:8b'],
  openai: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini'],
  anthropic: ['claude-sonnet-5', 'claude-opus-5', 'claude-haiku-4-5-20251001'],
  google: ['gemini-2.5-flash', 'gemini-2.5-pro'],
  yandexgpt: ['yandexgpt/latest', 'yandexgpt-lite/latest', 'yandexgpt-32k/latest'],
}

const PROVIDER_DEFAULT_MODEL: Record<BackendSettings['provider'], string> = {
  local: 'qwen2.5vl:7b', openai: 'gpt-4o-mini', anthropic: 'claude-sonnet-5',
  google: 'gemini-2.5-flash', yandexgpt: 'yandexgpt/latest',
}

const CLASSIFICATION_SOURCE_LABELS: Record<string, string> = {
  filename: 'по имени файла', title_page: 'по титульному листу', stamp_text: 'по штампу (текст)',
  stamp_vision: 'по штампу (зрение)', none: 'не определён',
}

function DisciplineBadge({ doc }: { doc: BackendDocument }) {
  if (!doc.discipline_code) {
    return <Chip tone="warn">раздел не определён</Chip>
  }
  return (
    <Chip tone="accent">
      {doc.discipline_code} · {CLASSIFICATION_SOURCE_LABELS[doc.classification_source ?? ''] ?? doc.classification_source}
    </Chip>
  )
}

function UploadZone({
  title, subtitle, docs, onFiles, onRemove, pending,
}: {
  title: string; subtitle: string; docs: BackendDocument[]
  onFiles: (files: FileList | null) => void; onRemove: (id: number) => void
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
                  {doc.name} <span className="text-ink-faint">· {doc.pages} л.</span>
                </span>
                <span className="flex items-center gap-2">
                  <DisciplineBadge doc={doc} />
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
  const [form, setForm] = useState<BackendSettings>({ provider: 'local', base_url: '', model: '', api_key: '' })
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
          pending={pendingBefore} />
        <UploadZone title="2. Рабочая / исполнительная документация (РД/ИД)" subtitle="Комплект «после» — что проверяем на соответствие"
          docs={afterDocs} onFiles={(f) => uploadTo('after', f)} onRemove={(id) => removeFrom('after', id)}
          pending={pendingAfter} />

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
            {form.provider === 'local' ? (
              <>
                <label className="block text-xs text-ink-faint">Адрес (Ollama)</label>
                <input className="input" placeholder="http://localhost:11434/v1"
                  value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
              </>
            ) : form.provider === 'yandexgpt' ? (
              <p className="text-xs text-ink-faint">
                Ключ и Folder ID берутся из <code>.env</code> — тех же, что и в «Настройках моделей и правил».
                Вводить их здесь отдельно не нужно.
              </p>
            ) : (
              <>
                <label className="block text-xs text-ink-faint">Ключ API</label>
                <input className="input" type="password" value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
              </>
            )}
            <label className="block text-xs text-ink-faint">Модель</label>
            <input className="input" list="model-suggestions" placeholder={PROVIDER_DEFAULT_MODEL[form.provider]}
              value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
            <datalist id="model-suggestions">
              {MODEL_OPTIONS[form.provider].map((m) => <option key={m} value={m} />)}
            </datalist>
            {form.provider === 'yandexgpt' && (
              <p className="text-xs text-ink-faint">
                Только текстовое сравнение (акты, спецификации). Чертежи по картинке YandexGPT здесь не читает —
                для них нужна локальная модель или другой провайдер со зрением.
              </p>
            )}
            <button className="btn-primary w-full justify-center" disabled={saveSettings.isPending}
              onClick={() => saveSettings.mutate()}>
              {saveSettings.isPending ? 'Сохранение…' : 'Сохранить'}
            </button>
            <p className="text-xs text-ink-faint">
              Локальная модель по умолчанию — данные не покидают компьютер. Внешний API — быстрее, но данные уходят наружу.
            </p>
          </div>
        </SectionCard>

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
