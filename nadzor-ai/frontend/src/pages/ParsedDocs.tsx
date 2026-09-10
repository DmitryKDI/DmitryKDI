import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  backendApi, type BackendCoverageReport, type BackendDocument,
  type BackendDocumentCoverage,
} from '../backendApi'
import { useDocuments } from '../useDocuments'
import { Chip, Empty, SectionCard, Skeleton, Table, formatDate } from '../components/ui'

/**
 * РАЗОБРАННАЯ ДОКУМЕНТАЦИЯ — что программа прочитала в загруженных томах.
 *
 * Экран отвечает на вопрос, которого раньше задать было негде: «а что
 * вообще увидела программа в моём томе?». До этого единственным видимым
 * результатом был отчёт, и пустой отчёт нельзя было отличить от тома,
 * который не прочитался (Г.10).
 *
 * Показывается ВЫЖИМКА, а не всё подряд: разбор тома в сотни листов — это
 * тысячи строк, и вывалить их целиком значит не показать ничего. Подробности
 * — постранично и по требованию, за раскрытием строки.
 */

function Number_({ label, value, tone }: { label: string; value: number | string; tone?: 'warn' }) {
  return (
    <div className="rounded border border-surface-line px-3 py-2">
      <div className={`text-lg font-semibold ${tone === 'warn' ? 'text-amber-600' : 'text-ink'}`}>
        {value}
      </div>
      <div className="text-xs text-ink-faint">{label}</div>
    </div>
  )
}

function List({ title, items, total }: { title: string; items: string[]; total?: number }) {
  if (!items.length) return null
  const more = total != null && total > items.length ? total - items.length : 0
  return (
    <div className="text-xs">
      <span className="text-ink-faint">{title}: </span>
      <span className="text-ink-muted">{items.join(' · ')}</span>
      {more > 0 && <span className="text-ink-faint"> … и ещё {more}</span>}
    </div>
  )
}

/**
 * Две независимые дорожки разбора тома. Текстовый слой читается локально,
 * графика ждёт отдельной визуальной сверки; смешивать их в один «разобрано»
 * нельзя, потому что отсутствие текста на скане не говорит об отсутствии
 * сведений на листе.
 */
function ProcessingBreakdown({ digest }: { digest: import('../backendApi').BackendDocumentDigest }) {
  return (
    <div className="mt-3 grid gap-2 sm:grid-cols-2">
      <div className="rounded border border-surface-line bg-surface-muted/40 px-3 py-2 text-xs text-ink-muted">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-ink">Текстовая часть</span>
          <Chip tone="neutral">{digest.text_pages} стр. с текстом</Chip>
        </div>
        <p className="mt-1">Текстовый слой извлечён локально. {digest.pages_without_text > 0
          ? `${digest.pages_without_text} стр. без слоя не участвуют в текстовой проверке.`
          : 'Все страницы доступны для текстовой проверки.'}</p>
      </div>
      <div className="rounded border border-surface-line bg-surface-muted/40 px-3 py-2 text-xs text-ink-muted">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-ink">Графические материалы</span>
          <Chip tone="neutral">{digest.drawings} чертежей</Chip>
        </div>
        <p className="mt-1">Визуальная сверка выполняется отдельно после подбора пары листов; это не результат текстовой проверки.</p>
      </div>
    </div>
  )
}

/**
 * Состояние переданного комплекта, а не оценка его нормативной полноты.
 *
 * Сервер пока не сообщает ожидаемый состав документации, поэтому интерфейс
 * может честно показать только наличие томов и готовность их разбора. Это
 * всё же позволяет сразу увидеть недостающую сторону сопоставления и файл,
 * который не вошёл в обработку.
 */
