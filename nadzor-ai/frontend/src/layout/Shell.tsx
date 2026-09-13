import { type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useApp } from '../store'
import { Toasts } from '../components/ui'

const MENU = [
  { to: '/', label: 'Обзор', icon: '⌂' },
  { to: '/documents', label: 'Документы', icon: '▤' },
  { to: '/attention', label: 'Точки контроля', icon: '◎' },
  { to: '/evidence', label: 'Доказательства', icon: '◫' },
  { to: '/reports', label: 'Отчёты', icon: '▧' },
  { to: '/history', label: 'История', icon: '↺' },
]

const TITLES: Record<string, string> = {
  '/': 'Обзор объекта',
  '/documents': 'Документы и анализ',
  '/attention': 'Точки контроля',
  '/evidence': 'Доказательства',
  '/reports': 'Отчёты',
  '/history': 'История анализа',
  '/parsed': 'Разобранная документация',
}

export default function Shell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { menuCollapsed, toggleMenu } = useApp()
  const title = TITLES[location.pathname] ?? 'НАДЗОР.ИИ'

  return (
    <div className="flex min-h-screen bg-surface-muted">
      <aside
        className={`no-print sticky top-0 hidden h-screen shrink-0 border-r border-surface-line bg-surface transition-[width] duration-200 md:block ${
          menuCollapsed ? 'w-[72px]' : 'w-[244px]'
        }`}
      >
        <div className="flex h-[72px] items-center gap-3 border-b border-surface-line px-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-sm font-bold text-white shadow-sm">Н</div>
          {!menuCollapsed && (
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold tracking-wide text-ink">НАДЗОР.ИИ</div>
              <div className="truncate text-[11px] text-ink-faint">строительный контроль</div>
            </div>
          )}
          <button
            className="ml-auto rounded-md p-1.5 text-ink-faint hover:bg-surface-muted hover:text-ink"
            onClick={toggleMenu}
            aria-label={menuCollapsed ? 'Развернуть меню' : 'Свернуть меню'}
          >
            {menuCollapsed ? '›' : '‹'}
          </button>
        </div>

        <nav className="p-3">
          <ul className="space-y-1">
            {MENU.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === '/'}
                  className={({ isActive }) =>
                    `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${
                      isActive
                        ? 'bg-accent-soft font-medium text-accent'
                        : 'text-ink-muted hover:bg-surface-muted hover:text-ink'
                    }`
                  }
                  title={item.label}
                >
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center text-base">{item.icon}</span>
                  {!menuCollapsed && <span className="truncate">{item.label}</span>}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        {!menuCollapsed && (
          <div className="absolute bottom-5 left-3 right-3 rounded-xl border border-surface-line bg-surface-muted/60 p-3">
            <div className="text-xs font-medium text-ink">Режим инспектора</div>
            <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">
              Технические настройки ИИ скрыты. На экране остаются только документы, риски и доказательства.
            </p>
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print sticky top-0 z-20 flex h-[72px] items-center gap-4 border-b border-surface-line bg-surface/95 px-4 backdrop-blur md:px-6">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-base font-semibold text-ink">{title}</h1>
            <p className="truncate text-xs text-ink-faint">Предиктивный строительный надзор · рабочее место инспектора</p>
          </div>
          <div className="hidden items-center gap-2 lg:flex">
            <div className="rounded-lg border border-surface-line bg-surface-muted/50 px-3 py-2 text-xs text-ink-muted">
              Выводы ИИ требуют подтверждения инспектором
            </div>
          </div>
        </header>

        <main className="min-w-0 flex-1 p-4 md:p-6">{children}</main>
      </div>

      <Toasts />
    </div>
  )
}
