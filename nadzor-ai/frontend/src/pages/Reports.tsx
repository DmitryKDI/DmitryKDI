import { Link } from 'react-router-dom'
import { Empty, SectionCard } from '../components/ui'
import { useApp } from '../store'

export default function Reports() {
  const { complianceRunStatus, triangulatedRunStatus } = useApp()
  const report = complianceRunStatus?.report?.trim() ?? ''
  const tickets = triangulatedRunStatus?.result?.escalation_tickets ?? []

  return (
    <div className="mx-auto max-w-[1200px] space-y-5">
      <section className="rounded-2xl border border-surface-line bg-surface p-5 shadow-sm no-print">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-ink">Отчёты инспектора</h2>
            <p className="mt-1 text-sm text-ink-muted">
              Короткий результат проверки: что требует внимания, на основании чего и что проверить на объекте.
            </p>
          </div>
          <button className="btn-ghost" type="button" onClick={() => window.print()} disabled={!report && tickets.length === 0}>
            Печать / PDF
          </button>
        </div>
      </section>

      {tickets.length > 0 && (
        <SectionCard title="Задание на проверку" subtitle={`${tickets.length} пунктов требуют подтверждения инспектором`}>
          <ol className="space-y-3">
            {tickets.map((ticket, index) => (
              <li key={`${ticket.domain}:${ticket.key}`} className="rounded-xl border border-surface-line p-4">
                <div className="flex items-start gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-ink">{ticket.key || 'Точка контроля'}</div>
                    <p className="mt-1 text-sm text-ink-muted">{ticket.question}</p>
                    {ticket.context.length > 0 && (
                      <p className="mt-2 text-xs text-ink-faint">Основание: {ticket.context.join(' · ')}</p>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </SectionCard>
      )}

      {report && (
        <SectionCard title="Текстовая сверка ПД → РД" subtitle="Рабочий отчёт по извлечённым требованиям">
          <pre className="whitespace-pre-wrap rounded-xl border border-surface-line bg-surface-muted/40 p-4 text-sm leading-relaxed text-ink">
            {report}
          </pre>
        </SectionCard>
      )}

      {!report && tickets.length === 0 && (
        <Empty
          title="Отчёт ещё не сформирован"
          hint="Запустите анализ документации. После завершения здесь появится задание на проверку и текстовая сверка, если она выполнялась."
          action={<Link className="btn-primary mt-3" to="/documents">Перейти к документам</Link>}
        />
      )}

      {(report || tickets.length > 0) && (
        <p className="text-xs text-ink-faint">
          Выводы системы являются информационной поддержкой инспектора и сами по себе не являются заключением о нарушении.
        </p>
      )}
    </div>
  )
}
