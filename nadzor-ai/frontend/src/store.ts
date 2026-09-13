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
  analysisPendingBefore: PendingUpload[]
  analysisPendingAfter: PendingUpload[]
  analysisRunId: number | null
  analysisRunStatus: BackendAnalysisRun | null
  triangulatedRunId: number | null
  triangulatedRunStatus: BackendTriangulatedRun | null
  pdRunId: number | null
  pdRunStatus: BackendPdRun | null
  rdRunId: number | null
  rdRunStatus: BackendPdRun | null
  complianceRunId: number | null
  complianceRunStatus: BackendComplianceRun | null
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
        analysisRunId: s.analysisRunId,
        triangulatedRunId: s.triangulatedRunId,
        pdRunId: s.pdRunId,
        rdRunId: s.rdRunId,
        complianceRunId: s.complianceRunId,
        pdSelected: s.pdSelected,
        rdSelected: s.rdSelected,
      }),
      onRehydrateStorage: () => (state) => {
        if (state?.analysisRunId != null) {
          const id = state.analysisRunId
          setTimeout(() => pollAnalysisRun(id), 0)
        }
        if (state?.triangulatedRunId != null) {
          const id = state.triangulatedRunId
          setTimeout(() => pollTriangulatedRun(id), 0)
        }
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

/** A 4xx means the persisted server-side run no longer exists or is no longer
 * retrievable. Retrying forever only spams the backend after workspace reset. */
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
    if (useApp.getState().analysisRunId !== runId) return
    let data: BackendAnalysisRun
    try {
      data = await backendApi.getAnalysisRun(runId)
    } catch (error) {
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
    } catch (error) {
      if (isGone(error)) {
        useApp.setState({ triangulatedRunId: null, triangulatedRunStatus: null })
        return
      }
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
