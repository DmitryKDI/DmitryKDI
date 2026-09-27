import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/** Состояние оболочки интерфейса. Данные проверки живут на сервере и
 *  запрашиваются экраном напрямую: в браузере хранится только вид. */
interface AppState {
  menuCollapsed: boolean
  toggleMenu: () => void
}

export const useApp = create<AppState>()(
  persist(
    (set) => ({
      menuCollapsed: false,
      toggleMenu: () => set((s) => ({ menuCollapsed: !s.menuCollapsed })),
    }),
    { name: 'nadzor.app', version: 2 },
  ),
)