function InputCompleteness({
  docs, coverage, loading, error,
}: {
  docs: BackendDocument[]
  coverage?: BackendCoverageReport
  loading: boolean
  error: Error | null
}) {
  const sides: Array<{ key: BackendDocument['side']; title: string }> = [
    { key: 'before', title: 'Проектная документация' },
    { key: 'after', title: 'Рабочая и исполнительная документация' },
  ]
  return (
    <div className="mb-4 grid gap-2 sm:grid-cols-2">
      {sides.map(({ key, title }) => {
        const sideDocs = docs.filter((doc) => doc.side === key)
        const ids = new Set(sideDocs.map((doc) => doc.id))
        const rows = coverage?.documents.filter((row) => ids.has(row.document_id)) ?? []
        const ready = rows.filter((row) => row.status === 'processed')
        const partial = rows.filter((row) => row.status === 'partial')
        const failedCoverage = rows.filter((row) => row.status === 'error' || row.status === 'missing')
        const parsing = sideDocs.filter((doc) => doc.status === 'parsing')
        const failed = sideDocs.filter((doc) => doc.status === 'error')
        const fullyCovered = sideDocs.length > 0 && rows.length === sideDocs.length
          && ready.length === sideDocs.length
        return (
          <div key={key} className="rounded border border-surface-line p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-ink">{title}</span>
              {sideDocs.length === 0
                ? <Chip tone="warn">не загружена</Chip>
                : loading
                  ? <Chip tone="neutral">проверяется…</Chip>
                  : error
                    ? <Chip tone="warn">покрытие неизвестно</Chip>
                    : <Chip tone={fullyCovered ? 'accent' : 'warn'}>
                        {ready.length} из {sideDocs.length} полностью обработаны
                      </Chip>}
            </div>
            {sideDocs.length === 0 ? (
              <p className="mt-1 text-xs text-ink-muted">В этой части комплекта нет переданных документов.</p>
            ) : (
              <p className="mt-1 text-xs text-ink-muted">
                {loading && 'Сервер проверяет сохранённое покрытие страниц. '}
                {error && `Отчёт покрытия недоступен: ${error.message}. `}
                {parsing.length > 0 && `разбираются: ${parsing.length}. `}
                {failed.length > 0 && `не обработаны: ${failed.map((doc) => doc.name).join(', ')}. `}
                {partial.length > 0 && `частично обработаны: ${partial.length}. `}
                {failedCoverage.length > 0 && `ошибки покрытия: ${failedCoverage.length}. `}
                {fullyCovered && 'Все страницы переданных документов учтены первичным разбором.'}
              </p>
            )}
          </div>
        )
      })}
      <p className="sm:col-span-2 text-xs text-ink-faint">
        Это состояние переданных файлов, а не вывод о нормативной комплектности: ожидаемый состав сервер не сообщает.
      </p>
    </div>
  )
}

