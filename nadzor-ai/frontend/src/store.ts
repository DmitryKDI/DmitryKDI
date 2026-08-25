import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Principal } from './types'
import type { BackendDocument } from './backendApi'

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
      setAnalysisRunId: (analysisRunId) => set({ analysisRunId }),
      resetAnalysis: () => set({
        analysisBeforeDocs: [], analysisAfterDocs: [],
        analysisPendingBefore: [], analysisPendingAfter: [], analysisRunId: null,
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
      }),
    },
  ),
)
