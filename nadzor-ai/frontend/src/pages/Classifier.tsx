import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { ErrorState, SectionCard, SeverityChip, Skeleton, Table } from '../components/ui'

interface ClassItem {
  code: string; group: string; transitions: string[]; title: string; default_severity: string
  norm_refs: { doc: string; clause: string }[]
  legal_refs: { act: string; article: string; part: string }[]
}

export default function Classifier() {
  const query = useQuery({
    queryKey: ['classifier'],
    queryFn: () => api.get<{ classes: ClassItem[]; region: Record<string, string> }>('/classifier'),
  })
  if (query.isLoading) return <div className="card p-6"><Skeleton rows={6} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />

  return (
    <SectionCard title="Классификатор расхождений"
      subtitle={`Региональный справочник: ${query.data!.region.name ?? ''}. Подключение нового субъекта — загрузка нормативного пакета, а не доработка кода`}>
      <Table head={['Код', 'Группа', 'Наименование', 'Переходы', 'Критичность', 'Нормативное основание', 'КоАП']}>
        {query.data!.classes.map((c) => (
          <tr key={c.code}>
            <td className="td font-mono font-medium">{c.code}</td>
            <td className="td text-ink-muted">{c.group}</td>
            <td className="td">{c.title}</td>
            <td className="td text-xs text-ink-muted">{c.transitions.join(', ')}</td>
            <td className="td"><SeverityChip value={c.default_severity} /></td>
            <td className="td text-xs text-ink-muted">
              {c.norm_refs.map((r) => `${r.doc} п. ${r.clause}`).join('; ') || '—'}
            </td>
            <td className="td text-xs text-ink-muted">
              {c.legal_refs.map((r) => `ст. ${r.article} ч. ${r.part}`).join('; ') || '—'}
            </td>
          </tr>
        ))}
      </Table>
    </SectionCard>
  )
}
