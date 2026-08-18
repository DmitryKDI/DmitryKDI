import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useApp } from '../store'
import {
  Chip, Confidence, DemoNotice, Empty, ErrorState, SectionCard, SeverityChip, Skeleton, Table,
  elementLocation,
} from '../components/ui'
import { theme } from '../theme'
import type { Finding, ObjectBrief } from '../types'

const SEVERITIES = [['', 'Все'], ['critical', 'Критические'], ['major', 'Существенные'],
  ['minor', 'Незначительные']]

/** Документ и лист, на котором находится расхождение: инспектор проверяет вывод сам. */
function docCell(f: Finding, which: 0 | 1) {
  const reqIndex = Math.max(f.evidence.findIndex((e) => e.role === 'требование'), 0)
  const index = which === 0 ? reqIndex : f.evidence.findIndex((_, i) => i !== reqIndex)
  const ev = f.evidence[index >= 0 ? index : reqIndex]
  if (!ev) return <span className="text-ink-faint">—</span>
  return (
    <span className="block max-w-[15rem]">
      <span className="block text-xs">{ev.doc_title}</span>
      <Link to={`/findings/${f.id}`}
        className="mt-0.5 inline-block rounded border border-accent-line bg-accent-soft px-1.5
                   text-[11px] tabular-nums text-accent">лист {ev.page}</Link>
      <span className="mt-0.5 block truncate text-xs text-ink-muted">{ev.extracted}</span>
    </span>
  )
}

export default function HypothesisLog() {
  const [params, setParams] = useSearchParams()
  const { filters, setFilter, density, setDensity } = useApp()
  const saved = filters.findings ?? {}
  const objectId = params.get('object') ?? saved.object ?? ''
  const severity = params.get('severity') ?? saved.severity ?? ''
  const transition = params.get('transition') ?? saved.transition ?? ''

  const objects = useQuery({ queryKey: ['objects'], queryFn: () => api.get<{ items: ObjectBrief[] }>('/objects') })
  const query = useQuery({
    queryKey: ['findings', objectId, severity, transition],
    queryFn: () => {
      const search = new URLSearchParams()
      if (objectId) search.set('object_id', objectId)
      if (severity) search.set('severity', severity)
      if (transition) search.set('transition', transition)
      return api.get<{ items: Finding[]; total: number }>(`/findings?${search}`)
    },
  })

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key === 'object' ? 'object' : key, value)
    else next.delete(key === 'object' ? 'object' : key)
    setParams(next)
    setFilter('findings', key, value)
  }

  const exportCsv = () => {
    const rows = query.data?.items ?? []
    const header = ['Идентификатор', 'Код', 'Переход', 'Критичность', 'Уверенность', 'Балл', 'Описание']
    const csv = [header, ...rows.map((f) => [f.public_id, f.code, f.transition, f.severity,
      f.confidence, f.priority_score, f.title.replace(/;/g, ',')])]
      .map((r) => r.join(';')).join('\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = 'gipotezy.csv'
    link.click()
  }

  return (
    <SectionCard
      title="Журнал гипотез нарушений"
      subtitle="Система формирует гипотезы, а не заключения: каждая требует оценки инспектора"
      right={
        <div className="flex items-center gap-2">
          <button className="btn-ghost px-2 py-1 text-xs"
            onClick={() => setDensity(density === 'compact' ? 'comfortable' : 'compact')}>
            {density === 'compact' ? 'Обычная плотность' : 'Компактно'}
          </button>
          <button className="btn-ghost px-2 py-1 text-xs" onClick={exportCsv}>Выгрузить выборку</button>
        </div>
      }>
      <div className="mb-3 flex flex-wrap gap-2">
        <select className="input w-auto" value={objectId} onChange={(e) => update('object', e.target.value)}>
          <option value="">Все объекты</option>
          {objects.data?.items.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
        <select className="input w-auto" value={severity} onChange={(e) => update('severity', e.target.value)}>
          {SEVERITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select className="input w-auto" value={transition} onChange={(e) => update('transition', e.target.value)}>
          <option value="">Все переходы</option>
          {Object.entries(theme.transitions).map(([code, label]) => (
            <option key={code} value={code}>{code} · {label}</option>
          ))}
        </select>
      </div>

      {query.isLoading && <Skeleton rows={6} />}
      {query.isError && <ErrorState error={query.error} retry={() => query.refetch()} />}
      {query.data && query.data.total === 0 && (
        <Empty title="Гипотез по заданным условиям нет"
          hint="Снимите фильтры или запустите анализ комплекта документации по объекту."
          action={<Link className="btn-primary mt-2" to="/analysis/new">Новый анализ</Link>} />
      )}

      {query.data && query.data.total > 0 && (
        <Table head={['Гипотеза', 'Конструктив', 'Требование: документ и лист',
          'Факт: документ и лист', 'Критичность', 'Уверенность', 'Балл', 'Оценка']}>
          {query.data.items.map((f) => (
            <tr key={f.id} className="hover:bg-surface-muted/60">
              <td className="td">
                <Link to={`/findings/${f.id}`} className="font-medium text-accent hover:underline">
                  {f.public_id} · {f.code}
                </Link>
                <span className="block max-w-xl text-ink-muted">{f.title}</span>
                {f.injection_suspected && (
                  <span className="mt-1 inline-block"><DemoNotice text="Обнаружена попытка воздействия на систему" /></span>
                )}
              </td>
              <td className="td text-ink-muted">{elementLocation(f.structural_element) || '—'}</td>
              <td className="td">{docCell(f, 0)}</td>
              <td className="td">{docCell(f, 1)}</td>
              <td className="td"><SeverityChip value={f.severity} /></td>
              <td className="td"><Confidence value={f.confidence} /></td>
              <td className="td tabular-nums font-semibold">{f.priority_score}</td>
              <td className="td">
                {f.assessment
                  ? <Chip tone={f.assessment.verdict === 'confirmed' ? 'accent' : 'neutral'}>
                      {f.assessment.verdict === 'confirmed' ? 'Подтверждено'
                        : f.assessment.verdict === 'rejected' ? 'Не подтверждено' : 'Уточнить'}
                    </Chip>
                  : <span className="text-xs text-ink-faint">не оценено</span>}
              </td>
            </tr>
          ))}
        </Table>
      )}
    </SectionCard>
  )
}
