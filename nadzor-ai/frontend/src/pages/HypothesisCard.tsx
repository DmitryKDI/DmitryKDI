import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { useApp } from '../store'
import {
  AuthImage, Chip, Confidence, ErrorState, Modal, SectionCard, SeverityChip, Skeleton,
  elementLocation,
} from '../components/ui'
import { theme } from '../theme'
import type { Evidence, Finding } from '../types'

export default function HypothesisCard() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { can, pushToast } = useApp()
  const [preview, setPreview] = useState<Evidence | null>(null)
  const [comment, setComment] = useState('')

  const query = useQuery({ queryKey: ['finding', id], queryFn: () => api.get<Finding>(`/findings/${id}`) })
  const assess = useMutation({
    mutationFn: (verdict: string) => api.post(`/findings/${id}/assess`, { verdict, comment }),
    onSuccess: (_data, verdict) => {
      pushToast(`Оценка сохранена: ${verdict === 'confirmed' ? 'подтверждено'
        : verdict === 'rejected' ? 'не подтверждено' : 'требует уточнения'}`)
      queryClient.invalidateQueries({ queryKey: ['finding', id] })
      queryClient.invalidateQueries({ queryKey: ['findings'] })
    },
    onError: () => pushToast('Не удалось сохранить оценку', 'error'),
  })

  if (query.isLoading) return <div className="card p-6"><Skeleton rows={8} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const f = query.data!

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <SectionCard title={`${f.public_id} · ${f.code}`} subtitle={f.title}
          right={<SeverityChip value={f.severity} />}>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            <Item label="Переход" value={`${f.transition} · ${theme.transitions[f.transition] ?? ''}`} />
            <Item label="Конструктивный элемент"
              value={elementLocation(f.structural_element) || 'не определён'} />
            <Item label="Объект" value={f.object_name ?? f.object_id} />
            <Item label="Уверенность" value={<Confidence value={f.confidence} />} />
            <Item label="Приоритет" value={<span className="font-semibold tabular-nums">{f.priority_score}</span>} />
            <Item label="Источник вывода"
              value={f.detector === 'llm' ? 'семантический слой языковой модели' : 'детерминированное правило'} />
          </dl>
          {f.injection_suspected && (
            <p className="mt-4 rounded-md border border-major/40 bg-major-soft p-3 text-sm text-major">
              В документе обнаружена попытка воздействия на систему анализа. На результат она
              не повлияла: вывод получен детерминированным правилом. Требуется проверка
              добросовестности лица, представившего документацию.
            </p>
          )}
        </SectionCard>

        <SectionCard title="Доказательная база"
          subtitle="Каждое доказательство ведёт к области на листе документа">
          <ul className="space-y-2">
            {f.evidence.map((e, i) => (
              <li key={i} className="rounded-md border border-surface-line p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone={e.role === 'требование' ? 'accent' : 'neutral'}>{e.role || 'факт'}</Chip>
                  <span className="text-sm font-medium">{e.doc_title}</span>
                  <span className="text-xs text-ink-faint">лист {e.page}</span>
                  <button className="btn-ghost ml-auto px-2 py-1 text-xs" onClick={() => setPreview(e)}>
                    Показать на листе
                  </button>
                </div>
                <p className="mt-1 text-sm text-ink-muted">{e.extracted}</p>
              </li>
            ))}
          </ul>
        </SectionCard>

        <SectionCard title="Нормативное и правовое основание" collapsible>
          <ul className="space-y-3">
            {(f.norm_texts ?? f.norm_refs).map((ref, i) => (
              <li key={i} className="rounded-md border border-surface-line p-3">
                <p className="text-sm font-medium">
                  {ref.doc} п. {ref.clause}
                  <span className="ml-2 text-xs font-normal text-ink-faint">редакция {ref.edition}</span>
                  <Chip tone={ref.actual ? 'accent' : 'warn'}>
                    {ref.actual ? 'действующая' : 'проверить актуальность'}
                  </Chip>
                </p>
                {ref.title && <p className="text-xs text-ink-faint">{ref.title}</p>}
                {ref.text && <p className="mt-1 text-sm text-ink-muted">{ref.text}</p>}
                {ref.edition_check && !ref.edition_check.actual && (
                  <p className="mt-1 text-xs text-major">{ref.edition_check.message}</p>
                )}
              </li>
            ))}
          </ul>
          {f.legal_refs.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {f.legal_refs.map((l, i) => (
                <li key={i} className="text-ink-muted">
                  <span className="font-medium text-ink">{l.act} ст. {l.article} ч. {l.part}</span>
                  {l.title && ` — ${l.title}`}
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      </div>

      <div className="space-y-4">
        <SectionCard title="Рекомендуемое действие">
          <p className="text-sm"><span className="text-xs text-ink-faint">Проверить на объекте</span><br />
            {f.field_check || '—'}</p>
          {f.documents_to_request.length > 0 && (
            <div className="mt-3">
              <p className="text-xs text-ink-faint">Истребовать документы</p>
              <ul className="mt-1 list-inside list-disc text-sm text-ink-muted">
                {f.documents_to_request.map((d) => <li key={d}>{d}</li>)}
              </ul>
            </div>
          )}
        </SectionCard>

        <SectionCard title="Оценка инспектора">
          {f.assessment ? (
            <p className="text-sm">
              <Chip tone="accent">
                {f.assessment.verdict === 'confirmed' ? 'Подтверждено'
                  : f.assessment.verdict === 'rejected' ? 'Не подтверждено' : 'Требует уточнения'}
              </Chip>
              {f.assessment.comment && <span className="mt-2 block text-ink-muted">{f.assessment.comment}</span>}
            </p>
          ) : can('findings:assess') ? (
            <>
              <textarea className="input h-20" placeholder="Комментарий (необязательно)"
                value={comment} onChange={(e) => setComment(e.target.value)} />
              <div className="mt-2 grid gap-2">
                <button className="btn-primary justify-center" disabled={assess.isPending}
                  onClick={() => assess.mutate('confirmed')}>Подтвердить</button>
                <button className="btn-ghost justify-center" disabled={assess.isPending}
                  onClick={() => assess.mutate('rejected')}>Не подтверждено</button>
                <button className="btn-ghost justify-center" disabled={assess.isPending}
                  onClick={() => assess.mutate('clarify')}>Требует уточнения</button>
              </div>
            </>
          ) : (
            <p className="text-sm text-ink-muted">Оценка доступна инспектору и начальнику отдела.</p>
          )}
          <Link className="mt-3 block text-xs text-accent hover:underline"
            to={`/attention?object=${f.object_id}`}>Открыть карту внимания объекта →</Link>
        </SectionCard>

        <SectionCard title="Происхождение вывода" collapsible defaultOpen={false}>
          <dl className="space-y-1 text-xs text-ink-muted">
            {Object.entries(f.provenance).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-2">
                <dt className="text-ink-faint">{key}</dt><dd className="text-right">{String(value)}</dd>
              </div>
            ))}
            <div className="flex justify-between gap-2">
              <dt className="text-ink-faint">перепроверка</dt>
              <dd className="text-right">{String(f.verification?.status ?? '—')}</dd>
            </div>
          </dl>
        </SectionCard>
      </div>

      <Modal open={Boolean(preview)} title={preview?.doc_title ?? ''} onClose={() => setPreview(null)}>
        {preview && <SheetPreview evidence={preview} />}
      </Modal>
    </div>
  )
}

