import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Chip, Empty, ErrorState, SectionCard, Skeleton, Table, formatDate } from '../components/ui'
import { theme } from '../theme'

interface Run {
  id: string; object_id: string; object_name: string; transitions: string[]
  status: string; stage: string; started_at: string; documents: number; findings: number
  provenance: Record<string, string>
}

export default function AnalysisLog() {
  const query = useQuery({ queryKey: ['runs'], queryFn: () => api.get<{ items: Run[] }>('/runs') })
  if (query.isLoading) return <div className="card p-6"><Skeleton rows={6} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const items = query.data!.items

  return (
    <SectionCard title="Журнал анализа документации"
      subtitle="Каждый запуск сохраняет состав переходов, провайдера и версию промпта"
      right={<Link className="btn-primary px-3 py-1.5 text-xs" to="/analysis/new">Новый анализ</Link>}>
      {items.length === 0 ? (
        <Empty title="Анализ ещё не выполнялся"
          hint="Загрузите комплект документации и выберите переходы для сравнения." />
      ) : (
        <Table head={['Дата', 'Объект', 'Переходы', 'Документов', 'Гипотез', 'Модель', 'Статус']}>
          {items.map((run) => (
            <tr key={run.id} className="hover:bg-surface-muted/60">
              <td className="td whitespace-nowrap">{formatDate(run.started_at)}</td>
              <td className="td">
                <Link className="text-accent hover:underline" to={`/findings?object=${run.object_id}`}>
                  {run.object_name}
                </Link>
              </td>
              <td className="td">
                <span className="flex flex-wrap gap-1">
                  {run.transitions.map((t) => (
                    <span key={t} title={theme.transitions[t]} className="chip border-surface-line
                      bg-surface-muted text-ink-muted">{t}</span>
                  ))}
                </span>
              </td>
              <td className="td tabular-nums">{run.documents}</td>
              <td className="td tabular-nums font-semibold">{run.findings}</td>
              <td className="td text-xs text-ink-muted">
                {run.provenance.provider} · {run.provenance.model}
              </td>
              <td className="td"><Chip tone="accent">{run.stage || run.status}</Chip></td>
            </tr>
          ))}
        </Table>
      )}
    </SectionCard>
  )
}
