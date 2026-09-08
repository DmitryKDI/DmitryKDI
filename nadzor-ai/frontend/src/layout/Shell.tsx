import { type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useApp } from '../store'
import { Toasts } from '../components/ui'

// Г.85 — оболочка ужата до двух реальных экранов вместе с удалением старого
// бэкенда. Убраны: трёхуровневое меню CRM (14 экранов), права доступа
// (`permission` у пункта меню), выход из системы и поисковая строка по
// объектам — всё это опиралось на `packages/api`, которого больше нет.
// Осталась навигация и тосты: то, что реально обслуживает живой движок.
const MENU: { to: string; label: string }[] = [
  { to: '/', label: 'Новый анализ' },
  { to: '/parsed', label: 'Разобранная документация' },
  { to: '/attention', label: 'Карта внимания' },
]

const TITLES: Record<string, string> = {
  '/': 'Новый анализ',
  '/parsed': 'Разобранная документация',
  '/attention': 'Карта внимания',
}

export default function Shell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { menuCollapsed, toggleMenu } = useApp()
  const title = TITLES[location.pathname] ?? 'НАДЗОР.ИИ'

  return (
    <div className="flex min-h-screen bg-surface-muted">
      <aside
        className={`no-print sticky top-0 hidden h-screen shrink-0 border-r border-surface-line bg-surface md:block ${
          menuCollapsed ? 'w-14' : 'w-64'
        }`}
      >
        <div className="flex h-14 items-center gap-2 border-b border-surface-line px-3">
          <img src="/icon.svg" alt="" className="h-7 w-7" />
          {!menuCollapsed && <span className="truncate text-sm font-semibold">НАДЗОР.ИИ</span>}
          <button
            className="ml-auto rounded p-1 text-ink-faint hover:bg-surface-muted"
            onClick={toggleMenu}
            aria-label={menuCollapsed ? 'Развернуть меню' : 'Свернуть меню'}
          >
            {menuCollapsed ? '›' : '‹'}
          </button>
        </div>
        <nav className="space-y-1 overflow-y-auto p-2" style={{ height: 'calc(100vh - 3.5rem)' }}>
          <ul className="space-y-0.5">
            {MENU.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end
                  className={({ isActive }) =>
                    `block truncate rounded-md px-2 py-1.5 text-sm ${
                      isActive ? 'bg-surface-muted font-medium' : 'text-ink-muted hover:bg-surface-muted'
                    }`
                  }
                  title={item.label}
                >
                  {menuCollapsed ? item.label.slice(0, 1) : item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-surface-line bg-surface px-4">
          <button className="btn-ghost px-2 py-1 text-xs md:hidden" onClick={toggleMenu}>
            Меню
          </button>
          <h1 className="truncate text-sm font-semibold">{title}</h1>
        </header>
        <main className="min-w-0 flex-1 p-4">{children}</main>
      </div>

      <Toasts />
    </div>
  )
}
