import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { backendApi, type BackendDocument } from '../backendApi'
import { Chip, Empty, SectionCard, Skeleton } from '../components/ui'
import { useApp } from '../store'
import { DOCUMENTS_KEY, useDocuments } from '../useDocuments'

function DocumentColumn({
  title,
  subtitle,
  docs,
  side,
  uploading,
  onFiles,
  onDelete,
}: {
  title: string
  subtitle: string
  docs: BackendDocument[]
  side: 'before' | 'after'
  uploading: boolean
  onFiles: (files: FileList | null, side: 'before' | 'after') => void
  onDelete: (id: number) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)

  return (
    <SectionCard title={title} subtitle={subtitle}>
      <div
        className={`rounded-xl border border-dashed p-5 text-center transition ${
          drag ? 'border-accent bg-accent-soft/70' : 'border-surface-line bg-surface-muted/50'
        }`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDrag(false)
          onFiles(e.dataTransfer.files, side)
        }}
      >
        <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-xl bg-surface text-lg shadow-sm">PDF</div>
        <p className="text-sm font-medium text-ink">Перетащите документы сюда</p>
        <p className="mt-1 text-xs text-ink-faint">Раздел и тип листов определяются автоматически</p>
        <button className="btn-ghost mt-3" type="button" disabled={uploading} onClick={() => input.current?.click()}>
          {uploading ? 'Загружаю…' : 'Выбрать PDF'}
        </button>
        <input
          ref={input}
          hidden
          multiple
          type="file"
          accept=".pdf"
          onChange={(e) => {
            onFiles(e.target.files, side)
            e.target.value = ''
          }}
        />
      </div>

      <div className="mt-3 space-y-2">
        {docs.length === 0 && <p className="text-xs text-ink-faint">Документы ещё не загружены.</p>}
        {docs.map((doc) => (
          <div key={doc.id} className="flex items-center gap-3 rounded-lg border border-surface-line bg-surface px-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-ink" title={doc.name}>{doc.name}</div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-faint">
                <span>{doc.pages || '—'} л.</span>
                {doc.discipline_code && <Chip tone="accent">{doc.discipline_code}</Chip>}
                {doc.status === 'parsing' && <Chip tone="warn">обрабатывается</Chip>}
                {doc.status === 'ok' && <Chip>готов</Chip>}
                {doc.status === 'error' && <Chip tone="warn">ошибка разбора</Chip>}
              </div>
            </div>
            <button
              type="button"
              aria-label="Удалить документ"
              className="rounded-md px-2 py-1 text-xs text-ink-faint hover:bg-surface-muted hover:text-critical"
              onClick={() => onDelete(doc.id)}
            >
              Удалить
            </button>
          </div>
        ))}
      </div>
    </SectionCard>
  )
}

