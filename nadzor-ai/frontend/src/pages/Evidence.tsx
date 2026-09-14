import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { backendApi, pageImageUrl, type BackendFinding } from '../backendApi'
import { Chip, Empty, SectionCard, SeverityChip, Skeleton } from '../components/ui'
import { useApp } from '../store'

function Sheet({ title, documentId, page, tone }: {
  title: string
  documentId: number | null
  page: number | null
  tone: 'before' | 'after'
}) {
  if (!documentId || !page) {
    return (
      <div className="flex min-h-72 items-center justify-center rounded-xl border border-dashed border-surface-line bg-surface-muted/40 text-sm text-ink-faint">
        Лист-источник не указан
      </div>
    )
  }
  return (
    <a
      href={pageImageUrl(documentId, page)}
      target="_blank"
      rel="noreferrer"
      className="group block overflow-hidden rounded-xl border border-surface-line bg-surface"
    >
      <div className="flex items-center justify-between border-b border-surface-line px-3 py-2">
        <span className="text-sm font-medium text-ink">{title}</span>
        <Chip tone={tone === 'before' ? 'neutral' : 'accent'}>лист {page}</Chip>
      </div>
      <div className="relative bg-surface-muted p-2">
        <img
          src={pageImageUrl(documentId, page)}
          alt={`${title}, лист ${page}`}
          className="h-[420px] w-full rounded-lg bg-white object-contain shadow-sm transition group-hover:scale-[1.005]"
        />
      </div>
    </a>
  )
}

function EvidenceCard({ finding }: { finding: BackendFinding }) {
  return (
    <SectionCard
      title={finding.label || 'Выявленное расхождение'}
      subtitle="Система показывает источник вывода, а решение остаётся за инспектором"
      right={finding.severity ? <SeverityChip value={finding.severity} /> : <Chip>проверить</Chip>}
    >
      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_320px_minmax(0,1fr)]">
        <Sheet
          title="Исходная документация"
          documentId={finding.before_document_id}
          page={finding.before_page}
          tone="before"
        />

        <div className="rounded-xl border border-surface-line bg-surface-muted/45 p-4">
          <div className="space-y-4 text-sm">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Что изменилось</div>
              <p className="mt-1 leading-relaxed text-ink">{finding.change_text}</p>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Почему это показано</div>
              <p className="mt-1 leading-relaxed text-ink-muted">
                Расхождение найдено при сопоставлении доступных источников. Оно является гипотезой для проверки,
                а не автоматически установленным нарушением.
              </p>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Источник</div>
              <p className="mt-1 text-ink-muted">
                {finding.before_page ? `лист ${finding.before_page}` : 'источник не указан'}
                {' → '}
                {finding.after_page ? `лист ${finding.after_page}` : 'лист не указан'}
                {' · '}{finding.kind === 'vision' ? 'визуальное сравнение' : 'текстовая сверка'}
              </p>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Рекомендация инспектору</div>
              <p className="mt-1 font-medium leading-relaxed text-accent">
                {finding.field_check || 'Проверить расхождение по исходным листам и при необходимости на объекте.'}
              </p>
            </div>
          </div>
        </div>

        <Sheet
          title="Проверяемая документация"
          documentId={finding.after_document_id}
          page={finding.after_page}
          tone="after"
        />
      </div>
    </SectionCard>
  )
}

export default function Evidence() {
  const { analysisRunId, analysisRunStatus } = useApp()
  const findings = useQuery({
    queryKey: ['evidence-findings', analysisRunId],
    queryFn: () => backendApi.listFindings(analysisRunId as number),
    enabled: analysisRunId != null && analysisRunStatus?.status === 'done',
  })

  if (analysisRunStatus?.status === 'running') {
    return <div className="mx-auto max-w-[1500px]"><Skeleton rows={8} /></div>
  }

  if (analysisRunStatus?.status === 'error') {
    return (
      <div className="mx-auto max-w-[1500px]">
        <Empty title="Анализ завершился с ошибкой" hint={analysisRunStatus.error ?? undefined} />
      </div>
    )
  }

  if (!analysisRunId) {
    return (
      <div className="mx-auto max-w-[1500px]">
        <Empty
          title="Доказательств пока нет"
          hint="Сначала запустите сравнение документации. После анализа здесь появятся пары листов и обоснование каждой точки контроля."
          action={<Link className="btn-primary mt-3" to="/documents">Перейти к документам</Link>}
        />
      </div>
    )
  }

  const items = findings.data ?? []
  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="rounded-2xl border border-surface-line bg-surface p-5 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-ink">Доказательства</h2>
            <p className="mt-1 text-sm text-ink-muted">
              Синхронный просмотр исходного и проверяемого листа с объяснением, что именно привлекло внимание системы.
            </p>
          </div>
          <div className="rounded-lg border border-surface-line bg-surface-muted/50 px-3 py-2 text-xs text-ink-muted">
            Выводы носят характер гипотез и требуют проверки инспектором.
          </div>
        </div>
      </section>

      {findings.isLoading && <Skeleton rows={8} />}
      {!findings.isLoading && items.length === 0 && (
        <Empty title="Расхождений для просмотра нет" hint="Проверенные пары листов не дали находок, сохранённых как точки контроля." />
      )}
      {items.map((finding) => <EvidenceCard key={finding.id} finding={finding} />)}
    </div>
  )
}
