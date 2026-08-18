import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ErrorState, Hint, SectionCard, Skeleton, StatTile, Table } from '../components/ui'

interface DashboardData {
  saved_hours: number
  saved_money_rub: number
  formula: string
  documents_total: number
  documents_processed: number
  findings_total: number
  findings_critical: number
  assessed: number
  confirmed: number
  confirmation_rate: number | null
  by_object: { id: string; name: string; address: string; stage: string; critical: number
    total: number; documents: number; next_visit: string }[]
  by_severity: Record<string, number>
  by_transition: Record<string, number>
  dynamics: { month: string; findings: number }[]
}

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

export default function Dashboard() {
  const query = useQuery({ queryKey: ['dashboard'], queryFn: () => api.get<DashboardData>('/dashboard') })

  if (query.isLoading) return <div className="card p-6"><Skeleton rows={8} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const data = query.data!
  const maxDynamic = Math.max(...data.dynamics.map((d) => d.findings), 1)

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Сэкономлено человеко-часов" value={data.saved_hours.toLocaleString('ru-RU')}
          unit="ч" tone="accent" hint={data.formula} />
        <StatTile label="Обработано документов"
          value={`${data.documents_processed} / ${data.documents_total.toLocaleString('ru-RU')}`}
          hint="Слева — разобрано системой полностью, справа — всего в контуре объектов." />
        <StatTile label="Гипотез нарушений" value={data.findings_total} />
        <StatTile label="Критических расхождений" value={data.findings_critical} tone="critical" />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <SectionCard className="lg:col-span-2" title="Объекты ближайших выездов"
          subtitle="Сортировка по числу критических расхождений">
          <Table head={['Объект', 'Этап', 'Критических', 'Всего гипотез', 'Документов', 'Выезд']}>
            {[...data.by_object].sort((a, b) => b.critical - a.critical).map((o) => (
              <tr key={o.id} className="hover:bg-surface-muted/60">
                <td className="td min-w-[18rem]">
                  <Link className="font-medium text-accent hover:underline" to={`/objects/${o.id}`}>{o.name}</Link>
                  <span className="block text-xs text-ink-faint">{o.address}</span>
                </td>
                <td className="td text-ink-muted">{o.stage}</td>
                <td className="td tabular-nums font-semibold text-critical">{o.critical}</td>
                <td className="td tabular-nums">{o.total}</td>
                <td className="td tabular-nums text-ink-muted">{o.documents}</td>
                <td className="td whitespace-nowrap text-ink-muted">{o.next_visit}</td>
              </tr>
            ))}
          </Table>
        </SectionCard>

        <SectionCard title="Подтверждение гипотез"
          subtitle="Доля гипотез, подтверждённых инспекторами при проверке">
          <p className="text-4xl font-semibold tabular-nums text-accent">
            {data.confirmation_rate === null ? '—' : `${Math.round(data.confirmation_rate * 100)}%`}
          </p>
          <p className="mt-1 text-sm text-ink-muted">
            оценено {data.assessed} из {data.findings_total}, подтверждено {data.confirmed}
          </p>
          <div className="mt-4 space-y-2">
            {Object.entries(data.by_severity).map(([key, count]) => (
              <div key={key} className="flex items-center gap-2 text-sm">
                <span className="w-28 text-ink-muted">
                  {key === 'critical' ? 'Критические' : key === 'major' ? 'Существенные' : 'Незначительные'}
                </span>
                <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface-muted">
                  <span className={`block h-full rounded-full ${key === 'critical' ? 'bg-critical'
                    : key === 'major' ? 'bg-major' : 'bg-minor'}`}
                    style={{ width: `${(count / data.findings_total) * 100}%` }} />
                </span>
                <span className="w-6 text-right tabular-nums">{count}</span>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>

      <SectionCard title="Динамика выявленных расхождений за 6 месяцев"
        right={<Hint text="Показывает нагрузку на отдел и результативность анализа." />}>
        <div className="flex items-end gap-3">
          {data.dynamics.map((point) => (
            <div key={point.month} className="flex flex-1 flex-col items-center gap-1">
              <span className="text-xs tabular-nums text-ink-muted">{point.findings}</span>
              <div className="w-full rounded-t bg-accent/80"
                style={{ height: `${Math.round(12 + (point.findings / maxDynamic) * 120)}px` }} />
              <span className="text-xs text-ink-faint">
                {MONTHS[Number(point.month.split('-')[1]) - 1]}
              </span>
            </div>
          ))}
        </div>
      </SectionCard>
    </div>
  )
}