function Progress({ label, status, done, total, detail }: {
  label: string
  status?: string | null
  done?: number
  total?: number
  detail?: string | null
}) {
  const share = total && total > 0 ? Math.min(100, Math.round(((done ?? 0) / total) * 100)) : null
  const statusLabel = status === 'done' ? 'готово'
    : status === 'running' ? 'выполняется'
      : status === 'error' ? 'частично / ошибка'
        : status === 'cancelled' ? 'остановлено'
          : 'не запускалось'

  return (
    <div className="rounded-lg border border-surface-line bg-surface-muted/50 px-3 py-2">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-medium text-ink">{label}</span>
        <span className={status === 'error' ? 'text-critical' : 'text-ink-faint'}>{statusLabel}</span>
      </div>
      {status === 'running' && (
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-line">
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${share ?? 18}%` }} />
        </div>
      )}
      {detail && <p className="mt-1 line-clamp-2 text-[11px] text-ink-faint">{detail}</p>}
    </div>
  )
}

export default function Workspace() {
  const qc = useQueryClient()
  const { before, after, isLoading } = useDocuments()
  const {
    setAnalysisRunId,
    analysisRunStatus,
    setTriangulatedRunId,
    triangulatedRunStatus,
    setPdRunId,
    pdRunStatus,
    setComplianceRunId,
    complianceRunStatus,
    pushToast,
  } = useApp()
  const [uploading, setUploading] = useState<'before' | 'after' | null>(null)
  const autoCompliancePdId = useRef<number | null>(null)
  const complianceStartedForPd = useRef<number | null>(null)

  const readyBefore = useMemo(() => before.filter((doc) => doc.status === 'ok'), [before])
  const readyAfter = useMemo(() => after.filter((doc) => doc.status === 'ok'), [after])
  const ready = readyBefore.length > 0 && readyAfter.length > 0
  const busy = Boolean(
    analysisRunStatus?.status === 'running'
    || triangulatedRunStatus?.status === 'running'
    || pdRunStatus?.status === 'running'
    || complianceRunStatus?.status === 'running'
  )

  const upload = async (files: FileList | null, side: 'before' | 'after') => {
    if (!files?.length) return
    setUploading(side)
    try {
      for (const file of Array.from(files)) {
        await backendApi.uploadDocument(side, file)
      }
      await qc.invalidateQueries({ queryKey: DOCUMENTS_KEY })
      pushToast(`Загружено файлов: ${files.length}`)
    } catch (e) {
      pushToast(e instanceof Error ? e.message : 'Не удалось загрузить документы', 'error')
    } finally {
      setUploading(null)
    }
  }

  const remove = useMutation({
    mutationFn: backendApi.deleteDocument,
    onSuccess: () => void qc.invalidateQueries({ queryKey: DOCUMENTS_KEY }),
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось удалить документ', 'error'),
  })

  const fullAnalysis = useMutation({
    mutationFn: async () => {
      const beforeIds = readyBefore.map((doc) => doc.id)
      const afterIds = readyAfter.map((doc) => doc.id)
      return Promise.allSettled([
        backendApi.createAnalysisRun(beforeIds, afterIds),
        backendApi.createTriangulatedRun(beforeIds, afterIds),
        backendApi.createPdRun(beforeIds, 'before'),
      ])
    },
    onSuccess: ([analysis, attention, pd]) => {
      let started = 0
      if (analysis.status === 'fulfilled') {
        setAnalysisRunId(analysis.value.id)
        started += 1
      }
      if (attention.status === 'fulfilled') {
        setTriangulatedRunId(attention.value.id)
        started += 1
      }
      if (pd.status === 'fulfilled') {
        setPdRunId('before', pd.value.id)
        setComplianceRunId(null)
        autoCompliancePdId.current = pd.value.id
        complianceStartedForPd.current = null
        started += 1
      }

      if (started === 3) pushToast('Полный анализ запущен')
      else if (started > 0) pushToast(`Запущено контуров: ${started} из 3. Остальные не стартовали.`, 'error')
      else pushToast('Не удалось запустить анализ', 'error')
    },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось запустить анализ', 'error'),
  })

  useEffect(() => {
    const pdId = autoCompliancePdId.current
    if (!pdId || pdRunStatus?.id !== pdId || !pdRunStatus.store_run_id) return
    if (pdRunStatus.status === 'running' || complianceStartedForPd.current === pdId) return
    if (readyAfter.length === 0) return

    complianceStartedForPd.current = pdId
    void backendApi.createComplianceRun(pdId, readyAfter.map((doc) => doc.id))
      .then((run) => {
        setComplianceRunId(run.id)
        pushToast('Сверка требований с РД запущена автоматически')
      })
      .catch((e) => {
        pushToast(e instanceof Error ? e.message : 'Не удалось автоматически запустить сверку требований', 'error')
      })
  }, [pdRunStatus, readyAfter, setComplianceRunId, pushToast])

  if (isLoading) return <Skeleton rows={8} />

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="rounded-2xl border border-surface-line bg-surface p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-ink">Документы объекта</h2>
            <p className="mt-1 max-w-2xl text-sm text-ink-muted">
              Загрузите исходную и проверяемую документацию. Сервис сам запускает сравнение листов,
              поиск точек контроля и извлечение требований; технические параметры скрыты на сервере.
            </p>
          </div>
          <button
            className="btn-primary min-w-48 justify-center"
            type="button"
            disabled={!ready || fullAnalysis.isPending || busy}
            onClick={() => fullAnalysis.mutate()}
          >
            {fullAnalysis.isPending || busy ? 'Анализ выполняется…' : 'Запустить полный анализ'}
          </button>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <DocumentColumn
          title="Проектная документация"
          subtitle="Исходные требования и проектные решения"
          docs={before}
          side="before"
          uploading={uploading === 'before'}
          onFiles={upload}
          onDelete={(id) => remove.mutate(id)}
        />
        <DocumentColumn
          title="Рабочая / исполнительная документация"
          subtitle="То, что сравниваем с проектом и проверяем перед выездом"
          docs={after}
          side="after"
          uploading={uploading === 'after'}
          onFiles={upload}
          onDelete={(id) => remove.mutate(id)}
        />
      </div>

      {!ready && (
        <Empty
          title="Для анализа нужны обе стороны"
          hint="Дождитесь окончания первичного разбора хотя бы одного файла ПД и одного файла РД/ИД."
        />
      )}

      <SectionCard
        title="Ход проверки"
        subtitle="Один запуск — четыре понятных инспектору этапа. Сверка требований стартует сама после извлечения ПД.">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <Progress
            label="Сравнение листов"
            status={analysisRunStatus?.status}
            done={analysisRunStatus?.pairs_done}
            total={analysisRunStatus?.pairs_total}
            detail={analysisRunStatus?.error}
          />
          <Progress
            label="Точки контроля"
            status={triangulatedRunStatus?.status}
            detail={triangulatedRunStatus?.error}
          />
          <Progress
            label="Извлечение требований"
            status={pdRunStatus?.status}
            done={pdRunStatus?.units_done}
            total={pdRunStatus?.units_total}
            detail={pdRunStatus?.error}
          />
          <Progress
            label="Сверка ПД → РД"
            status={complianceRunStatus?.status}
            done={complianceRunStatus?.units_done}
            total={complianceRunStatus?.units_total}
            detail={complianceRunStatus?.error}
          />
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs text-ink-faint">
          <span>ПД: {readyBefore.length} файлов</span>
          <span>·</span>
          <span>РД/ИД: {readyAfter.length} файлов</span>
          <span>·</span>
          <span>Результат — гипотезы для проверки, не юридический вывод о нарушении.</span>
        </div>
      </SectionCard>
    </div>
  )
}
