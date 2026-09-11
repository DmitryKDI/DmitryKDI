import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import {
  backendApi,
  BackendApiError,
  type BackendAnalysisRun,
  type BackendComplianceRun,
  type BackendPdRun,
  type BackendTriangulatedRun,
} from './backendApi'

interface Toast {
  id: number
  text: string
  kind: 'ok' | 'error'
  undo?: () => void
}

export interface PendingUpload { name: string; startedAt: number }

type PendingUpdater = PendingUpload[] | ((prev: PendingUpload[]) => PendingUpload[])

interface AppState {
  menuCollapsed: boolean
  density: 'comfortable' | 'compact'
  filters: Record<string, Record<string, string>>
  checkedAttention: Record<string, boolean>
  toasts: Toast[]
  // Состояние экрана "Новый анализ" живёт здесь, а не в useState компонента:
  // переход на другую вкладку меню размонтирует NewAnalysis, и локальный
  // useState (включая уже загруженные документы и идущий прогон) терялся бы.
  analysisPendingBefore: PendingUpload[]
  analysisPendingAfter: PendingUpload[]
  analysisRunId: number | null
  // Прогресс прогона — отдельно от analysisRunId: обновляется фоновым
  // опросом (см. pollAnalysisRun ниже), который не привязан к тому, что
  // экран "Новый анализ" сейчас смонтирован, — так прогон реально продолжает
  // считаться, пока инспектор смотрит другие вкладки, а не замирает.
  analysisRunStatus: BackendAnalysisRun | null
  // Прогон реального движка Приложения Г (Карта внимания, см.
  // triangulated_pipeline.py) — тот же принцип фонового опроса, что и у
  // analysisRunId выше, но отдельный движок и отдельный набор полей: эти
  // два прогона не смешивают статус друг друга.
  triangulatedRunId: number | null
  triangulatedRunStatus: BackendTriangulatedRun | null
  // Прогоны трёх кнопок инспектора (разбор ПД, разбор РД, сверка). Здесь, а
  // не в useState экрана: разбор тома идёт минутами, и переход на другую
  // вкладку не должен ни останавливать его, ни терять его результат.
  // Опрос ведётся из стора и переживает размонтирование экрана.
  pdRunId: number | null
  pdRunStatus: BackendPdRun | null
  rdRunId: number | null
  rdRunStatus: BackendPdRun | null
  complianceRunId: number | null
  complianceRunStatus: BackendComplianceRun | null
  // Какие документы разбирать: пусто — все загруженные с этой стороны.
  // Выбор живёт рядом с прогоном, потому что относится к тому же действию.
  pdSelected: number[]
  rdSelected: number[]
  toggleMenu: () => void
  setDensity: (value: 'comfortable' | 'compact') => void
  setFilter: (screen: string, key: string, value: string) => void
  toggleChecked: (id: string) => void
  pushToast: (text: string, kind?: 'ok' | 'error', undo?: () => void) => void
  dropToast: (id: number) => void
  setAnalysisPending: (side: 'before' | 'after', updater: PendingUpdater) => void
  setAnalysisRunId: (id: number | null) => void
  setPdRunId: (side: 'before' | 'after', id: number | null) => void
  setComplianceRunId: (id: number | null) => void
  setSelected: (side: 'before' | 'after', ids: number[]) => void
  setTriangulatedRunId: (id: number | null) => void
  resetAnalysis: () => void
}

