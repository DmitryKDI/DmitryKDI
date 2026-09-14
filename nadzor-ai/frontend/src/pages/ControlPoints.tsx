import { useNavigate } from 'react-router-dom'
import { Chip, Empty, SectionCard, StatTile } from '../components/ui'
import { useApp } from '../store'
import { useDocuments } from '../useDocuments'

function labelDomain(domain: string): string {
  const labels: Record<string, string> = {
    room: 'Помещения', rooms: 'Помещения',
    equipment: 'Оборудование', composition: 'Комплектность',
    requirement: 'Требования', requirements: 'Требования',
  }
  return labels[domain] ?? domain
}

export default function ControlPoints() {
  const navigate = useNavigate()
  const { before, after } = useDocuments()
  const run = useApp((s) => s.triangulatedRunStatus)
  const result = run?.result
  const tickets = result?.escalation_tickets ?? []
  const candidates = result?.triangulation?.candidates ?? []
  const confirmed = result?.triangulation?.confirmed ?? []
  const totalPages = [...before, ...after].reduce((sum, d) => sum + (d.pages || 0), 0)

  if (!run || !result) {
    return (
      <Empty
        title="Точки контроля ещё не сформированы"
        hint="Загрузите ПД и РД/ИД и запустите полный анализ. Система соберёт только те места, где инспектору действительно нужно внимание."
        action={<button className="btn-primary mt-2" onClick={() => navigate('/documents')}>Перейти к документам</button>}
      />
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Проверено листов" value={totalPages} />
        <StatTile label="Требуют проверки" value={tickets.length + candidates.length} tone="critical" />
        <StatTile label="Подтверждено источниками" value={confirmed.length} tone="accent" />
        <StatTile label="Непроверенные контуры" value={result.not_run?.length ?? 0} />
      </div>

      <SectionCard
        title="Приоритетные точки контроля"
        subtitle="Это очередь инспектора: сначала вопросы с недостаточными доказательствами, затем подтверждённые документами изменения."
        right={<button className="btn-ghost" onClick={() => navigate('/evidence')}>Открыть доказательства</button>}>
        {tickets.length === 0 && candidates.length === 0 ? (
          <p className="text-sm text-ink-muted">По текущему прогону дополнительных точек контроля не сформировано.</p>
        ) : (
          <div className="space-y-3">
            {tickets.map((ticket, index) => (
              <article key={`ticket-${ticket.domain}-${ticket.key}-${index}`}
                className="rounded-lg border border-critical/30 bg-critical/5 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone="warn">Нужна проверка</Chip>
                  <span className="text-xs font-medium text-ink-muted">{labelDomain(ticket.domain)}</span>
                  <span className="text-sm font-semibold text-ink">{ticket.key || 'без обозначения'}</span>
                </div>
                <p className="mt-2 text-sm text-ink">{ticket.question}</p>
                {ticket.context.length > 0 && (
                  <ul className="mt-2 space-y-1 text-xs text-ink-muted">
                    {ticket.context.slice(0, 4).map((line, i) => <li key={i}>• {line}</li>)}
                  </ul>
                )}
                <div className="mt-3 flex flex-wrap gap-2 text-xs text-ink-faint">
                  {ticket.sources_present.length > 0 && <span>Есть: {ticket.sources_present.join(', ')}</span>}
                  {ticket.sources_missing.length > 0 && <span>Не хватает: {ticket.sources_missing.join(', ')}</span>}
                </div>
              </article>
            ))}

            {candidates.map((item, index) => (
              <article key={`candidate-${item.domain}-${item.key}-${index}`}
                className="rounded-lg border border-major/30 bg-major-soft/30 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone="warn">Требует подтверждения</Chip>
                  <span className="text-xs font-medium text-ink-muted">{labelDomain(item.domain)}</span>
                  <span className="text-sm font-semibold text-ink">{item.key || 'без обозначения'}</span>
                </div>
                {item.details.length > 0 && (
                  <p className="mt-2 text-sm text-ink-muted">{item.details.slice(0, 2).join(' · ')}</p>
                )}
                <p className="mt-2 text-xs text-ink-faint">Источники: {item.sources.join(', ') || 'один источник'}</p>
              </article>
            ))}
          </div>
        )}
      </SectionCard>

      {confirmed.length > 0 && (
        <SectionCard title="Подтверждено несколькими источниками" collapsible defaultOpen={false}>
          <div className="space-y-2">
            {confirmed.map((item, index) => (
              <div key={`confirmed-${item.domain}-${item.key}-${index}`}
                className="rounded-md border border-surface-line bg-surface-muted/40 px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone="accent">Подтверждено</Chip>
                  <span className="font-medium">{labelDomain(item.domain)} · {item.key}</span>
                </div>
                {item.details.length > 0 && <p className="mt-1 text-xs text-ink-muted">{item.details.slice(0, 2).join(' · ')}</p>}
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      <p className="text-xs text-ink-faint">Выводы системы являются гипотезами для проверки и не заменяют решение инспектора.</p>
    </div>
  )
}
