import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  backendApi, type EscalationTicket, type TriangulationConfirmation,
} from '../backendApi'
import { useApp } from '../store'
import { useDocuments } from '../useDocuments'
import { Chip, Empty, SectionCard, Skeleton } from '../components/ui'
import { coverageAllowsAnalysis } from '../documentCoverage'

const DOMAIN_LABELS: Record<string, string> = {
  room: 'Помещение', equipment: 'Позиция оборудования', document: 'Документ', requirement_code: 'Код требования',
}

const SOURCE_LABELS: Record<string, string> = {
  room_registry: 'реестр помещений', equip_registry: 'реестр оборудования', text: 'текст требования',
  prose: 'проза ПД', schema: 'схема', vision: 'зрение', balance: 'баланс', routing: 'маршрутизация',
  composition_registry: 'состав документации', requirement_prose: 'требование из прозы ПД',
}

function sourceLabel(s: string): string { return SOURCE_LABELS[s] ?? s }

/**
 * Сигнатурный экран, реально подключённый к движку Приложения Г
 * (`triangulated_pipeline.py`, порт 8010) — не демо-данные. Список
 * построен из очереди эскалации: кандидат на нарушение, который
 * подтвердил только ОДИН независимый источник, и вопрос, что проверить,
 * чтобы закрыть его на месте. Работает над тем же комплектом, что уже
 * загружен на экране «Новый анализ».
 */
