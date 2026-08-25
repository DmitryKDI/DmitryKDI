import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import {
  Chip, Empty, ErrorState, SectionCard, Skeleton, Table, formatDate,
} from '../components/ui'
import { theme } from '../theme'
import { useApp } from '../store'
import type { DocumentBrief, ObjectBrief } from '../types'

const EMPTY_FORM = {
  permit_number: '', name: '', address: '', district: '', developer: '', contractor: '',
  designer: '', stage: '', planned_completion: '', cadastral_number: '', permit_date: '',
}

interface OgdCard {
  permit_number: string; permit_date: string; name: string; address: string; district: string
  cadastral_number: string; developer: string; contractor: string; designer: string
  stage: string; planned_completion: string
}

function AddObjectForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient()
  const { pushToast } = useApp()
  const [form, setForm] = useState(EMPTY_FORM)
  const [lookupState, setLookupState] = useState<'idle' | 'checking' | 'found' | 'not-found'>('idle')
  const set = (key: keyof typeof EMPTY_FORM) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  const lookup = useMutation({
    mutationFn: () => api.get<{ found: boolean; card?: OgdCard }>(
      `/objects/lookup?permit=${encodeURIComponent(form.permit_number)}`),
    onSuccess: (data) => {
      if (data.found && data.card) {
        setLookupState('found')
        const { permit_number: _p, ...rest } = data.card
        setForm((f) => ({ ...f, ...rest }))
        pushToast('Карточка подтянута из ИАИС ОГД')
      } else {
        setLookupState('not-found')
      }
    },
    onError: () => setLookupState('not-found'),
  })

  const create = useMutation({
    mutationFn: () => api.post<ObjectBrief>('/objects', form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['objects'] })
      pushToast('Объект добавлен')
      onDone()
    },
    onError: (e) => pushToast(e instanceof ApiError ? e.message : 'Не удалось добавить объект', 'error'),
  })

  return (
    <div className="mb-4 space-y-3 rounded-md border border-surface-line bg-surface-muted/40 p-4">
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <label htmlFor="obj-permit" className="block text-xs text-ink-faint">Номер разрешения на строительство *</label>
          <input id="obj-permit" className="input" value={form.permit_number}
            onChange={(e) => { setForm((f) => ({ ...f, permit_number: e.target.value })); setLookupState('idle') }} />
        </div>
        <button type="button" className="btn-ghost shrink-0" disabled={!form.permit_number || lookup.isPending}
          onClick={() => { setLookupState('checking'); lookup.mutate() }}>
          {lookup.isPending ? 'Ищу…' : 'Найти по ИАИС ОГД'}
        </button>
      </div>
      {lookupState === 'found' && (
        <p className="text-xs text-accent">Найдено в ИАИС ОГД — поля подтянуты, можно поправить.</p>
      )}
      {lookupState === 'not-found' && (
        <p className="text-xs text-major">
          В ИАИС ОГД (мок) сведений нет. Заполните поля вручную — объект будет помечен «ручной ввод».
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="obj-name" className="block text-xs text-ink-faint">Наименование объекта *</label>
          <input id="obj-name" className="input" value={form.name} onChange={set('name')} />
        </div>
        <div>
          <label htmlFor="obj-address" className="block text-xs text-ink-faint">Адрес</label>
          <input id="obj-address" className="input" value={form.address} onChange={set('address')} />
        </div>
        <div>
          <label htmlFor="obj-district" className="block text-xs text-ink-faint">Округ / район</label>
          <input id="obj-district" className="input" value={form.district} onChange={set('district')} />
        </div>
        <div>
          <label htmlFor="obj-cadastral" className="block text-xs text-ink-faint">Кадастровый номер</label>
          <input id="obj-cadastral" className="input" value={form.cadastral_number} onChange={set('cadastral_number')} />
        </div>
        <div>
          <label htmlFor="obj-developer" className="block text-xs text-ink-faint">Застройщик</label>
          <input id="obj-developer" className="input" value={form.developer} onChange={set('developer')} />
        </div>
        <div>
          <label htmlFor="obj-contractor" className="block text-xs text-ink-faint">Подрядчик</label>
          <input id="obj-contractor" className="input" value={form.contractor} onChange={set('contractor')} />
        </div>
        <div>
          <label htmlFor="obj-designer" className="block text-xs text-ink-faint">Генпроектировщик</label>
          <input id="obj-designer" className="input" value={form.designer} onChange={set('designer')} />
        </div>
        <div>
          <label htmlFor="obj-stage" className="block text-xs text-ink-faint">Стадия</label>
          <input id="obj-stage" className="input" value={form.stage} onChange={set('stage')} />
        </div>
        <div>
          <label htmlFor="obj-permit-date" className="block text-xs text-ink-faint">Дата разрешения</label>
          <input id="obj-permit-date" className="input" placeholder="дд.мм.гггг" value={form.permit_date} onChange={set('permit_date')} />
        </div>
        <div>
          <label htmlFor="obj-completion" className="block text-xs text-ink-faint">Плановое завершение</label>
          <input id="obj-completion" className="input" placeholder="дд.мм.гггг" value={form.planned_completion}
            onChange={set('planned_completion')} />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button className="btn-primary" disabled={!form.permit_number || !form.name || create.isPending}
          onClick={() => create.mutate()}>
          {create.isPending ? 'Добавляю…' : 'Добавить объект'}
        </button>
        <button className="btn-ghost" onClick={onDone}>Отмена</button>
      </div>
    </div>
  )
}

export function ObjectsList() {
  const { can } = useApp()
  const [adding, setAdding] = useState(false)
  const query = useQuery({ queryKey: ['objects'], queryFn: () => api.get<{ items: ObjectBrief[] }>('/objects') })
  if (query.isLoading) return <div className="card p-6"><Skeleton rows={5} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />

  return (
    <SectionCard title="Объекты надзора"
      subtitle="Показаны объекты, доступные вашей роли: проверка прав выполняется при выборке данных"
      right={can('objects:create') && !adding ? (
        <button className="btn-primary px-2 py-1 text-xs" onClick={() => setAdding(true)}>Добавить объект</button>
      ) : undefined}>
      {adding && <AddObjectForm onDone={() => setAdding(false)} />}
      {query.data!.items.length === 0 ? (
        <Empty title="Объектов, доступных вашей роли, нет"
          hint={can('objects:create')
            ? 'Добавьте объект по номеру разрешения на строительство — карточка заполнится из ИАИС ОГД.'
            : 'Обратитесь к начальнику отдела для закрепления объектов.'} />
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
