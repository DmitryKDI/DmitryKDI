import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { backendApi, type BackendFinding } from '../backendApi'
import { Chip, Empty, SectionCard, SeverityChip, Skeleton } from '../components/ui'
import { useApp } from '../store'
import { useDocuments } from '../useDocuments'

function Kpi({ label, value, hint, to, tone = 'neutral' }: {
  label: string
  value: number | string
  hint?: string
  to: string
  tone?: 'neutral' | 'critical' | 'major' | 'ok'
}) {
  const toneClass = tone === 'critical'
    ? 'text-critical'
    : tone === 'major'
      ? 'text-major'
      : tone === 'ok'
        ? 'text-minor'
        : 'text-ink'
  return (
    <Link
      to={to}
      aria-label={`${label}: ${value}`}
      className="group rounded-xl border border-surface-line bg-surface p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-accent/35 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-accent/30"
    >
      <div className={`text-2xl font-semibold tracking-tight ${toneClass}`}>{value}</div>
      <div className="mt-1 flex items-center gap-1 text-sm font-medium text-ink">
        <span>{label}</span>
        <span className="text-xs text-ink-faint transition group-hover:translate-x-0.5 group-hover:text-accent">→</span>
      </div>
      {hint && <div className="mt-1 text-xs text-ink-faint">{hint}</div>}
    </Link>
  )
}

function FindingLine({ finding }: { finding: BackendFinding }) {
  return (
    <li className="flex items-start gap-3 border-t border-surface-line py-3 first:border-t-0 first:pt-0">
      <div className="mt-0.5">{finding.severity ? <SeverityChip value={finding.severity} /> : <Chip>проверить</Chip>}</div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-ink">{finding.label || 'Выявлено расхождение'}</span>
          {finding.after_page && <span className="text-xs text-ink-faint">лист {finding.after_page}</span>}
        </div>
        <p className="mt-1 text-sm text-ink-muted">{finding.change_text}</p>
        {finding.field_check && (
          <p className="mt-1 text-xs text-accent">На объекте: {finding.field_check}</p>
        )}
      </div>
      <Link className="shrink-0 text-xs text-accent hover:underline" to="/evidence">Доказательства →</Link>
    </li>
  )
}

export default function Dashboard() {
  const { before, after, isLoading } = useDocuments()
  const {
    analysisRunId,
    analysisRunStatus,
    triangulatedRunStatus,
    complianceRunStatus,
  } = useApp()

  const findings = useQuery({
    queryKey: ['dashboard-findings', analysisRunId],
    queryFn: () => backendApi.listFindings(analysisRunId as number),
    enabled: analysisRunId != null && analysisRunStatus?.status === 'done',
  })

  const items = findings.data ?? []
  const critical = items.filter((item) => item.severity === 'critical').length
  const major = items.filter((item) => item.severity === 'major').length
  const tickets = triangulatedRunStatus?.result?.escalation_tickets?.length ?? 0
  const confirmed = triangulatedRunStatus?.result?.triangulation?.confirmed?.length ?? 0
  const pages = [...before, ...after].reduce((sum, doc) => sum + (doc.pages || 0), 0)
  const processed = [...before, ...after].filter((doc) => doc.status === 'ok').length

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="overflow-hidden rounded-2xl border border-surface-line bg-surface shadow-sm">
        <div className="flex flex-col gap-5 p-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Chip tone="accent">Строительный контроль</Chip>
              <span className="text-xs text-ink-faint">Текущий комплект документации</span>
            </div>
            <h2 className="text-2xl font-semibold tracking-tight text-ink">Рабочее место инспектора</h2>
            <p className="mt-1 max-w-2xl text-sm text-ink-muted">
              Система сопоставляет ПД, РД и исполнительные материалы и выводит только точки,
              которые стоит проверить в документах или на объекте.
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-full bg-accent-soft px-3 py-1 font-medium text-accent">ПД</span>
              <span className="text-ink-faint">→</span>
              <span className="rounded-full bg-surface-muted px-3 py-1 font-medium text-ink-muted">РД</span>
              <span className="text-ink-faint">→</span>
              <span className="rounded-full bg-surface-muted px-3 py-1 font-medium text-ink-muted">ИД</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link className="btn-ghost" to="/documents">Документы</Link>
            <Link className="btn-primary" to="/documents">Запустить анализ</Link>
          </div>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi to="/documents" label="Проверено листов" value={isLoading ? '—' : pages} hint={`${processed} документов готовы`} />
        <Kpi to="/evidence" label="Критических точек" value={critical} hint={critical ? 'в первую очередь' : 'не выявлено'} tone="critical" />
        <Kpi to="/evidence" label="Существенных" value={major} hint="требуют внимания" tone="major" />
        <Kpi to="/attention" label="Требуют проверки" value={tickets} hint="очередь инспектора" tone="major" />
        <Kpi to="/attention" label="Подтверждено источниками" value={confirmed} hint="2+ независимых сигнала" tone="ok" />
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <SectionCard
          title="Приоритетные точки контроля"
          subtitle="Сначала то, что потенциально сильнее влияет на безопасность и соответствие проекту"
          right={<Link className="text-xs text-accent hover:underline" to="/attention">Смотреть все →</Link>}
        >
          {analysisRunStatus?.status === 'running' && <Skeleton rows={5} />}
          {analysisRunStatus?.status === 'error' && (
            <Empty title="Анализ завершился с ошибкой" hint={analysisRunStatus.error ?? undefined} />
          )}
          {analysisRunStatus?.status === 'done' && findings.isLoading && <Skeleton rows={5} />}
          {analysisRunStatus?.status === 'done' && !findings.isLoading && items.length === 0 && (
            <Empty title="Приоритетных расхождений не найдено" hint="Это относится только к реально проверенным парам листов." />
          )}
          {items.length > 0 && (
            <ol>
              {items.slice(0, 7).map((finding) => <FindingLine key={finding.id} finding={finding} />)}
            </ol>
          )}
          {!analysisRunStatus && (
            <Empty
              title="Анализ ещё не запускался"
              hint="Загрузите ПД и РД/ИД. Технические настройки модели скрыты: инспектор работает только с документами и результатом."
              action={<Link className="btn-primary mt-3" to="/documents">Загрузить документы</Link>}
            />
          )}
        </SectionCard>

        <div className="space-y-4">
          <SectionCard title="Состояние анализа">
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <span className="text-ink-muted">ПД</span>
                <Chip tone={before.some((d) => d.status === 'ok') ? 'accent' : 'warn'}>{before.length} файлов</Chip>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-ink-muted">РД / ИД</span>
                <Chip tone={after.some((d) => d.status === 'ok') ? 'accent' : 'warn'}>{after.length} файлов</Chip>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-ink-muted">Сравнение листов</span>
                <span className="text-xs text-ink-faint">{analysisRunStatus?.status ?? 'не запускалось'}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-ink-muted">Карта контроля</span>
                <span className="text-xs text-ink-faint">{triangulatedRunStatus?.status ?? 'не запускалась'}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-ink-muted">Текстовая сверка</span>
                <span className="text-xs text-ink-faint">{complianceRunStatus?.status ?? 'не запускалась'}</span>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Принцип работы">
            <p className="text-sm leading-relaxed text-ink-muted">
              ИИ не выносит юридический вердикт. Он показывает расхождение, его источник
              и действие для инспектора. «Не найдено подтверждение» не означает «есть нарушение».
            </p>
          </SectionCard>
        </div>
      </div>
    </div>
  )
}