export const useApp = create<AppState>()(
  persist(
    (set, get) => ({
      menuCollapsed: false,
      density: 'comfortable',
      filters: {},
      checkedAttention: {},
      toasts: [],
      analysisPendingBefore: [],
      analysisPendingAfter: [],
      analysisRunId: null,
      analysisRunStatus: null,
      triangulatedRunId: null,
      triangulatedRunStatus: null,
      pdRunId: null,
      pdRunStatus: null,
      rdRunId: null,
      rdRunStatus: null,
      complianceRunId: null,
      complianceRunStatus: null,
      pdSelected: [],
      rdSelected: [],
      toggleMenu: () => set((s) => ({ menuCollapsed: !s.menuCollapsed })),
      setDensity: (density) => set({ density }),
      setFilter: (screen, key, value) =>
        set((s) => ({ filters: { ...s.filters, [screen]: { ...s.filters[screen], [key]: value } } })),
      toggleChecked: (id) =>
        set((s) => ({ checkedAttention: { ...s.checkedAttention, [id]: !s.checkedAttention[id] } })),
      pushToast: (text, kind = 'ok', undo) => {
        const id = Date.now() + Math.random()
        set((s) => ({ toasts: [...s.toasts, { id, text, kind, undo }] }))
        setTimeout(() => get().dropToast(id), 6000)
      },
      dropToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
      setAnalysisPending: (side, updater) =>
        set((s) => {
          const key = side === 'before' ? 'analysisPendingBefore' : 'analysisPendingAfter'
          const prev = s[key]
          return { [key]: typeof updater === 'function' ? updater(prev) : updater }
        }),
      setAnalysisRunId: (analysisRunId) => {
        set({ analysisRunId, analysisRunStatus: null })
        if (analysisRunId != null) pollAnalysisRun(analysisRunId)
      },
      setPdRunId: (side, id) => {
        const key = side === 'before' ? 'pdRunId' : 'rdRunId'
        const statusKey = side === 'before' ? 'pdRunStatus' : 'rdRunStatus'
        set({ [key]: id, [statusKey]: null } as Partial<AppState>)
        if (id != null) pollPdRun(side, id)
      },
      setComplianceRunId: (complianceRunId) => {
        set({ complianceRunId, complianceRunStatus: null })
        if (complianceRunId != null) pollComplianceRun(complianceRunId)
      },
      setSelected: (side, ids) =>
        set(side === 'before' ? { pdSelected: ids } : { rdSelected: ids }),
      setTriangulatedRunId: (triangulatedRunId) => {
        set({ triangulatedRunId, triangulatedRunStatus: null })
        if (triangulatedRunId != null) pollTriangulatedRun(triangulatedRunId)
      },
      resetAnalysis: () => set({
        analysisPendingBefore: [], analysisPendingAfter: [],
        analysisRunId: null, analysisRunStatus: null,
        triangulatedRunId: null, triangulatedRunStatus: null,
        pdRunId: null, pdRunStatus: null, rdRunId: null, rdRunStatus: null,
        complianceRunId: null, complianceRunStatus: null,
        pdSelected: [], rdSelected: [],
      }),
    }),
    {
      name: 'nadzor.app',
      partialize: (s) => ({
        menuCollapsed: s.menuCollapsed,
        density: s.density,
        filters: s.filters,
        checkedAttention: s.checkedAttention,
        // Список документов здесь НЕ хранится (Г.114): он живёт на сервере,
        // и копия в браузере переживала пересборку базы, чужое удаление и
        // ручную правку раздела — инспектор видел то, чего уже нет.
        analysisRunId: s.analysisRunId,
        triangulatedRunId: s.triangulatedRunId,
        // Прогон живёт на сервере, поэтому сохраняем только его номер и
        // выбор томов: состояние подтянется опросом при следующем открытии.
        pdRunId: s.pdRunId,
        rdRunId: s.rdRunId,
        complianceRunId: s.complianceRunId,
        pdSelected: s.pdSelected,
        rdSelected: s.rdSelected,
      }),
      onRehydrateStorage: () => (state) => {
        // Прогон мог остаться незавершённым, пока страница была закрыта —
        // одним запросом узнаём актуальный статус и, если он ещё не готов,
        // продолжаем фоновый опрос сразу, не дожидаясь открытия "Нового
        // анализа" (см. pollAnalysisRun — опрос не привязан к монтированию
        // конкретного экрана, поэтому продолжается на любой странице сайта).
        // setTimeout, а не прямой вызов: гидратация может завершиться синхронно
        // внутри самого create(), когда переменная useApp ещё не присвоена —
        // pollAnalysisRun читает её через useApp.getState().
        if (state?.analysisRunId != null) {
          const id = state.analysisRunId
          setTimeout(() => pollAnalysisRun(id), 0)
        }
        if (state?.triangulatedRunId != null) {
          const id = state.triangulatedRunId
          setTimeout(() => pollTriangulatedRun(id), 0)
        }
        // Разбор и сверка идут на сервере, поэтому переживают перезагрузку
        // страницы: опрос просто подхватывается заново по сохранённому id.
        if (state?.pdRunId != null) {
          const id = state.pdRunId
          setTimeout(() => pollPdRun('before', id), 0)
        }
        if (state?.rdRunId != null) {
          const id = state.rdRunId
          setTimeout(() => pollPdRun('after', id), 0)
        }
        if (state?.complianceRunId != null) {
          const id = state.complianceRunId
          setTimeout(() => pollComplianceRun(id), 0)
        }
      },
    },
  ),
)

/** Ответ, после которого повторять бессмысленно: записи больше нет.
 *  Сетевой сбой и «не найдено» требуют разного — первое повторяют, второе
 *  забывают. Без этого различия опрос удалённого прогона крутится вечно. */
