import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { Chip, ErrorState, SectionCard, Skeleton, Table } from '../components/ui'

interface AuditItem {
  seq: number; occurred_at: string; actor_id: string; actor_role: string; action: string
  entity_type: string; entity_id: string; payload: Record<string, unknown>
  prev_hash: string; record_hash: string
}

export default function AuditLog() {
  const [result, setResult] = useState<{ valid: boolean; reason: string; records?: number
    broken_at?: number } | null>(null)
  const query = useQuery({ queryKey: ['audit'], queryFn: () => api.get<{ items: AuditItem[] }>('/audit') })
  const verify = useMutation({
    mutationFn: () => api.post<{ valid: boolean; reason: string }>('/audit/verify'),
    onSuccess: setResult,
  })

  return (
    <SectionCard title="Журнал аудита"
      subtitle="Записи только добавляются. Каждая содержит хэш предыдущей — изменение обнаруживается проверкой"
      right={
        <button className="btn-primary px-3 py-1.5 text-xs" disabled={verify.isPending}
          onClick={() => verify.mutate()}>
          {verify.isPending ? 'Проверка…' : 'Проверить целостность цепочки'}
        </button>
      }>
      {result && (
        <p className={`mb-3 rounded-md border p-3 text-sm ${result.valid
          ? 'border-minor/40 bg-minor-soft text-minor' : 'border-critical/40 bg-critical-soft text-critical'}`}>
          {result.valid
            ? `Цепочка целостна: проверено записей — ${result.records}. Признаков изменения журнала нет.`
            : `Целостность нарушена на записи № ${result.broken_at}: ${result.reason}`}
        </p>
      )}
      {query.isLoading && <Skeleton rows={8} />}
      {query.isError && <ErrorState error={query.error} retry={() => query.refetch()} />}
      {query.data && (
        <Table head={['№', 'Время', 'Кто', 'Роль', 'Действие', 'Объект записи', 'Хэш предыдущей', 'Хэш записи']}>
          {query.data.items.map((item) => (
            <tr key={item.seq}>
              <td className="td tabular-nums">{item.seq}</td>
              <td className="td whitespace-nowrap text-ink-muted">
                {new Date(item.occurred_at).toLocaleString('ru-RU')}
              </td>
              <td className="td font-mono text-xs">{item.actor_id}</td>
              <td className="td"><Chip>{item.actor_role || '—'}</Chip></td>
              <td className="td">{item.action}</td>
              <td className="td text-xs text-ink-muted">{item.entity_type} {item.entity_id}</td>
              <td className="td font-mono text-xs text-ink-faint">{item.prev_hash}…</td>
              <td className="td font-mono text-xs text-ink-faint">{item.record_hash}…</td>
            </tr>
          ))}
        </Table>
      )}
    </SectionCard>
  )
}
