import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import {
  AuthImage, Chip, Empty, ErrorState, SectionCard, SeverityChip, Skeleton, Table,
  elementLocation,
} from '../components/ui'
import { theme } from '../theme'
import type { DocumentBrief, Evidence, Finding, ObjectBrief } from '../types'

interface DiffResponse {
  engine: string
  regions: { page: number; kind: string; bbox_after: [number, number, number, number] | null
    weight: number }[]
}

/** Двухпанельный просмотр «было / стало» с подсветкой изменённой области листа. */
export default function DiscrepancyLog() {
  const [params, setParams] = useSearchParams()
  const [pair, setPair] = useState<{ before: Evidence; after: Evidence } | null>(null)
  const objects = useQuery({ queryKey: ['objects'], queryFn: () => api.get<{ items: ObjectBrief[] }>('/objects') })
  const objectId = params.get('object') ?? objects.data?.items[0]?.id ?? ''

  const findings = useQuery({
    queryKey: ['findings', objectId],
    queryFn: () => api.get<{ items: Finding[] }>(`/findings?object_id=${objectId}`),
    enabled: Boolean(objectId),
  })
  const docs = useQuery({
    queryKey: ['object', objectId],
    queryFn: () => api.get<{ documents: DocumentBrief[] }>(`/objects/${objectId}`),
    enabled: Boolean(objectId),
  })

  // Расхождение — находка, у которой есть и требование, и факт: их и сравниваем.
  const rows = useMemo(
    () => (findings.data?.items ?? []).filter((f) => f.evidence.length >= 2),
    [findings.data],
  )

  return (
    <div className="space-y-4">
      <SectionCard title="Журнал расхождений"
        subtitle="Переходы между состояниями объекта: что было в одном состоянии и что стало в другом"
        right={
          <select className="input w-auto py-1 text-xs" value={objectId}
            onChange={(e) => setParams({ object: e.target.value })}>
            {objects.data?.items.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        }>
        {findings.isLoading && <Skeleton rows={5} />}
        {findings.isError && <ErrorState error={findings.error} retry={() => findings.refetch()} />}
        {findings.data && rows.length === 0 && (
          <Empty title="Расхождений между состояниями не найдено"
            hint="Расхождение показывается, когда система нашла и проектное требование, и фактическое значение." />
        )}
        {rows.length > 0 && (
          <Table head={['Переход', 'Раздел и конструктив', 'Было', 'Стало', 'Критичность', '']}>
            {rows.map((f) => {
              const before = f.evidence.find((e) => e.role === 'требование') ?? f.evidence[0]
              const after = f.evidence.find((e) => e.role === 'факт') ?? f.evidence[1]
              return (
                <tr key={f.id} className="hover:bg-surface-muted/60">
                  <td className="td whitespace-nowrap" title={theme.transitions[f.transition]}>{f.transition}</td>
                  <td className="td">
                    <span className="font-medium">{f.structural_element.name || f.code}</span>
                    <span className="block text-xs text-ink-faint">{elementLocation(f.structural_element)}</span>
                  </td>
                  <td className="td text-ink-muted">{before?.extracted}</td>
                  <td className="td font-medium">{after?.extracted}</td>
                  <td className="td"><SeverityChip value={f.severity} /></td>
                  <td className="td">
                    <button className="btn-ghost px-2 py-1 text-xs"
                      onClick={() => setPair({ before, after })}>Сравнить листы</button>
                  </td>
                </tr>
              )
            })}
          </Table>
        )}
      </SectionCard>

      {pair && <SideBySide pair={pair} onClose={() => setPair(null)} />}
      {docs.data && (
        <SectionCard title="Состав комплекта" collapsible defaultOpen={false}>
          <Table head={['Документ', 'Состояние', 'Редакция', 'Дата', 'Листов', 'Фактов', 'Подпись']}>
            {docs.data.documents.map((d) => (
              <tr key={d.id}>
                <td className="td">{d.title}</td>
                <td className="td text-ink-muted">{theme.stateKinds[d.state_kind] ?? d.state_kind}</td>
                <td className="td">{d.revision}</td>
                <td className="td">{d.doc_date ?? '—'}</td>
                <td className="td tabular-nums">{d.page_count}</td>
                <td className="td tabular-nums">{d.facts_count}</td>
                <td className="td">
                  <Chip tone={d.signature_status === 'valid' ? 'accent' : 'warn'}>
                    {d.signature_status === 'valid' ? 'ЭП действительна' : 'не проверена'}
                  </Chip>
                </td>
              </tr>
            ))}
          </Table>
        </SectionCard>
      )}
    </div>
  )
}

function SideBySide({ pair, onClose }: { pair: { before: Evidence; after: Evidence }; onClose: () => void }) {
  const diff = useQuery({
    queryKey: ['diff', pair.before.doc_id, pair.after.doc_id],
    queryFn: () => api.get<DiffResponse>(
      `/diff?document_before=${pair.before.doc_id}&document_after=${pair.after.doc_id}`),
  })
  return (
    <SectionCard title="Сравнение листов «было / стало»"
      subtitle={diff.data ? `Движок: ${diff.data.engine}, изменённых областей: ${diff.data.regions.length}` : ''}
      right={<button className="btn-ghost px-2 py-1 text-xs" onClick={onClose}>Закрыть</button>}>
      <div className="grid gap-4 md:grid-cols-2">
        {[['Было', pair.before], ['Стало', pair.after]].map(([label, evidence]) => (
          <figure key={label as string}>
            <figcaption className="mb-1 text-xs font-medium text-ink-muted">
              {label as string}: {(evidence as Evidence).doc_title}, лист {(evidence as Evidence).page}
            </figcaption>
            <AuthImage src={api.pageUrl((evidence as Evidence).doc_id, (evidence as Evidence).page)}
              alt={label as string} className="w-full rounded border border-surface-line" />
          </figure>
        ))}
      </div>
      {diff.isLoading && <div className="mt-3"><Skeleton rows={2} /></div>}
    </SectionCard>
  )
}
