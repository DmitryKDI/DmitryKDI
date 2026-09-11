import { useMemo, useRef, useState } from 'react'
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

function Progress({ label, status, done, total }: {
  label: string
  status?: string | null
  done?: number
  total?: number
}) {
  const share = total && total > 0 ? Math.min(100, Math.round(((done ?? 0) / total) * 100)) : null
  return (
    <div className="rounded-lg border border-surface-line bg-surface-muted/50 px-3 py-2">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-medium text-ink">{label}</span>
        <span className="text-ink-faint">{status || 'не запускалось'}</span>
      </div>
      {status === 'running' && (
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-line">
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${share ?? 18}%` }} />
        </div>
      )}
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
    pdRunId,
    pdRunStatus,
    setComplianceRunId,
    complianceRunStatus,
    pushToast,
  } = useApp()
  const [uploading, setUploading] = useState<'before' | 'after' | null>(null)

  const readyBefore = useMemo(() => before.filter((doc) => doc.status === 'ok'), [before])
  const readyAfter = useMemo(() => after.filter((doc) => doc.status === 'ok'), [after])
  const ready = readyBefore.length > 0 && readyAfter.length > 0

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
      const [analysis, attention] = await Promise.all([
        backendApi.createAnalysisRun(beforeIds, afterIds),
        backendApi.createTriangulatedRun(beforeIds, afterIds),
      ])
      return { analysis, attention }
    },
    onSuccess: ({ analysis, attention }) => {
      setAnalysisRunId(analysis.id)
      setTriangulatedRunId(attention.id)
      pushToast('Анализ запущен')
    },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось запустить анализ', 'error'),
  })

  const extractPd = useMutation({
    mutationFn: () => backendApi.createPdRun(readyBefore.map((doc) => doc.id), 'before'),
    onSuccess: (run) => {
      setPdRunId('before', run.id)
      pushToast('Извлечение требований ПД запущено')
    },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось запустить разбор ПД', 'error'),
  })

  const compliance = useMutation({
    mutationFn: () => backendApi.createComplianceRun(pdRunId as number, readyAfter.map((doc) => doc.id)),
    onSuccess: (run) => {
      setComplianceRunId(run.id)
      pushToast('Текстовая сверка запущена')
    },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось запустить сверку', 'error'),
  })

  if (isLoading) return <Skeleton rows={8} />

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="rounded-2xl border border-surface-line bg-surface p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-ink">Документы объекта</h2>
            <p className="mt-1 max-w-2xl text-sm text-ink-muted">
              Загрузите исходную и проверяемую документацию. Выбор модели, лимиты и технические параметры
              вынесены из рабочего интерфейса и настраиваются на сервере.
            </p>
          </div>
          <button
            className="btn-primary min-w-48 justify-center"
            type="button"
            disabled={!ready || fullAnalysis.isPending || analysisRunStatus?.status === 'running'}
            onClick={() => fullAnalysis.mutate()}
          >
            {fullAnalysis.isPending || analysisRunStatus?.status === 'running' ? 'Анализ выполняется…' : 'Запустить полный анализ'}
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

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <SectionCard title="Ход проверки" subtitle="Только понятные инспектору этапы — технические вызовы модели скрыты">
          <div className="grid gap-2 sm:grid-cols-2">
            <Progress
              label="Сравнение листов"
              status={analysisRunStatus?.status}
              done={analysisRunStatus?.pairs_done}
              total={analysisRunStatus?.pairs_total}
            />
            <Progress label="Точки контроля" status={triangulatedRunStatus?.status} />
            <Progress
              label="Извлечение требований"
              status={pdRunStatus?.status}
              done={pdRunStatus?.units_done}
              total={pdRunStatus?.units_total}
            />
            <Progress
              label="Сверка требований"
              status={complianceRunStatus?.status}
              done={complianceRunStatus?.units_done}
              total={complianceRunStatus?.units_total}
            />
          </div>
        </SectionCard>

        <SectionCard title="Дополнительная текстовая проверка" subtitle="Не обязательна для запуска основного анализа">
          <div className="space-y-2">
            <button
              className="btn-ghost w-full justify-center"
              type="button"
              disabled={readyBefore.length === 0 || extractPd.isPending || pdRunStatus?.status === 'running'}
              onClick={() => extractPd.mutate()}
            >
              {pdRunStatus?.status === 'running' ? 'Извлекаю требования…' : 'Извлечь требования ПД'}
            </button>
            <button
              className="btn-ghost w-full justify-center"
              type="button"
              disabled={!pdRunStatus?.store_run_id || readyAfter.length === 0 || compliance.isPending || complianceRunStatus?.status === 'running'}
              onClick={() => compliance.mutate()}
            >
              {complianceRunStatus?.status === 'running' ? 'Сверяю…' : 'Сверить требования с РД'}
            </button>
            <p className="text-xs leading-relaxed text-ink-faint">
              Основной сценарий — одна кнопка «Запустить полный анализ». Эти действия оставлены как отдельный
              доказательный контур для текстовых разделов ПД.
            </p>
          </div>
        </SectionCard>
      </div>
    </div>
  )
}