export default function AttentionMap() {
  const {
    triangulatedRunId: runId, triangulatedRunStatus: runStatus,
    setTriangulatedRunId, checkedAttention, toggleChecked, pushToast,
  } = useApp()
  // Комплект берётся с сервера, а не из памяти браузера (Г.114): иначе
  // карта строилась бы по документам, которых на сервере уже нет.
  const { before: beforeDocs, after: afterDocs } = useDocuments()
  const stop = useMutation({
    mutationFn: () => backendApi.cancelTriangulatedRun(runId as number),
    onSuccess: (r) => pushToast(r.detail),
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось остановить', 'error'),
  })

  const readyDocs = beforeDocs.some((d) => d.status === 'ok') && afterDocs.some((d) => d.status === 'ok')
  const readyBeforeIds = beforeDocs.filter((d) => d.status === 'ok').map((d) => d.id)
  const readyAfterIds = afterDocs.filter((d) => d.status === 'ok').map((d) => d.id)
  const coverageIds = [...readyBeforeIds, ...readyAfterIds].sort((a, b) => a - b)
  const coverage = useQuery({
    queryKey: ['document-coverage', coverageIds],
    queryFn: () => backendApi.getDocumentCoverage(coverageIds),
    enabled: readyDocs,
  })
  const coverageComplete = coverageAllowsAnalysis(coverage.data)
  // В запуск передаются только готовые документы. Не скрываем остальные:
  // частичный комплект меняет границы результата, даже если карта всё ещё
  // полезна для уже разобранных томов.
  const unavailableDocs = [...beforeDocs, ...afterDocs].filter((d) => d.status !== 'ok')

  const run = useMutation({
    mutationFn: () => backendApi.createTriangulatedRun(
      readyBeforeIds,
      readyAfterIds,
    ),
    onSuccess: (data) => setTriangulatedRunId(data.id),
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось построить карту внимания', 'error'),
  })

  const result = runStatus?.status === 'done' ? runStatus.result : null
  const tickets = result?.escalation_tickets ?? []
  const confirmed = result?.triangulation?.confirmed ?? []

  const groups = new Map<string, EscalationTicket[]>()
  for (const t of tickets) {
    if (!groups.has(t.domain)) groups.set(t.domain, [])
    groups.get(t.domain)!.push(t)
  }

  return (
    <div className="space-y-4">
      <SectionCard
        title="Карта внимания"
        subtitle="Очередь эскалации реального анализа: что подтвердил только один источник и что проверить, чтобы закрыть вопрос"
        right={
          <div className="no-print flex items-center gap-2">
            {result && (
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => window.print()}>
                Печать и PDF
              </button>
            )}
          </div>
        }>
        {!readyDocs && (
          <Empty title="Комплект документов не загружен"
            hint="Карта внимания строится по документам, загруженным на экране «Новый анализ»."
            action={<Link className="btn-primary mt-2" to="/analysis/new">Загрузить документы</Link>} />
        )}
        {readyDocs && unavailableDocs.length > 0 && (
          <p className="mb-3 rounded-md border border-major/40 bg-major-soft p-2 text-xs text-major">
            В текущий запуск не войдут документы, которые ещё разбираются или завершились ошибкой: {unavailableDocs.map((d) => d.name).join(', ')}.
            {' '}Карта будет относиться только к {beforeDocs.filter((d) => d.status === 'ok').length} из {beforeDocs.length} документам ПД
            {' '}и {afterDocs.filter((d) => d.status === 'ok').length} из {afterDocs.length} документам РД/ИД.
          </p>
        )}
        {readyDocs && coverage.isLoading && (
          <p className="mb-3 rounded-md border border-surface-line bg-surface-muted p-2 text-xs text-ink-muted">
            Проверяется техническое покрытие всех переданных страниц…
          </p>
        )}
        {readyDocs && coverage.error && (
          <p className="mb-3 rounded-md border border-danger/40 bg-danger-soft p-2 text-xs text-danger">
            Не удалось проверить покрытие документов. Карта не будет запущена, пока состояние страниц неизвестно.
          </p>
        )}
        {readyDocs && coverage.data && !coverageComplete && (
          <p className="mb-3 rounded-md border border-major/40 bg-major-soft p-2 text-xs text-major">
            Первичный разбор неполный: обработано {coverage.data.pages_processed} из {coverage.data.pages_known} известных страниц,
            {' '}не обработано {coverage.data.pages_unprocessed}, исключено {coverage.data.pages_excluded}.
            {' '}Карта не запускается, чтобы неполный результат не выглядел как отсутствие расхождений.
          </p>
        )}

        {readyDocs && (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-surface-line bg-surface-muted/60 px-3 py-2 text-xs text-ink-muted no-print">
            <span>
              ПД: {beforeDocs.filter((d) => d.status === 'ok').map((d) => d.name).join(', ') || '—'}
              {' · '}РД: {afterDocs.filter((d) => d.status === 'ok').map((d) => d.name).join(', ') || '—'}
            </span>
            <button className="btn-primary px-3 py-1 text-xs"
              disabled={!coverageComplete || run.isPending || runStatus?.status === 'running'}
              onClick={() => run.mutate()}>
              {run.isPending || runStatus?.status === 'running' ? 'Считаю…' : runId === null ? 'Построить карту внимания' : 'Пересчитать'}
            </button>
            {/* Остановка длинного дела (Г.114): она не мгновенная, прогон
                прервётся на ближайшей безопасной точке. */}
            {runStatus?.status === 'running' && (
              <button className="ml-2 rounded border border-surface-line px-2 py-1 text-xs text-ink-muted hover:text-danger"
                disabled={stop.isPending}
                onClick={() => stop.mutate()}>
                {stop.isPending ? 'останавливаю…' : 'Остановить'}
              </button>
            )}
          </div>
        )}

        {readyDocs && runStatus?.status === 'running' && <Skeleton rows={5} />}

        {readyDocs && runStatus?.status === 'cancelled' && (
          <Empty title="Остановлено инспектором"
                 hint="Результат неполный. Запустите заново, когда будете готовы." />
        )}
        {readyDocs && runStatus?.status === 'error' && (
          <Empty title="Анализ завершился с ошибкой" hint={runStatus.error ?? undefined} />
        )}

        {result && !result.valid && (
          <Empty title="Прогон недействителен" hint={result.reason} />
        )}

        {result && result.valid && (
          <>
            {(result.not_run?.length ?? 0) > 0 && (
              <p className="no-print mb-3 rounded-md border border-major/40 bg-major-soft p-2 text-xs text-major">
                Без ключа ИИ в этом прогоне не проверялось: {result.not_run!.map((r) => r.split(':')[0]).join('; ')}.
                {result.llm?.used === false && ' Реестры, комплектность и текстовая сверка требований — посчитаны полностью.'}
              </p>
            )}

            {tickets.length === 0 ? (
              <Empty title="Пунктов, требующих выезда для проверки, не осталось"
                hint="Все расхождения либо подтвердились минимум двумя источниками сразу, либо не были найдены ни одним." />
            ) : (
              <ol className="space-y-3">
                {[...groups.entries()].map(([domain, items]) => (
                  <TicketGroup key={domain} domain={domain} items={items}
                    checked={checkedAttention} onToggle={toggleChecked} pushToast={pushToast} />
                ))}
              </ol>
            )}

            {confirmed.length > 0 && (
              <SectionCard title="Уже подтверждено" collapsible defaultOpen={false} className="mt-4">
                <p className="mb-2 text-xs text-ink-faint">
                  Минимум два независимых источника указали на один и тот же номер — выезд не обязателен, чтобы понять суть, но стоит взять с собой при обходе.
                </p>
                <ul className="space-y-2">
                  {confirmed.map((c: TriangulationConfirmation) => (
                    <li key={`${c.domain}:${c.key}`} className="rounded-md border border-minor/30 bg-minor-soft/30 p-3 text-sm">
                      <p className="font-medium">
                        {DOMAIN_LABELS[c.domain] ?? c.domain} {c.key}
                        <span className="ml-2 text-xs font-normal text-ink-faint">
                          {c.sources.map(sourceLabel).join(' + ')}
                        </span>
                      </p>
                      {c.details.map((d, i) => <p key={i} className="mt-1 text-xs text-ink-muted">{d}</p>)}
                    </li>
                  ))}
                </ul>
              </SectionCard>
            )}

            <SectionCard title="Подробности разбора" collapsible defaultOpen={false} className="mt-4">
              <ul className="space-y-1 text-xs text-ink-muted">
                <li>Помещения: ПД {result.rooms?.total_pd} · РД {result.rooms?.total_rd} · совпало {result.rooms?.matched} · расхождений {result.rooms?.unmatched}</li>
                <li>Оборудование: ПД {result.equipment?.total_pd} · РД {result.equipment?.total_rd} · совпало {result.equipment?.matched} · расхождений {result.equipment?.unmatched}</li>
                <li>Требования из прозы ПД (с кодом): {result.requirements?.coded.total}, подтверждено в РД {result.requirements?.coded.confirmed}, не найдено {result.requirements?.coded.missing}</li>
                <li>Требования из прозы ПД (общая форма): {result.requirements?.general.total}, с проверяемым токеном {result.requirements?.general.with_token} (подтверждено {result.requirements?.general.token_confirmed}, не найдено {result.requirements?.general.token_missing}), без токена — нужна ручная проверка: {result.requirements?.general.no_token}</li>
                <li>Комплектность: передано документов {result.composition?.supplied_count}, не хватает по ведомости {result.composition?.findings.length}</li>
                <li>Всего сигналов {result.triangulation?.signals_count}, подтверждено {confirmed.length}, в очереди эскалации {tickets.length}</li>
              </ul>
            </SectionCard>

            <p className="mt-4 text-xs text-ink-faint">
              Прогон #{runId} от {new Date(runStatus!.created_at).toLocaleString('ru-RU')}
              {result.llm?.used ? ` · ИИ: ${result.llm.provider}` : ' · без ключа ИИ (только детерминированная сверка)'}.
              Выводы носят характер гипотез и подлежат проверке на объекте.
            </p>
          </>
        )}
      </SectionCard>
    </div>
  )
}

