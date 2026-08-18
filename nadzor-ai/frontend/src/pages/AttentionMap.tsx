import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useApp } from '../store'
import {
  Confidence, Empty, ErrorState, SectionCard, SeverityChip, Skeleton,
} from '../components/ui'
import type { AttentionItem, ObjectBrief } from '../types'

interface AttentionResponse {
  object: { id: string; name: string; address: string; contractor: string; stage: string }
  items: AttentionItem[]
  generated_at: string
}

/**
 * Сигнатурный экран: не таблица, а короткий список «куда идти на объекте».
 * Печатается и работает офлайн — связь на стройке нестабильна.
 */
export default function AttentionMap() {
  const [params, setParams] = useSearchParams()
  const { checkedAttention, toggleChecked, pushToast } = useApp()
  const objects = useQuery({ queryKey: ['objects'], queryFn: () => api.get<{ items: ObjectBrief[] }>('/objects') })
  const objectId = params.get('object') ?? objects.data?.items[0]?.id ?? ''

  const query = useQuery({
    queryKey: ['attention', objectId],
    queryFn: () => api.get<AttentionResponse>(`/attention?object_id=${objectId}`),
    enabled: Boolean(objectId),
  })

  const online = typeof navigator !== 'undefined' ? navigator.onLine : true

  return (
    <div className="space-y-4">
      <SectionCard
        title="Карта внимания"
        subtitle="Пять–семь позиций: что смотреть на объекте в первую очередь и почему"
        right={
          <div className="no-print flex items-center gap-2">
            <select className="input w-auto py-1 text-xs" value={objectId}
              onChange={(e) => setParams({ object: e.target.value })}>
              {objects.data?.items.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
            <button className="btn-ghost px-2 py-1 text-xs" onClick={() => window.print()}>
              Печать и PDF
            </button>
          </div>
        }>
        {!online && (
          <p className="mb-3 rounded-md border border-major/40 bg-major-soft p-2 text-xs text-major">
            Нет связи. Показана сохранённая версия карты — отметки о проверке сохранятся
            в устройстве и уйдут на сервер при появлении сети.
          </p>
        )}
        {query.isLoading && <Skeleton rows={6} />}
        {query.isError && <ErrorState error={query.error} retry={() => query.refetch()} />}
        {query.data && (
          <>
            <div className="mb-4 text-sm">
              <p className="font-medium">{query.data.object.name}</p>
              <p className="text-ink-muted">{query.data.object.address}</p>
              <p className="text-xs text-ink-faint">
                Подрядчик: {query.data.object.contractor} · Этап: {query.data.object.stage}
              </p>
            </div>
            {query.data.items.length === 0 && (
              <Empty title="Расхождений, требующих выезда, не выявлено"
                hint="Запустите анализ комплекта документации, чтобы сформировать карту внимания."
                action={<Link className="btn-primary mt-2" to="/analysis/new">Новый анализ</Link>} />
            )}
            <ol className="space-y-3">
              {query.data.items.map((item, index) => {
                const done = checkedAttention[item.finding_id]
                return (
                  <li key={item.finding_id}
                    className={`rounded-md border p-4 ${done ? 'border-minor/40 bg-minor-soft/40' : 'border-surface-line'}`}>
                    <div className="flex flex-wrap items-start gap-3">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full
                        bg-accent text-sm font-semibold text-white">{index + 1}</span>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold">
                          {item.element.name} {item.element.mark && `· ${item.element.mark}`}
                        </p>
                        <p className="text-xs text-ink-faint">{item.location}</p>
                        <p className="mt-2 text-sm">{item.summary}</p>
                        <p className="mt-2 text-sm">
                          <span className="text-xs uppercase text-ink-faint">Проверить: </span>
                          {item.what_to_check}
                        </p>
                        {item.documents_to_take.length > 0 && (
                          <p className="mt-1 text-sm">
                            <span className="text-xs uppercase text-ink-faint">Взять с собой: </span>
                            {item.documents_to_take.join('; ')}
                          </p>
                        )}
                        <p className="mt-1 text-xs text-ink-faint">
                          {item.norm_refs.map((r) => `${r.doc} п. ${r.clause}`).join('; ')}
                        </p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-2">
                        <SeverityChip value={item.severity} />
                        <Confidence value={item.confidence} />
                        <span className="text-xs tabular-nums text-ink-faint">
                          приоритет {item.priority_score}
                        </span>
                        <label className="no-print flex items-center gap-1 text-xs">
                          <input type="checkbox" checked={Boolean(done)}
                            onChange={() => {
                              toggleChecked(item.finding_id)
                              pushToast(done ? 'Отметка снята' : 'Отмечено как проверенное', 'ok',
                                () => toggleChecked(item.finding_id))
                            }} />
                          проверено
                        </label>
                        <Link className="no-print text-xs text-accent hover:underline"
                          to={`/findings/${item.finding_id}`}>Открыть гипотезу</Link>
                      </div>
                    </div>
                  </li>
                )
              })}
            </ol>
            <p className="mt-4 text-xs text-ink-faint">
              Сформировано {new Date(query.data.generated_at).toLocaleString('ru-RU')}.
              Выводы носят характер гипотез и подлежат проверке на объекте.
            </p>
          </>
        )}
      </SectionCard>
    </div>
  )
}
