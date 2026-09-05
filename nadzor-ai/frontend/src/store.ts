import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Principal } from './types'
import { backendApi, type BackendAnalysisRun, type BackendDocument, type BackendTriangulatedRun } from './backendApi'

interface Toast {
  id: number
  text: string
  kind: 'ok' | 'error'
  undo?: () => void
}

export interface PendingUpload { name: string; startedAt: number }

type DocsUpdater = BackendDocument[] | ((prev: BackendDocument[]) => BackendDocument[])
type PendingUpdater = PendingUpload[] | ((prev: PendingUpload[]) => PendingUpload[])

interface AppState {
  principal: Principal | null
  permissions: string[]
  menuCollapsed: boolean
  density: 'comfortable' | 'compact'
  filters: Record<string, Record<string, string>>
  checkedAttention: Record<string, boolean>
  toasts: Toast[]
  // Состояние экрана "Новый анализ" живёт здесь, а не в useState компонента:
  // переход на другую вкладку меню размонтирует NewAnalysis, и локальный
  // useState (включая уже загруженные документы и идущий прогон) терялся бы.
  analysisBeforeDocs: BackendDocument[]
  analysisAfterDocs: BackendDocument[]
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
  setSession: (principal: Principal | null, permissions: string[]) => void
  toggleMenu: () => void
  setDensity: (value: 'comfortable' | 'compact') => void
  setFilter: (screen: string, key: string, value: string) => void
  toggleChecked: (id: string) => void
  pushToast: (text: string, kind?: 'ok' | 'error', undo?: () => void) => void
  dropToast: (id: number) => void
  can: (action: string) => boolean
  setAnalysisDocs: (side: 'before' | 'after', updater: DocsUpdater) => void
  setAnalysisPending: (side: 'before' | 'after', updater: PendingUpdater) => void
  setAnalysisRunId: (id: number | null) => void
  setTriangulatedRunId: (id: number | null) => void
  resetAnalysis: () => void
}

export const useApp = create<AppState>()(
  persist(
    (set, get) => ({
      principal: null,
      permissions: [],
      menuCollapsed: false,
      density: 'comfortable',
      filters: {},
      checkedAttention: {},
      toasts: [],
      analysisBeforeDocs: [],
      analysisAfterDocs: [],
      analysisPendingBefore: [],
      analysisPendingAfter: [],
      analysisRunId: null,
      analysisRunStatus: null,
      triangulatedRunId: null,
      triangulatedRunStatus: null,
      setSession: (principal, permissions) => set({ principal, permissions }),
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
      can: (action) => get().permissions.includes(action),
      setAnalysisDocs: (side, updater) =>
        set((s) => {
          const key = side === 'before' ? 'analysisBeforeDocs' : 'analysisAfterDocs'
          const prev = s[key]
          return { [key]: typeof updater === 'function' ? updater(prev) : updater }
        }),
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
      setTriangulatedRunId: (triangulatedRunId) => {
        set({ triangulatedRunId, triangulatedRunStatus: null })
        if (triangulatedRunId != null) pollTriangulatedRun(triangulatedRunId)
      },
      resetAnalysis: () => set({
        analysisBeforeDocs: [], analysisAfterDocs: [],
        analysisPendingBefore: [], analysisPendingAfter: [],
        analysisRunId: null, analysisRunStatus: null,
        triangulatedRunId: null, triangulatedRunStatus: null,
      }),
    }),
    {
      name: 'nadzor.app',
      partialize: (s) => ({
        principal: s.principal,
        permissions: s.permissions,
        menuCollapsed: s.menuCollapsed,
        density: s.density,
        filters: s.filters,
        checkedAttention: s.checkedAttention,
        // Переживает и смену вкладки, и обновление страницы: инспектор не
        // должен терять загруженные документы и результат прогона анализа.
        analysisBeforeDocs: s.analysisBeforeDocs,
        analysisAfterDocs: s.analysisAfterDocs,
        analysisRunId: s.analysisRunId,
        triangulatedRunId: s.triangulatedRunId,
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
      },
    },
  ),
)

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
    } catch {
      pollTimer = setTimeout(tick, 800)
      return
    }
    if (useApp.getState().analysisRunId !== runId) return
    useApp.setState({ analysisRunStatus: data })
    if (data.status !== 'done' && data.status !== 'error') {
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
    if (data.status !== 'done' && data.status !== 'error') {
      triangulatedPollTimer = setTimeout(tick, 800)
    }
  }
  void tick()
}