function TicketGroup({ domain, items, checked, onToggle, pushToast }: {
  domain: string; items: EscalationTicket[]
  checked: Record<string, boolean>; onToggle: (id: string) => void
  pushToast: (text: string, kind?: 'ok' | 'error', undo?: () => void) => void
}) {
  // Свёрнуто по умолчанию для всех групп, включая «Помещение» — на реальном
  // комплекте это может быть сотни пунктов сразу (нет ключа ИИ — второй
  // источник не подтверждает ни одного расхождения, все уходят в очередь
  // эскалации), и открытая по умолчанию группа делает страницу нечитаемой
  // длинной простынёй вместо короткого списка «куда идти».
  const [open, setOpen] = useState(false)
  return (
    <li className="rounded-md border border-surface-line">
      <button className="flex w-full flex-wrap items-center gap-2 p-3 text-left text-sm" onClick={() => setOpen((v) => !v)}>
        <span className="text-ink-faint">{open ? '▾' : '▸'}</span>
        <span className="font-medium">{DOMAIN_LABELS[domain] ?? domain}</span>
        <Chip>{items.length}</Chip>
      </button>
      {open && (
        <ul className="space-y-2 border-t border-surface-line p-3 pt-2">
          {items.map((t) => {
            const id = `${t.domain}:${t.key}`
            const done = checked[id]
            return (
              <li key={id} className={`rounded-md border p-3 text-sm ${done ? 'border-minor/40 bg-minor-soft/30' : 'border-surface-line'}`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{DOMAIN_LABELS[t.domain] ?? t.domain} {t.key}</p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {t.sources_present.map((s) => <Chip key={s} tone="accent">{sourceLabel(s)}</Chip>)}
                    </div>
                    {t.context.length > 0 && (
                      <ul className="mt-2 space-y-0.5 text-xs text-ink-muted">
                        {t.context.map((c, i) => <li key={i}>· {c}</li>)}
                      </ul>
                    )}
                    <p className="mt-2 text-sm">
                      <span className="text-xs uppercase text-ink-faint">Проверить: </span>{t.question}
                    </p>
                  </div>
                  <label className="no-print flex shrink-0 items-center gap-1 text-xs">
                    <input type="checkbox" checked={Boolean(done)}
                      onChange={() => {
                        onToggle(id)
                        pushToast(done ? 'Отметка снята' : 'Отмечено как проверенное', 'ok', () => onToggle(id))
                      }} />
                    проверено
                  </label>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </li>
  )
}
