/**
 * Обращения к packages/backend — отдельному лёгкому бэкенду сравнения
 * документов (без RBAC/аудита, см. packages/backend/README.md). Отдельный
 * клиент от api.ts: другой сервер, другой контракт, без токена авторизации —
 * инструмент однопользовательский, запускается локально на своём порту.
 */
export class BackendApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/backend${path}`, init)
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: 'Бэкенд недоступен или вернул ошибку.' }))
    throw new BackendApiError(response.status, detail.detail || 'Бэкенд недоступен или вернул ошибку.')
  }
  return response.json() as Promise<T>
}

export interface BackendDocument {
  id: number
  name: string
  side: 'before' | 'after'
  pages: number
  discipline_code: string | null
  classification_source: string | null
  status: 'parsing' | 'ok' | 'error'
  uploaded_at: string
}

export interface BackendAnalysisRun {
  id: number
  created_at: string
  status: 'running' | 'done' | 'error'
  pairs_total: number
  pairs_done: number
  /** Кто считал прогон — данные о работе ИИ, а не только итог. */
  provider: string
  model: string
  pairs_llm_ok: number
  pairs_llm_error: number
  error: string | null
}

export interface BackendPagePair {
  id: number
  before_document_id: number
  before_document_name: string
  before_page: number
  after_document_id: number
  after_document_name: string
  after_page: number
  matched_by: 'text' | 'position'
  page_kind: 'drawing' | 'text'
  discipline_mismatch: boolean
  llm_status: 'ok' | 'error'
  llm_error: string | null
}

export interface BackendFinding {
  id: number
  run_id: number
  pair_id: number | null
  kind: 'text' | 'vision'
  label: string
  change_text: string
  /** Ключи theme.severity. Пустая строка — модель не вернула степень внятно. */
  severity: '' | 'critical' | 'major' | 'minor'
  /** Что проверить или измерить на объекте. Пусто — проверять нечего. */
  field_check: string
  reviewed_status: 'new' | 'confirmed' | 'rejected'
  created_at: string
  before_document_id: number | null
  before_page: number | null
  after_document_id: number | null
  after_page: number | null
}

export function pageImageUrl(documentId: number, page: number): string {
  return `/backend/page-image/${documentId}/${page}`
}

/**
 * Реальный движок Приложения Г (`packages/backend/app/triangulated_pipeline.py`):
 * реестры помещений/оборудования, комплектность, требования из прозы —
 * сведённые в триангуляцию источников и очередь эскалации. Другой анализ,
 * чем `createAnalysisRun` выше (тот — прямое сравнение листов зрением,
 * `_run_analysis`): здесь находки идут не из одного вызова ИИ на пару
 * листов, а из независимых детерминированных сверок, подтверждённых
 * ТОЛЬКО когда минимум два источника указали на один и тот же номер
 * помещения/позиции (см. `triangulation.py`, Г.30 п.4).
 */
export interface TriangulationConfirmation {
  domain: string
  key: string
  status: 'confirmed' | 'candidate'
  sources: string[]
  details: string[]
}

export interface EscalationTicket {
  domain: string
  key: string
  sources_present: string[]
  sources_missing: string[]
  context: string[]
  question: string
}

export interface RoomFinding {
  room_key: string
  room_name_pd: string
  room_name_rd: string | null
  finding_type: 'missing_in_rd' | 'name_changed' | 'area_changed'
  detail: string
  severity: string
}

export interface EquipFinding {
  equip_key: string
  equip_name_pd: string
  equip_name_rd: string | null
  finding_type: 'missing_in_rd' | 'missing_in_pd' | 'qty_changed'
  detail: string
  severity: string
}

export interface CompositionFinding {
  designation: string
  finding_type: string
  detail: string
  reference_count: number
}

export interface TriangulatedResult {
  valid: boolean
  reason?: string
  documents?: { before: string[]; after: string[] }
  skipped_files: string[]
  llm?: { used: boolean; provider: string | null }
  /** Что реально НЕ проверялось в этом прогоне и почему (нет ключа ИИ и
   *  т.п.) — видимое состояние, а не молчаливый пропуск (Г.10). */
  not_run?: string[]
  rooms?: { total_pd: number; total_rd: number; matched: number; unmatched: number; findings: RoomFinding[] }
  equipment?: { total_pd: number; total_rd: number; matched: number; unmatched: number; findings: EquipFinding[] }
  composition?: { supplied_count: number; findings: CompositionFinding[] }
  requirements?: {
    coded: { total: number; confirmed: number; missing: number; source: 'llm' | 'regex' }
    general: { total: number; with_token: number; token_confirmed: number; token_missing: number; no_token: number }
  }
  routing?: { room_keys: string[]; auto_selected: boolean } | null
  triangulation?: { signals_count: number; confirmed: TriangulationConfirmation[]; candidates: TriangulationConfirmation[] }
  escalation_tickets?: EscalationTicket[]
  verdicts?: { domain: string; key: string; verdict: string; reasoning: string; sources: string[] }[]
}

export interface BackendTriangulatedRun {
  id: number
  created_at: string
  status: 'running' | 'done' | 'error'
  provider: string
  error: string | null
  result: TriangulatedResult | null
}

export interface BackendSettings {
  provider: 'local' | 'openai' | 'anthropic' | 'google' | 'yandexgpt'
  base_url: string
  model: string
  api_key: string
}

export const backendApi = {
  uploadDocument(side: 'before' | 'after', file: File): Promise<BackendDocument> {
    const form = new FormData()
    form.append('file', file)
    return request<BackendDocument>(`/documents?side=${side}`, { method: 'POST', body: form })
  },
  listDocuments: () => request<BackendDocument[]>('/documents'),
  deleteDocument: (id: number) => request<{ ok: boolean }>(`/documents/${id}`, { method: 'DELETE' }),

  createAnalysisRun: (beforeIds: number[], afterIds: number[]) =>
    request<BackendAnalysisRun>('/analysis-runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ before_document_ids: beforeIds, after_document_ids: afterIds }),
    }),
  getAnalysisRun: (id: number) => request<BackendAnalysisRun>(`/analysis-runs/${id}`),
  listPagePairs: (runId: number) => request<BackendPagePair[]>(`/analysis-runs/${runId}/pairs`),

  listFindings: (runId: number) => request<BackendFinding[]>(`/findings?run_id=${runId}`),
  updateFinding: (id: number, reviewedStatus: BackendFinding['reviewed_status']) =>
    request<BackendFinding>(`/findings/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewed_status: reviewedStatus }),
    }),

  createTriangulatedRun: (beforeIds: number[], afterIds: number[], roomKeys: string[] = []) =>
    request<BackendTriangulatedRun>('/triangulated-runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ before_document_ids: beforeIds, after_document_ids: afterIds, room_keys: roomKeys }),
    }),
  getTriangulatedRun: (id: number) => request<BackendTriangulatedRun>(`/triangulated-runs/${id}`),

  getSettings: () => request<BackendSettings>('/settings'),
  updateSettings: (settings: BackendSettings) =>
    request<BackendSettings>('/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
}
