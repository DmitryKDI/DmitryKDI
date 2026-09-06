/** Обращения к серверу. Токен хранится в браузере и передаётся заголовком. */
const TOKEN_KEY = 'nadzor.token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(value: string): void {
  if (value) localStorage.setItem(TOKEN_KEY, value)
  else localStorage.removeItem(TOKEN_KEY)
}

/** Ключ zustand-persist из store.ts. Дублируется здесь осознанно: импорт
 *  store в api.ts создал бы цикл, а очистка сессии обязана работать даже
 *  тогда, когда до React-слоя дело ещё не дошло. */
const STORE_KEY = 'nadzor.app'

/** Сброс сессии до конца: токен, сохранённая роль и права.
 *
 *  Чистить один токен недостаточно: `principal` переживает перезагрузку в
 *  localStorage, и браузер с сессией от прошлого запуска (другая база, другой
 *  сервер) показывал экраны, которые заведомо не могут загрузиться. Уходим на
 *  вход через `location`, а не через роутер, — так гарантированно сбрасывается
 *  и состояние в памяти, включая кеш запросов.
 */
export function clearSession(): void {
  setToken('')
  localStorage.removeItem(STORE_KEY)
  if (!window.location.pathname.startsWith('/login')) window.location.replace('/login')
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(`/api${path}`, { ...init, headers })
  if (response.status === 401) {
    clearSession()
    throw new ApiError(401, 'Сессия завершена. Войдите заново.')
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: 'Не удалось выполнить запрос.' }))
    throw new ApiError(response.status, detail.detail || 'Не удалось выполнить запрос.')
  }
  return response.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  async upload<T>(path: string, form: FormData): Promise<T> {
    const token = getToken()
    const response = await fetch(`/api${path}`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    })
    if (response.status === 401) {
      setToken('')
      throw new ApiError(401, 'Сессия завершена. Войдите заново.')
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ detail: 'Не удалось загрузить файл.' }))
      throw new ApiError(response.status, detail.detail || 'Не удалось загрузить файл.')
    }
    return response.json() as Promise<T>
  },
  async download(path: string, body: unknown, filename: string) {
    const response = await fetch(`/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
      body: JSON.stringify(body),
    })
    if (!response.ok) throw new ApiError(response.status, 'Не удалось сформировать документ.')
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  },
  pageUrl: (documentId: string, page: number) => `/api/documents/${documentId}/page/${page}`,
}
