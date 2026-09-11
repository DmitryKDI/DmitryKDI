import { useQuery } from '@tanstack/react-query'
import { backendApi } from '../backendApi'
import { Chip, Empty, SectionCard, Skeleton } from '../components/ui'

function statusLabel(status: string) {
  if (status === 'done') return 'завершён'
  if (status === 'running') return 'выполняется'
  if (status === 'cancelled') return 'остановлен'
  if (status === 'error') return 'неполный / ошибка'
  return status
}

export default function History() {
  const runs = useQuery({ queryKey: ['pd-runs-history'], queryFn: backendApi.listPdRuns })

  return (
    <div className="mx-auto max-w-[1200px] space-y-5">
      <section className="rounded-2xl border border-surface-line bg-surface p-5 shadow-sm">
        <h2 className="text-xl font-semibold text-ink">История анализа</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Последние разборы документации. История хранится на сервере и не зависит от браузера инспектора.
        </p>
      </section>

      <SectionCard title="Прогоны разбора" subtitle="Последние сохранённые операции с документацией">
        {runs.isLoading && <Skeleton rows={6} />}
        {runs.error && <Empty title="История недоступна" hint={runs.error instanceof Error ? runs.error.message : undefined} />}
        {runs.data?.length === 0 && <Empty title="История пока пуста" />}
        {runs.data && runs.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-surface-line text-left text-xs uppercase tracking-wide text-ink-faint">
                  <th className="px-3 py-2">№</th>
                  <th className="px-3 py-2">Дата</th>
                  <th className="px-3 py-2">Сторона</th>
                  <th className="px-3 py-2">Статус</th>
                  <th className="px-3 py-2">Требований</th>
                  <th className="px-3 py-2">Прогресс</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((run) => (
                  <tr key={run.id} className="border-b border-surface-line/70 last:border-0">
                    <td className="px-3 py-3 font-medium text-ink">#{run.id}</td>
                    <td className="px-3 py-3 text-ink-muted">{new Date(`${run.created_at}Z`).toLocaleString('ru-RU')}</td>
                    <td className="px-3 py-3"><Chip>{run.side === 'before' ? 'ПД' : 'РД / ИД'}</Chip></td>
                    <td className="px-3 py-3"><Chip tone={run.status === 'done' ? 'accent' : run.status === 'error' ? 'warn' : 'neutral'}>{statusLabel(run.status)}</Chip></td>
                    <td className="px-3 py-3 text-ink-muted">{run.requirements_total}</td>
                    <td className="px-3 py-3 text-ink-muted">
                      {run.units_total ? `${run.units_done} / ${run.units_total}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  )
}
