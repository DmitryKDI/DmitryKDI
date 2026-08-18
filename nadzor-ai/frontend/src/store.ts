import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Principal } from './types'

interface Toast {
  id: number
  text: string
  kind: 'ok' | 'error'
  undo?: () => void
}

interface AppState {
  principal: Principal | null
  permissions: string[]
  menuCollapsed: boolean
  density: 'comfortable' | 'compact'
  filters: Record<string, Record<string, string>>
  checkedAttention: Record<string, boolean>
  toasts: Toast[]
  setSession: (principal: Principal | null, permissions: string[]) => void
  toggleMenu: () => void
  setDensity: (value: 'comfortable' | 'compact') => void
  setFilter: (screen: string, key: string, value: string) => void
  toggleChecked: (id: string) => void
  pushToast: (text: string, kind?: 'ok' | 'error', undo?: () => void) => void
  dropToast: (id: number) => void
  can: (action: string) => boolean
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
      }),
    },
  ),
)