function Item({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-ink-faint">{label}</dt>
      <dd className="text-ink">{value}</dd>
    </div>
  )
}

/** Лист документа с подсветкой области, к которой относится факт. */
function SheetPreview({ evidence }: { evidence: Evidence }) {
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [natural, setNatural] = useState({ width: 1, height: 1 })
  // Координаты факта заданы в единицах листа PDF; изображение отрисовано при 110 точках на дюйм.
  const scale = size.width / (natural.width || 1)
  const pdfScale = 110 / 72
  const [x0, y0, x1, y1] = evidence.bbox
  return (
    <div className="relative">
      <AuthImage src={api.pageUrl(evidence.doc_id, evidence.page)} alt={`Лист ${evidence.page}`}
        className="w-full rounded border border-surface-line"
        onSize={({ width, height, clientWidth }) => {
          setNatural({ width, height })
          setSize({ width: clientWidth, height: 0 })
        }} />
      {size.width > 0 && (
        <span className="pointer-events-none absolute rounded border-2 border-accent bg-accent/10"
          style={{
            left: x0 * pdfScale * scale, top: y0 * pdfScale * scale,
            width: (x1 - x0) * pdfScale * scale, height: (y1 - y0) * pdfScale * scale,
          }} />
      )}
      <p className="mt-2 text-xs text-ink-faint">
        Лист {evidence.page}. Извлечено: {evidence.extracted}
      </p>
    </div>
  )
}
