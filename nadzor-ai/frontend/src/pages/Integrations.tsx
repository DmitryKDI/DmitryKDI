import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { Chip, DemoNotice, ErrorState, SectionCard, Skeleton, Table } from '../components/ui'

interface Integration {
  code: string; title: string; purpose: string; state: string; roadmap: string; last_sync: string
}

const STATE_LABELS: Record<string, { label: string; tone: 'accent' | 'warn' | 'neutral' }> = {
  connected: { label: 'подключено', tone: 'accent' },
  configured: { label: 'настроено', tone: 'warn' },
  mock: { label: 'мок', tone: 'neutral' },
}

export default function Integrations() {
  const query = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api.get<{ items: Integration[]; demo_notice: string }>('/admin/integrations'),
  })
  if (query.isLoading) return <div className="card p-6"><Skeleton rows={6} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />

  return (
    <SectionCard title="Внешние интеграции"
      subtitle="Заглушка за честным интерфейсом: реализация вынесена в дорожную карту с оценкой трудозатрат"
      right={<DemoNotice />}>
      <Table head={['Система', 'Назначение', 'Состояние', 'План подключения']}>
        {query.data!.items.map((item) => {
          const state = STATE_LABELS[item.state] ?? STATE_LABELS.mock
          return (
            <tr key={item.code}>
              <td className="td font-medium">{item.title}</td>
              <td className="td text-ink-muted">{item.purpose}</td>
              <td className="td"><Chip tone={state.tone}>{state.label}</Chip></td>
              <td className="td text-xs text-ink-muted">{item.roadmap || '—'}</td>
            </tr>
          )
        })}
      </Table>
    </SectionCard>
  )
}
