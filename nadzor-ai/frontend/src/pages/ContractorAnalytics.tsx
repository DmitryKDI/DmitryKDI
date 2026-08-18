import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { DemoNotice, ErrorState, SectionCard, Skeleton, Table } from '../components/ui'
import type { Finding, ObjectBrief } from '../types'

/** Уровень C: витрина на подготовленных данных, помечена как демонстрационная. */
export default function ContractorAnalytics() {
  const objects = useQuery({ queryKey: ['objects'], queryFn: () => api.get<{ items: ObjectBrief[] }>('/objects') })
  const findings = useQuery({ queryKey: ['findings', 'all'], queryFn: () => api.get<{ items: Finding[] }>('/findings') })

  if (objects.isLoading || findings.isLoading) return <div className="card p-6"><Skeleton rows={6} /></div>
  if (objects.isError) return <ErrorState error={objects.error} retry={() => objects.refetch()} />

  const rows = (objects.data?.items ?? []).map((o) => {
    const own = (findings.data?.items ?? []).filter((f) => f.object_id === o.id)
    const critical = own.filter((f) => f.severity === 'critical').length
    return {
      contractor: o.contractor, object: o.name, total: own.length, critical,
      rate: own.length ? Math.round((critical / own.length) * 100) : 0,
    }
  })
  const byContractor = new Map<string, { total: number; critical: number; objects: number }>()
  rows.forEach((r) => {
    const acc = byContractor.get(r.contractor) ?? { total: 0, critical: 0, objects: 0 }
    byContractor.set(r.contractor, {
      total: acc.total + r.total, critical: acc.critical + r.critical, objects: acc.objects + 1,
    })
  })

  return (
    <div className="space-y-4">
      <SectionCard title="Аналитика по подрядчикам"
        subtitle="Доля критических расхождений — основание для повышения внимания при планировании проверок"
        right={<DemoNotice />}>
        <Table head={['Подрядчик', 'Объектов', 'Гипотез', 'Критических', 'Доля критических']}>
          {[...byContractor.entries()].sort((a, b) => b[1].critical - a[1].critical).map(([name, m]) => (
            <tr key={name}>
              <td className="td font-medium">{name}</td>
              <td className="td tabular-nums">{m.objects}</td>
              <td className="td tabular-nums">{m.total}</td>
              <td className="td tabular-nums font-semibold text-critical">{m.critical}</td>
              <td className="td">
                <span className="flex items-center gap-2">
                  <span className="h-2 w-24 overflow-hidden rounded-full bg-surface-muted">
                    <span className="block h-full rounded-full bg-critical"
                      style={{ width: `${m.total ? (m.critical / m.total) * 100 : 0}%` }} />
                  </span>
                  <span className="tabular-nums text-xs">
                    {m.total ? Math.round((m.critical / m.total) * 100) : 0}%
                  </span>
                </span>
              </td>
            </tr>
          ))}
        </Table>
      </SectionCard>
    </div>
  )
}