function isGone(error: unknown): boolean {
  return error instanceof BackendApiError && error.status >= 400 && error.status < 500
}

function isTerminalStatus(status: string): boolean {
  return status === 'done' || status === 'error' || status === 'cancelled'
}

let pollTimer: ReturnType<typeof setTimeout> | null = null

function pollAnalysisRun(runId: number): void {
  if (pollTimer) clearTimeout(pollTimer)
  const tick = async () => {
    // Пока запрос летел, мог начаться другой прогон (или анализ сбросили) —
    // не затираем более новое состояние устаревшим ответом.
    if (useApp.getState().analysisRunId !== runId) return
    let data: BackendAnalysisRun
    try {
      data = await backendApi.getAnalysisRun(runId)
    } catch (error) {
      // Прогона нет — сервер перезапустили с новой схемой базы, запись
      // удалили. Это навсегда, а не «сеть моргнула»: продолжать опрос
      // значит бесконечно сыпать 404 в консоль и держать в интерфейсе
      // работу, которой не существует (Г.112).
      if (isGone(error)) {
        useApp.setState({ analysisRunId: null, analysisRunStatus: null })
        return
      }
      pollTimer = setTimeout(tick, 800)
      return
    }
    if (useApp.getState().analysisRunId !== runId) return
    useApp.setState({ analysisRunStatus: data })
    if (!isTerminalStatus(data.status)) {
      pollTimer = setTimeout(tick, 800)
    }
  }
  void tick()
}

let triangulatedPollTimer: ReturnType<typeof setTimeout> | null = null

function pollTriangulatedRun(runId: number): void {
  if (triangulatedPollTimer) clearTimeout(triangulatedPollTimer)
  const tick = async () => {
    if (useApp.getState().triangulatedRunId !== runId) return
    let data: BackendTriangulatedRun
    try {
      data = await backendApi.getTriangulatedRun(runId)
    } catch {
      triangulatedPollTimer = setTimeout(tick, 800)
      return
    }
    if (useApp.getState().triangulatedRunId !== runId) return
    useApp.setState({ triangulatedRunStatus: data })
    if (!isTerminalStatus(data.status)) {
      triangulatedPollTimer = setTimeout(tick, 800)
    }
  }
  void tick()
}

// Опрос разбора и сверки. Тот же приём, что у прогонов выше: таймер живёт в
// модуле, а не в компоненте, поэтому переход на другую вкладку меню не
// прерывает наблюдение за работой, которая идёт на сервере.
const pdPollTimers: Record<'before' | 'after', ReturnType<typeof setTimeout> | null> = {
  before: null, after: null,
}

function pollPdRun(side: 'before' | 'after', runId: number): void {
  const idKey = side === 'before' ? 'pdRunId' : 'rdRunId'
  const statusKey = side === 'before' ? 'pdRunStatus' : 'rdRunStatus'
  if (pdPollTimers[side]) clearTimeout(pdPollTimers[side] as ReturnType<typeof setTimeout>)
  const tick = async () => {
    if (useApp.getState()[idKey] !== runId) return
    let data: BackendPdRun
    try {
      data = await backendApi.getPdRun(runId)
    } catch (error) {
      if (isGone(error)) {
        useApp.setState({ [idKey]: null, [statusKey]: null } as Partial<AppState>)
        return
      }
      pdPollTimers[side] = setTimeout(tick, 1000)
      return
    }
    if (useApp.getState()[idKey] !== runId) return
    useApp.setState({ [statusKey]: data } as Partial<AppState>)
    if (!isTerminalStatus(data.status)) {
      pdPollTimers[side] = setTimeout(tick, 1000)
    }
  }
  void tick()
}

let compliancePollTimer: ReturnType<typeof setTimeout> | null = null

function pollComplianceRun(runId: number): void {
  if (compliancePollTimer) clearTimeout(compliancePollTimer)
  const tick = async () => {
    if (useApp.getState().complianceRunId !== runId) return
    let data: BackendComplianceRun
    try {
      data = await backendApi.getComplianceRun(runId)
    } catch (error) {
      if (isGone(error)) {
        useApp.setState({ complianceRunId: null, complianceRunStatus: null })
        return
      }
      compliancePollTimer = setTimeout(tick, 1000)
      return
    }
    if (useApp.getState().complianceRunId !== runId) return
    useApp.setState({ complianceRunStatus: data })
    if (!isTerminalStatus(data.status)) {
      compliancePollTimer = setTimeout(tick, 1000)
    }
  }
  void tick()
}
