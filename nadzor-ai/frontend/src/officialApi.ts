/**
 * Контракт официальной проверки. Он намеренно отделён от старого клиента:
 * здесь три стадии комплекта и результат по параметрам матрицы, а не только
 * пара «до/после».
 */
export class OfficialApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/backend${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: 'Сервер недоступен.' }))
    throw new OfficialApiError(response.status, body.detail || 'Сервер вернул ошибку.')
  }
  return response.json() as Promise<T>
}

export type OfficialStage = 'PD' | 'RD' | 'ID'
export type ApprovalStatus = 'DRAFT' | 'APPROVED' | 'FOR_CONSTRUCTION' | 'SUPERSEDED' | 'CANCELLED'
export type FindingStatus = 'CANDIDATE' | 'NEGATIVE_VERIFIED' | 'CONFIRMED_VIOLATION' | 'SUSPICION' | 'CLARIFICATION_REQUIRED'
export type TechnicalStatus = 'completed' | 'not_run' | 'error'

export interface OfficialParameter {
  code: string
  name: string
  section: string
  unit: string | null
  priority: 'HIGH' | 'MEDIUM'
  source_pd: string
  source_rd: string
  source_id: string
  trigger: string
}

export interface OfficialDocumentMetadata {
  object_id: string
  stage: OfficialStage
  document_code: string
  revision: string
  approval_status: ApprovalStatus
  approval_date: string | null
  predecessor_id: number | null
  signature_status: string | null
  sheet_page_range: string | null
}

export interface OfficialDocument {
  id: number
  name: string
  pages: number
  status: string
  digest: string
  metadata: OfficialDocumentMetadata
}

export interface OfficialEvidence {
  document_id: number
  file_id: string | null
  sha256: string | null
  stage: OfficialStage
  role?: 'expected' | 'actual'
  page: number
  bbox: [number, number, number, number] | null
  quote: string | null
}

export interface ReviewHistoryItem {
  status: FindingStatus
  author: string
  reason: string
  created_at?: string
  version?: number
}

export interface OfficialCheck {
  finding_id: string
  parameter_code: string
  parameter_name: string
  priority: 'HIGH' | 'MEDIUM'
  completeness_status: string
  finding_status: FindingStatus | null
  expected_value: string | null
  actual_value: string | null
  explanation: string
  evidence: OfficialEvidence[]
  technical_status: TechnicalStatus
  review_history: ReviewHistoryItem[]
  confidence?: number | null
}

export interface OfficialRunResult {
  matrix_version: string
  object_id: string
  checks: OfficialCheck[]
  coverage: { total: number; completed: number; not_run: number }
  document_selection?: {
    selected: Partial<Record<OfficialStage, number[]>>
    problems: Partial<Record<OfficialStage, string>>
  }
  graphic_analysis: {
    status: 'completed' | 'incomplete' | 'not_run' | 'error'
    reason: string
    candidates: OfficialCheck[]
    performance: { duration_seconds?: number }
  }
}

export interface OfficialRun {
  id: number
  object_id: string
  status: 'queued' | 'running' | 'completed' | 'cancelled' | 'error'
  stage: string
  completed: number
  total: number
  result: OfficialRunResult | null
  error: string | null
  version?: number
  /** Статус процесса в словаре ТЗ: PENDING … FINALIZED. */
  process_status?: string
  verification_status?: string
  finalized_at?: string | null
  protocol?: {
    scenario: string
    upload_status: Record<string, string>
    pending_candidates: string[]
    versions: Record<string, string>
  }
}

/** Кодированные причины отклонения кандидата (ТЗ 9.3). */
export const REASON_CODES: Record<string, string> = {
  WRONG_REVISION: 'неверно выбрана актуальная редакция',
  APPROVED_CHANGE: 'есть согласованное изменение',
  OCR_ERROR: 'ошибка распознавания',
  BINDING_ERROR: 'ошибка привязки доказательства',
  NOT_APPLICABLE: 'параметр неприменим',
  NO_DIFFERENCE: 'расхождения нет',
  OTHER: 'иное (см. основание)',
}

export interface DecisionInput {
  finding_id: string
  status: Extract<FindingStatus, 'CONFIRMED_VIOLATION' | 'NEGATIVE_VERIFIED' | 'CANDIDATE' | 'CLARIFICATION_REQUIRED'>
  author: string
  reason: string
  expected_version: number
  reason_code?: string
}

export interface ProviderSettings {
  provider: string
  model: string
  base_url: string
}

export interface ProviderCheck {
  reachable: boolean
  provider: string
  message: string
  tls?: string
  /** Какая модель выбрана сейчас. */
  model?: string
  /**
   * Обслуживает ли сервер выбранную модель. Намеренно трёхзначно: null —
   * «перечень не получен», а не «модели нет»: сбой связи и недоступную
   * модель инспектор чинит по-разному.
   */
  model_available?: boolean | null
  models_available?: string[]
  models_message?: string
}

export const officialApi = {
  parameters: () => request<{ matrix_version: string; parameters: OfficialParameter[] }>('/official/parameters'),
  documents: () => request<OfficialDocument[]>('/official/documents'),
  settings: () => request<ProviderSettings>('/settings'),
  checkProvider: () => request<ProviderCheck>('/llm-check'),
  upload: (side: 'before' | 'after', file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ id: number }>(`/documents?side=${side}`, { method: 'POST', body: form })
  },
  saveMetadata: (id: number, metadata: OfficialDocumentMetadata) => request<OfficialDocument>(
    `/official/documents/${id}/metadata`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(metadata) },
  ),
  deleteDocument: (id: number) => request<{ ok: boolean }>(`/documents/${id}`, {
    method: 'DELETE',
  }),
  createRun: (object_id: string, document_ids: number[], include_medium: boolean) => request<OfficialRun>(
    '/official/runs',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ object_id, document_ids, include_medium }),
    },
  ),
  run: (id: number) => request<OfficialRun>(`/official/runs/${id}`),
  runs: () => request<OfficialRun[]>('/official/runs'),
  cancelRun: (id: number) => request<OfficialRun>(`/official/runs/${id}/cancel`, { method: 'POST' }),
  decide: (id: number, input: DecisionInput) => request<OfficialRun>(
    `/official/runs/${id}/decisions`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) },
  ),
  exportUrl: (id: number, format: 'json' | 'csv') => `/backend/official/runs/${id}/export?format=${format}`,
  protocolUrl: (id: number, format: 'pdf' | 'docx' | 'xml') => `/backend/api/v1/processes/${id}/export?format=${format}`,
  finalize: (id: number, author: string) => request<OfficialRun>(
    `/official/runs/${id}/finalize`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ author }) },
  ),
  pageImageUrl: (documentId: number, page: number) => `/backend/page-image/${documentId}/${page}`,
}

export function findingLabel(status: FindingStatus | null, technical: TechnicalStatus): string {
  if (technical === 'not_run') return 'не выполнялось'
  if (technical === 'error') return 'техническая ошибка'
  if (status === 'CONFIRMED_VIOLATION') return 'подтверждено инспектором'
  if (status === 'NEGATIVE_VERIFIED') return 'не подтверждено инспектором'
  if (status === 'CANDIDATE') return 'кандидат для проверки'
  if (status === 'SUSPICION') return 'требует уточнения'
  if (status === 'CLARIFICATION_REQUIRED') return 'запрошено уточнение'
  return 'нет машинной оценки'
}
