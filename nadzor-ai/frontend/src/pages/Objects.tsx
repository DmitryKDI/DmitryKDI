import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import {
  Chip, Empty, ErrorState, SectionCard, Skeleton, Table, formatDate,
} from '../components/ui'
import { theme } from '../theme'
import type { DocumentBrief, ObjectBrief } from '../types'

export function ObjectsList() {
  const query = useQuery({ queryKey: ['objects'], queryFn: () => api.get<{ items: ObjectBrief[] }>('/objects') })
  if (query.isLoading) return <div className="card p-6"><Skeleton rows={5} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />

  return (
    <SectionCard title="Объекты надзора"
      subtitle="Показаны объекты, доступные вашей роли: проверка прав выполняется при выборке данных">
      {query.data!.items.length === 0 ? (
        <Empty title="Объектов, доступных вашей роли, нет"
          hint="Обратитесь к начальнику отдела для закрепления объектов." />
      ) : (
        <Table head={['Объект', 'Округ', 'Разрешение', 'Застройщик', 'Подрядчик', 'Этап', 'Источник']}>
          {query.data!.items.map((o) => (
            <tr key={o.id} className="hover:bg-surface-muted/60">
              <td className="td">
                <Link className="font-medium text-accent hover:underline" to={`/objects/${o.id}`}>{o.name}</Link>
                <span className="block text-xs text-ink-faint">{o.address}</span>
              </td>
              <td className="td text-ink-muted">{o.district}</td>
              <td className="td whitespace-nowrap text-ink-muted">{o.permit_number}</td>
              <td className="td text-ink-muted">{o.developer}</td>
              <td className="td text-ink-muted">{o.contractor}</td>
              <td className="td text-ink-muted">{o.stage}</td>
              <td className="td">
                <Chip tone={o.data_source === 'iais_ogd' ? 'accent' : 'warn'}>
                  {o.data_source === 'iais_ogd' ? 'ИАИС ОГД' : 'ручной ввод'}
                </Chip>
              </td>
            </tr>
          ))}
        </Table>
      )}
    </SectionCard>
  )
}

interface ObjectDetails {
  object: ObjectBrief & { permit_date: string; cadastral_number: string; designer: string
    card: Record<string, unknown> }
  documents: DocumentBrief[]
  inspections: { date: string; kind: string; result: string; prescription: string
    deadline: string; status: string }[]
}

export function ObjectCard() {
  const { id = '' } = useParams()
  const query = useQuery({
    queryKey: ['object', id],
    queryFn: () => api.get<ObjectDetails>(`/objects/${id}`),
  })
  if (query.isLoading) return <div className="card p-6"><Skeleton rows={8} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const { object, documents, inspections } = query.data!

  return (
    <div className="space-y-4">
      <SectionCard title={object.name} subtitle={object.address}
        right={
          <div className="flex gap-2">
            <Link className="btn-ghost px-2 py-1 text-xs" to={`/attention?object=${object.id}`}>Карта внимания</Link>
            <Link className="btn-primary px-2 py-1 text-xs" to={`/findings?object=${object.id}`}>Гипотезы</Link>
          </div>
        }>
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
          {[['Разрешение на строительство', `${object.permit_number} от ${object.permit_date}`],
            ['Кадастровый номер', object.cadastral_number], ['Округ и район', object.district],
            ['Застройщик', object.developer], ['Проектировщик', object.designer],
            ['Лицо, осуществляющее строительство', object.contractor],
            ['Этап строительства', object.stage], ['Плановое завершение', object.planned_completion],
            ['Подразделение надзора', object.department]].map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-ink-faint">{label}</dt>
                <dd className="text-ink">{String(value) || '—'}</dd>
              </div>
            ))}
        </dl>
      </SectionCard>

      <SectionCard title="История проверок" subtitle="Сведения из ИАИС ОГД" collapsible>
        <Table head={['Дата', 'Вид проверки', 'Результат', 'Предписание', 'Срок устранения', 'Статус']}>
          {inspections.map((item, i) => (
            <tr key={i}>
              <td className="td whitespace-nowrap">{item.date}</td>
              <td className="td text-ink-muted">{item.kind}</td>
              <td className="td">{item.result}</td>
              <td className="td text-ink-muted">{item.prescription || '—'}</td>
              <td className="td text-ink-muted">{item.deadline || '—'}</td>
              <td className="td">
                <Chip tone={item.status === 'на контроле' ? 'warn' : 'neutral'}>{item.status || '—'}</Chip>
              </td>
            </tr>
          ))}
        </Table>
      </SectionCard>

      <SectionCard title={`Документы комплекта (${documents.length})`} collapsible defaultOpen={false}>
        <Table head={['Документ', 'Состояние', 'Ред.', 'Дата', 'Листов', 'Извлечено фактов', 'Хэш']}>
          {documents.map((d) => (
            <tr key={d.id}>
              <td className="td">{d.title}</td>
              <td className="td text-ink-muted">{theme.stateKinds[d.state_kind] ?? d.state_kind}</td>
              <td className="td">{d.revision}</td>
              <td className="td whitespace-nowrap">{formatDate(d.doc_date)}</td>
              <td className="td tabular-nums">{d.page_count}</td>
              <td className="td tabular-nums">{d.facts_count}</td>
              <td className="td font-mono text-xs text-ink-faint">{d.sha256.slice(0, 12)}</td>
            </tr>
          ))}
        </Table>
      </SectionCard>
    </div>
  )
}
