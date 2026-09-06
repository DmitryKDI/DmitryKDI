import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { ErrorState, SectionCard, Skeleton, StatTile, Table } from '../components/ui'

interface WorkloadRow {
  user_id: string
  full_name: string
  department: string
  objects: number
  analysed: number
  findings: number
  critical: number
  assessed: number
}

/** Сводка для начальника отдела: распределение объектов и результат по каждому инспектору. */
export default function Workload() {
  const query = useQuery({
    queryKey: ['workload'],
    queryFn: () => api.get<{ items: WorkloadRow[]; totals: Record<string, number> }>('/workload'),
  })
  if (query.isLoading) return <div className="card p-6"><Skeleton rows={6} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const { items, totals } = query.data!
  const maxObjects = Math.max(...items.map((r) => r.objects), 1)

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Объектов в отделе" value={totals.objects}
          hint="Комплекты загружены не по всем объектам — это видно в таблице." />
        <StatTile label="Инспекторов" value={items.length}
          hint={`В среднем ${(totals.objects / Math.max(items.length, 1)).toFixed(1)} объекта на человека`} />
        <StatTile label="Критических расхождений" value={totals.critical} tone="critical" />
        <StatTile label="Оценено инспекторами" value={totals.assessed} tone="accent" />
      </div>

      <SectionCard title="Нагрузка инспекторов"
        subtitle="Сколько объектов у каждого, по скольким загружены комплекты и что найдено"
        right={<button className="btn-ghost px-2 py-1 text-xs" onClick={() => window.print()}>Печать</button>}>
        <Table head={['Инспектор', 'Объектов', 'Проанализировано', 'Гипотез', 'Критических',
          'Оценено', 'Распределение']}>
          {items.map((r) => (
            <tr key={r.user_id} className="hover:bg-surface-muted/60">
              <td className="td">
                <span className="font-medium">{r.full_name}</span>
                <span className="block text-xs text-ink-faint">{r.department}</span>
              </td>
              <td className="td tabular-nums">{r.objects}</td>
              <td className="td tabular-nums">
                {r.analysed}
                {r.analysed < r.objects && <span className="text-ink-faint"> из {r.objects}</span>}
              </td>
              <td className="td tabular-nums">{r.findings || '—'}</td>
              <td className={`td tabular-nums font-semibold ${r.critical ? 'text-critical' : ''}`}>
                {r.critical || '—'}
              </td>
              <td className="td tabular-nums">{r.assessed || '—'}</td>
              <td className="td">
                <span className="block h-2 w-28 overflow-hidden rounded-full bg-surface-muted">
                  <span className="block h-full rounded-full bg-accent"
                    style={{ width: `${(r.objects / maxObjects) * 100}%` }} />
                </span>
              </td>
            </tr>
          ))}
        </Table>
      </SectionCard>
    </div>
  )
}
