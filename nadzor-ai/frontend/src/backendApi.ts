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
  error: string | null
}

export interface BackendFinding {
  id: number
  run_id: number
  pair_id: number | null
  kind: 'text' | 'vision'
  label: string
  change_text: string
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

export interface BackendSettings {
  provider: 'local' | 'openai' | 'anthropic' | 'google'
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

  listFindings: (runId: number) => request<BackendFinding[]>(`/findings?run_id=${runId}`),
  updateFinding: (id: number, reviewedStatus: BackendFinding['reviewed_status']) =>
    request<BackendFinding>(`/findings/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewed_status: reviewedStatus }),
    }),

  getSettings: () => request<BackendSettings>('/settings'),
  updateSettings: (settings: BackendSettings) =>
    request<BackendSettings>('/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
}
