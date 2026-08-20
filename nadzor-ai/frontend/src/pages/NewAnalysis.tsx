import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useApp } from '../store'
import { Chip, Hint, SectionCard, Skeleton } from '../components/ui'
import { theme } from '../theme'
import type { ObjectBrief } from '../types'

interface UploadedDoc {
  id: string
  title: string
  state_kind: string
  doc_kind: string
  page_count: number
  facts_count: number
}

interface LookupResponse {
  found: boolean
  source: string
  message?: string
  card?: Record<string, string | number>
  participants?: Record<string, unknown>
}

interface TransitionsResponse {
  states: { kind: string; revision: string; label: string; documents: number }[]
  transitions: { code: string; title: string; available: boolean; reason: string }[]
}

const STAGES = ['распознавание', 'извлечение сущностей', 'сравнение', 'сверка с нормативами',
  'перепроверка', 'готово']

const STATE_HINTS = [
  ['', 'Определить по документу'],
  ['PD', 'Проектная документация'],
  ['RD', 'Рабочая документация'],
  ['AS_BUILT', 'Исполнительная документация'],
  ['EXPERTISE', 'Заключение экспертизы'],
  ['PERMIT', 'Разрешительная документация'],
] as const

export default function NewAnalysis() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { pushToast } = useApp()
  const [permit, setPermit] = useState('')
  const [objectId, setObjectId] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [stage, setStage] = useState(-1)
  const [stateHint, setStateHint] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [uploaded, setUploaded] = useState<UploadedDoc[]>([])
  const fileInput = useRef<HTMLInputElement>(null)

  const objects = useQuery({ queryKey: ['objects'], queryFn: () => api.get<{ items: ObjectBrief[] }>('/objects') })
  const lookup = useMutation({
    mutationFn: (value: string) => api.get<LookupResponse>(`/objects/lookup?permit=${encodeURIComponent(value)}`),
    onSuccess: (data) => {
      if (!data.found) { pushToast(data.message ?? 'Объект не найден', 'error'); return }
      const found = objects.data?.items.find((o) => o.permit_number === permit.trim())
      if (found) setObjectId(found.id)
      pushToast('Карточка объекта заполнена по данным ИАИС ОГД')
    },
  })

  const transitions = useQuery({
    queryKey: ['transitions', objectId],
    queryFn: () => api.get<TransitionsResponse>(`/objects/${objectId}/transitions`),
    enabled: Boolean(objectId),
  })

  useEffect(() => {
    if (transitions.data) {
      setChosen(transitions.data.transitions.filter((t) => t.available).map((t) => t.code))
    }
  }, [transitions.data])

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      if (stateHint) form.append('state_hint', stateHint)
      return api.upload<UploadedDoc>(`/objects/${objectId}/documents`, form)
    },
    onSuccess: (doc) => {
      setUploaded((prev) => [...prev, doc])
      pushToast(`«${doc.title}» распознан как ${theme.stateKinds[doc.state_kind] ?? doc.state_kind}`)
      queryClient.invalidateQueries({ queryKey: ['transitions', objectId] })
    },
    onError: (error) => {
      pushToast(error instanceof Error ? error.message : 'Файл не принят', 'error')
    },
  })

  const uploadFiles = (files: FileList | null) => {
    if (!files || !objectId) return
    Array.from(files).forEach((file) => upload.mutate(file))
  }

  const run = useMutation({
    mutationFn: () => api.post<{ run_id: string; findings: number }>('/analysis/run',
      { object_id: objectId, transitions: chosen }),
    onSuccess: (data) => {
      setStage(STAGES.length - 1)
      pushToast(`Анализ завершён: гипотез — ${data.findings}`)
      setTimeout(() => navigate(`/findings?object=${objectId}`), 700)
    },
    onError: (error) => {
      setStage(-1)
      pushToast(error instanceof Error ? error.message : 'Анализ не выполнен', 'error')
    },
  })

  const start = () => {
    if (!objectId || !chosen.length) return
    setStage(0)
    const timer = setInterval(() => setStage((s) => (s < STAGES.length - 2 ? s + 1 : s)), 550)
    run.mutate(undefined, { onSettled: () => clearInterval(timer) })
  }

  const selected = objects.data?.items.find((o) => o.id === objectId)

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <SectionCard title="1. Объект капитального строительства"
          subtitle="Введите номер разрешения — карточка заполнится из ИАИС ОГД">
          <div className="flex flex-wrap gap-2">
            <input className="input flex-1" placeholder="77-123456-012345-2024"
              value={permit} onChange={(e) => setPermit(e.target.value)} />
            <button className="btn-primary" disabled={!permit.trim() || lookup.isPending}
              onClick={() => lookup.mutate(permit.trim())}>
              {lookup.isPending ? 'Запрос…' : 'Найти'}
            </button>
          </div>
          <p className="mt-2 text-xs text-ink-faint">
            Или выберите объект из списка закреплённых за вами.
            <Hint text="Ручное создание объекта возможно, но такие сведения помечаются как не подтверждённые ИАИС ОГД." />
          </p>
          {objects.isLoading ? <div className="mt-3"><Skeleton rows={3} /></div> : (
            <select className="input mt-2" value={objectId} onChange={(e) => setObjectId(e.target.value)}>
              <option value="">— выберите объект —</option>
              {objects.data?.items.map((o) => (
                <option key={o.id} value={o.id}>{o.name}</option>
              ))}
            </select>
          )}
          {selected && (
            <dl className="mt-4 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              {[['Адрес', selected.address], ['Разрешение', selected.permit_number],
                ['Застройщик', selected.developer], ['Подрядчик', selected.contractor],
                ['Этап', selected.stage], ['Срок завершения', selected.planned_completion]].map(
                ([label, value]) => (
                  <div key={label}>
                    <dt className="text-xs text-ink-faint">{label}</dt>
                    <dd className="text-ink">{value}</dd>
                  </div>
                ))}
              <div className="sm:col-span-2">
                <Chip tone={selected.data_source === 'iais_ogd' ? 'accent' : 'warn'}>
                  {selected.data_source === 'iais_ogd'
                    ? 'Сведения подтверждены ИАИС ОГД' : 'Данные не подтверждены ИАИС ОГД'}
                </Chip>
              </div>
            </dl>
          )}
        </SectionCard>

        <SectionCard title="2. Документация комплекта"
          subtitle="Файлы разложены по состояниям жизненного цикла; спорные требуют подтверждения">
          {!objectId && <p className="text-sm text-ink-muted">Сначала выберите объект.</p>}
          {objectId && transitions.isLoading && <Skeleton rows={4} />}
          {transitions.data && (
            <>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <label className="text-xs text-ink-faint" htmlFor="state-hint">
                  Состояние (если не определится само)
                </label>
                <select id="state-hint" className="input w-auto" value={stateHint}
                  onChange={(e) => setStateHint(e.target.value)}>
                  {STATE_HINTS.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </div>
              <div
                className={`rounded-md border border-dashed p-4 text-center transition-colors
                  ${dragOver ? 'border-accent bg-accent/5' : 'border-surface-line bg-surface-muted/60'}`}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => { e.preventDefault(); setDragOver(false); uploadFiles(e.dataTransfer.files) }}
              >
                <p className="text-sm font-medium">Перетащите файлы документации сюда</p>
                <p className="mt-1 text-xs text-ink-muted">
                  Тип файла определяется по сигнатуре, редакция и дата — по основной надписи
                  ГОСТ Р 21.1101. PDF, DOCX, XLSX, изображение или DXF.
                </p>
                <button type="button" className="btn-ghost mt-3" disabled={upload.isPending}
                  onClick={() => fileInput.current?.click()}>
                  {upload.isPending ? 'Загрузка…' : 'Выбрать файлы'}
                </button>
                <input ref={fileInput} type="file" multiple hidden
                  accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.dxf"
                  onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
              </div>
              {uploaded.length > 0 && (
                <ul className="mt-3 space-y-1 text-xs text-ink-muted">
                  {uploaded.map((doc) => (
                    <li key={doc.id}>
                      «{doc.title}» — {theme.stateKinds[doc.state_kind] ?? doc.state_kind},
                      {' '}листов: {doc.page_count}, фактов: {doc.facts_count}
                    </li>
                  ))}
                </ul>
              )}
              <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                {transitions.data.states.map((s) => (
                  <li key={`${s.kind}-${s.revision}`}
                    className="rounded-md border border-surface-line px-3 py-2 text-sm">
                    <span className="font-medium">{theme.stateKinds[s.kind] ?? s.kind}</span>
                    <span className="block text-xs text-ink-faint">
                      {s.label} · документов: {s.documents}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </SectionCard>

        <SectionCard title="3. Переходы для анализа"
          subtitle="По умолчанию отмечены переходы, доступные по составу загруженного">
          {!transitions.data && <p className="text-sm text-ink-muted">Выберите объект.</p>}
          <ul className="space-y-2">
            {transitions.data?.transitions.map((t) => (
              <li key={t.code}>
                <label className={`flex items-start gap-3 rounded-md border p-3 text-sm
                  ${t.available ? 'border-surface-line hover:bg-surface-muted' : 'border-surface-line opacity-60'}`}>
                  <input type="checkbox" className="mt-0.5" disabled={!t.available}
                    checked={chosen.includes(t.code)}
                    onChange={(e) => setChosen((prev) => e.target.checked
                      ? [...prev, t.code] : prev.filter((c) => c !== t.code))} />
                  <span>
                    <span className="font-medium">{t.code}</span> · {t.title}
                    {!t.available && <span className="block text-xs text-ink-faint">{t.reason}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </SectionCard>
      </div>

      <div className="space-y-4">
        <SectionCard title="Запуск анализа">
          <button className="btn-primary w-full justify-center"
            disabled={!objectId || !chosen.length || run.isPending} onClick={start}>
            {run.isPending ? 'Выполняется…' : 'Запустить анализ'}
          </button>
          <ol className="mt-4 space-y-2">
            {STAGES.map((label, index) => (
              <li key={label} className="flex items-center gap-2 text-sm">
                <span className={`flex h-5 w-5 items-center justify-center rounded-full border text-[11px]
                  ${stage > index ? 'border-accent bg-accent text-white'
                    : stage === index ? 'border-accent text-accent' : 'border-surface-line text-ink-faint'}`}>
                  {stage > index ? '✓' : index + 1}
                </span>
                <span className={stage >= index ? 'text-ink' : 'text-ink-faint'}>{label}</span>
              </li>
            ))}
          </ol>
          <p className="mt-3 text-xs text-ink-faint">
            Промежуточные находки показываются по мере готовности. Анализ не совершает
            юридически значимых действий.
          </p>
        </SectionCard>
      </div>
    </div>
  )
}