function DocumentCard({
  doc, coverage,
}: {
  doc: BackendDocument
  coverage?: BackendDocumentCoverage
}) {
  const [openPages, setOpenPages] = useState(false)
  const ready = doc.status === 'ok'
  const digest = useQuery({
    queryKey: ['document-digest', doc.id],
    queryFn: () => backendApi.getDocumentDigest(doc.id),
    enabled: ready,
  })
  const pages = useQuery({
    queryKey: ['document-pages', doc.id],
    queryFn: () => backendApi.getDocumentPages(doc.id),
    enabled: ready && openPages,
  })

  return (
    <div className="rounded border border-surface-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{doc.name}</span>
        <Chip tone="neutral">{doc.side === 'before' ? 'ПД' : 'РД/ИД'}</Chip>
        {doc.discipline_code ? <Chip tone="accent">{doc.discipline_code}</Chip>
                             : <Chip tone="warn">раздел не определён</Chip>}
        {doc.status === 'parsing' && <Chip tone="warn">разбирается…</Chip>}
        {doc.status === 'error' && <Chip tone="warn">разбор не удался</Chip>}
        <span className="ml-auto text-xs text-ink-faint">{formatDate(doc.uploaded_at)}</span>
      </div>

      {doc.status === 'error' && (
        <p className="mt-2 text-xs text-amber-700">
          Сервер отметил ошибку разбора, но причину для этого документа не передал. Файл не вошёл в результаты разбора.
        </p>
      )}
      {!ready && doc.status === 'parsing' && (
        <p className="mt-2 text-xs text-ink-faint">
          Идёт разбор. Он не привязан к этой вкладке: можно уйти и вернуться.
        </p>
      )}

      {ready && digest.isLoading && <Skeleton rows={2} />}
      {ready && digest.error && (
        <p className="mt-2 text-xs text-danger">
          Не удалось загрузить сводку разбора: {digest.error instanceof Error ? digest.error.message : 'неизвестная ошибка'}
        </p>
      )}
      {ready && digest.data && (
        <>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
            <Number_ label="листов" value={digest.data.pages} />
            <Number_ label="чертежей" value={digest.data.drawings} />
            <Number_ label="с текстом" value={digest.data.text_pages} />
            <Number_ label="помещений" value={digest.data.rooms_total} />
            <Number_ label="позиций оборудования" value={digest.data.equipment_total} />
          </div>

          <ProcessingBreakdown digest={digest.data} />

          {digest.data.pages_without_text > 0 && (
            <p className="mt-2 text-xs text-amber-700">
              Листов без текстового слоя: {digest.data.pages_without_text}
              {digest.data.pages_without_text_list.length > 0 &&
                ` (${digest.data.pages_without_text_list.join(', ')}${
                  digest.data.pages_without_text > digest.data.pages_without_text_list.length ? ', …' : ''})`}
              {' — '}подписи в кривых. По ним текстовый путь не даёт ничего: это «не читано», а не «чисто».
            </p>
          )}
          {digest.data.excluded > 0 && (
            <p className="mt-1 text-xs text-ink-faint">
              Исключено из сравнения листов: {digest.data.excluded}
              {digest.data.excluded_reasons.length > 0 && ` — ${digest.data.excluded_reasons.join('; ')}`}
            </p>
          )}

          <div className="mt-2 space-y-1">
            <List title="Помещения" items={digest.data.rooms} total={digest.data.rooms_total} />
            <List title="Оборудование" items={digest.data.equipment} total={digest.data.equipment_total} />
            <List title="Листы" items={digest.data.sheets} total={digest.data.sheets_total} />
            <List title="Системы" items={digest.data.systems} />
          </div>

          <button className="mt-3 text-xs text-accent underline"
            onClick={() => setOpenPages((v) => !v)}>
            {openPages ? 'скрыть постраничное покрытие' : 'показать постраничное покрытие'}
          </button>
          {openPages && pages.isLoading && <Skeleton rows={4} />}
          {openPages && pages.error && (
            <p className="mt-2 text-xs text-danger">
              Не удалось загрузить постраничную сводку: {pages.error instanceof Error ? pages.error.message : 'неизвестная ошибка'}
            </p>
          )}
          {openPages && pages.data && (
            <div className="mt-2 max-h-96 overflow-auto">
              {coverage ? (
                <p className="mb-2 text-xs text-ink-muted">
                  Обработано: {coverage.pages.filter((row) => row.status === 'processed').length}
                  {' из '}{coverage.page_count ?? 'неизвестного числа'} листов.
                  {' '}Не обработано: {coverage.pages.filter((row) => row.status === 'unprocessed').length}.
                  {' '}Исключено: {coverage.pages.filter((row) => row.status === 'excluded').length}.
                  {' '}Без текста: {coverage.pages.filter((row) => row.text_status === 'unavailable').length}.
                </p>
              ) : (
                <p className="mb-2 text-xs text-amber-700">
                  Отчёт покрытия недоступен; количество строк ниже не доказывает полноту разбора.
                </p>
              )}
              <Table head={['Лист', 'Покрытие', 'Вид', 'Название по штампу', 'Помещения', 'Позиций', 'Знаков']}>
                {pages.data.map((row) => (
                  <tr key={row.page} className="border-t border-surface-line">
                    <td className="px-2 py-1">{row.page}</td>
                    <td className="px-2 py-1 text-ink-faint">
                      {coverage?.pages.find((item) => item.page === row.page)?.status === 'processed'
                        ? 'обработан'
                        : coverage?.pages.find((item) => item.page === row.page)?.status === 'excluded'
                          ? 'исключён'
                          : coverage ? 'не обработан' : 'неизвестно'}
                    </td>
                    <td className="px-2 py-1 text-ink-faint">
                      {row.kind === 'drawing' ? 'чертёж' : row.kind === 'text' ? 'текст' : '—'}
                    </td>
                    <td className="px-2 py-1">
                      {row.excluded
                        ? <span className="text-amber-700">исключён: {row.excluded}</span>
                        : [row.sheet_no, row.sheet_name].filter(Boolean).join(' · ') || '—'}
                    </td>
                    <td className="px-2 py-1">{row.rooms.join(', ') || '—'}</td>
                    <td className="px-2 py-1">{row.equipment || '—'}</td>
                    <td className="px-2 py-1 text-ink-faint">{row.chars || '—'}</td>
                  </tr>
                ))}
              </Table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function RunCard({ runId }: { runId: number }) {
  const [open, setOpen] = useState(false)
  const requirements = useQuery({
    queryKey: ['pd-run-requirements', runId],
    queryFn: () => backendApi.getPdRunRequirements(runId),
    enabled: open,
  })
  return (
    <div className="mt-2">
      <button className="text-xs text-accent underline" onClick={() => setOpen((v) => !v)}>
        {open ? 'скрыть требования' : 'показать требования'}
      </button>
      {open && requirements.isLoading && <Skeleton rows={3} />}
      {open && requirements.error && (
        <p className="mt-1 text-xs text-ink-faint">
          {requirements.error instanceof Error ? requirements.error.message : 'не удалось загрузить'}
        </p>
      )}
      {open && requirements.data && (
        <div className="mt-2 max-h-96 space-y-2 overflow-auto">
          {requirements.data.length === 0 && (
            <p className="text-xs text-ink-faint">
              Прогон завершился, требований не извлечено. Это не «в документе их нет»:
              смотрите состав комплекта и связь с ИИ в карточке разбора.
            </p>
          )}
          {requirements.data.map((r, i) => (
            <div key={i} className="rounded border border-surface-line p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                {r.section && <Chip tone="accent">{r.section}</Chip>}
                <span className="text-ink-faint">{r.document} · лист {r.page}</span>
                {r.rooms.length > 0 && <span className="text-ink-faint">пом. {r.rooms.join(', ')}</span>}
                {r.norm && <Chip tone="neutral">{r.norm}</Chip>}
              </div>
              <p className="mt-1 text-ink">{r.summary || r.sentence}</p>
              {r.summary && <p className="mt-1 text-ink-faint">{r.sentence}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ParsedDocs() {
  const { docs, isLoading } = useDocuments()
  const documentIds = docs.map((doc) => doc.id).sort((a, b) => a - b)
  const coverage = useQuery({
    queryKey: ['document-coverage', documentIds],
    queryFn: () => backendApi.getDocumentCoverage(documentIds),
    enabled: documentIds.length > 0,
  })
  const coverageById = new Map(
    (coverage.data?.documents ?? []).map((row) => [row.document_id, row]),
  )
  const runs = useQuery({ queryKey: ['pd-runs'], queryFn: backendApi.listPdRuns })

  return (
    <div className="space-y-4">
      <SectionCard title="Разобранные документы"
        subtitle="Что программа прочитала в томе. Подробности — постранично, по требованию">
        {isLoading && <Skeleton rows={3} />}
        {!isLoading && docs.length === 0 && (
          <Empty title="Документы не загружены"
            hint="Загрузите тома на экране «Новый анализ» — разбор начнётся сам, в фоне" />
        )}
        {docs.length > 0 && (
          <InputCompleteness
            docs={docs}
            coverage={coverage.data}
            loading={coverage.isLoading}
            error={coverage.error instanceof Error ? coverage.error : null}
          />
        )}
        <div className="space-y-3">
          {docs.map((doc) => (
            <DocumentCard key={doc.id} doc={doc} coverage={coverageById.get(doc.id)} />
          ))}
        </div>
      </SectionCard>

      <SectionCard title="Прогоны разбора"
        subtitle="Каждый прогон — это то, что извлекли из выбранных томов">
        {runs.isLoading && <Skeleton rows={3} />}
        {runs.data && runs.data.length === 0 && (
          <Empty title="Разбор ещё не запускали"
            hint="Кнопка «Разобрать документацию» на экране «Новый анализ»" />
        )}
        <div className="space-y-3">
          {(runs.data ?? []).map((run) => (
            <div key={run.id} className="rounded border border-surface-line p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">Прогон №{run.id}</span>
                <Chip tone="neutral">{run.side === 'before' ? 'ПД' : 'РД/ИД'}</Chip>
                <Chip tone={run.status === 'done' ? 'accent' : 'warn'}>
                  {run.status === 'done' ? 'завершён'
                    : run.status === 'running' ? 'идёт'
                    : run.status === 'cancelled' ? 'остановлен инспектором' : 'ошибка'}
                </Chip>
                <span className="text-xs text-ink-faint">
                  требований: {run.requirements_total}
                  {run.extractor && ` · извлечение: ${run.extractor}`}
                </span>
                <span className="ml-auto text-xs text-ink-faint">{formatDate(run.created_at)}</span>
              </div>
              {run.error && <p className="mt-1 text-xs text-amber-700">{run.error}</p>}
              {run.summary && (
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-surface-muted p-2 text-xs text-ink-muted">
                  {run.summary}
                </pre>
              )}
              {run.store_run_id != null && <RunCard runId={run.id} />}
            </div>
          ))}
        </div>
      </SectionCard>
    </div>
  )
}
