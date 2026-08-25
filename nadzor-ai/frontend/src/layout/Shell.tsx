import { useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, setToken } from '../api'
import { useApp } from '../store'
import { Toasts } from '../components/ui'
import type { Finding, ObjectBrief } from '../types'

interface MenuItem { to: string; label: string; permission?: string }
interface MenuGroup { label: string; items: MenuItem[] }

const MENU: MenuGroup[] = [
  {
    label: 'Предиктивный надзор',
    items: [
      { to: '/', label: 'Дашборд' },
      { to: '/analysis/new', label: 'Новый анализ', permission: 'analysis:run' },
      { to: '/analysis', label: 'Журнал анализа документации' },
      { to: '/discrepancies', label: 'Журнал расхождений' },
      { to: '/findings', label: 'Журнал гипотез нарушений' },
      { to: '/attention', label: 'Карта внимания' },
      { to: '/workload', label: 'Нагрузка инспекторов', permission: 'analytics:read' },
    ],
  },
  {
    label: 'Объекты и справочники',
    items: [
      { to: '/objects', label: 'Объекты надзора' },
      { to: '/classifier', label: 'Классификатор расхождений' },
      { to: '/analytics', label: 'Аналитика по подрядчикам', permission: 'analytics:read' },
    ],
  },
  {
    label: 'Администрирование',
    items: [
      { to: '/admin', label: 'Настройки моделей и правил', permission: 'integrations:read' },
      { to: '/admin/integrations', label: 'Внешние интеграции', permission: 'integrations:read' },
      { to: '/admin/users', label: 'Пользователи и роли', permission: 'integrations:read' },
      { to: '/audit', label: 'Журнал аудита', permission: 'audit:read' },
    ],
  },
]

const TITLES: Record<string, string> = {
  '/': 'Дашборд', '/analysis/new': 'Новый анализ', '/analysis': 'Журнал анализа документации',
  '/discrepancies': 'Журнал расхождений', '/findings': 'Журнал гипотез нарушений',
  '/attention': 'Карта внимания', '/workload': 'Нагрузка инспекторов', '/objects': 'Объекты надзора',
  '/classifier': 'Классификатор расхождений', '/analytics': 'Аналитика по подрядчикам',
  '/admin': 'Настройки моделей и правил', '/admin/integrations': 'Внешние интеграции',
  '/admin/users': 'Пользователи и роли', '/audit': 'Журнал аудита',
}

export default function Shell({ children }: { children: ReactNode }) {
  const { principal, menuCollapsed, toggleMenu, can, setSession } = useApp()
  const location = useLocation()
  const navigate = useNavigate()
  const [searchOpen, setSearchOpen] = useState(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      }
      if (e.key === 'Escape') setSearchOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const logout = async () => {
    await api.post('/auth/logout').catch(() => undefined)
    setToken('')
    setSession(null, [])
    navigate('/login')
  }

  return (
    <div className="flex min-h-screen bg-surface-muted">
      <aside className={`no-print sticky top-0 hidden h-screen shrink-0 border-r border-surface-line
        bg-surface transition-all md:block ${menuCollapsed ? 'w-14' : 'w-72'}`}>
        <div className="flex h-14 items-center gap-2 border-b border-surface-line px-3">
          <img src="/icon.svg" alt="" className="h-7 w-7" />
          {!menuCollapsed && <span className="truncate text-sm font-semibold">НАДЗОР.ИИ</span>}
          <button className="ml-auto rounded p-1 text-ink-faint hover:bg-surface-muted"
            onClick={toggleMenu} aria-label="Свернуть меню">{menuCollapsed ? '»' : '«'}</button>
        </div>
        <nav className="space-y-4 overflow-y-auto p-2" style={{ height: 'calc(100vh - 3.5rem)' }}>
          {MENU.map((group) => {
            const items = group.items.filter((i) => !i.permission || can(i.permission))
            if (!items.length) return null
            return (
              <div key={group.label}>
                {!menuCollapsed && (
                  <p className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
                    {group.label}
                  </p>
                )}
                <ul className="space-y-0.5">
                  {items.map((item) => (
                    <li key={item.to}>
                      <NavLink to={item.to} end={item.to === '/'} title={item.label}
                        className={({ isActive }) => `block truncate rounded-md px-2 py-1.5 text-sm
                          ${isActive ? 'bg-accent-soft font-medium text-accent' : 'text-ink-muted hover:bg-surface-muted'}`}>
                        {menuCollapsed ? item.label.slice(0, 1) : item.label}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print sticky top-0 z-20 flex h-14 items-center gap-3 border-b
          border-surface-line bg-surface px-4">
          <button className="btn-ghost px-2 py-1 text-xs md:hidden" onClick={toggleMenu}>Меню</button>
          <button className="hidden w-96 items-center gap-2 rounded-md border border-surface-line
            px-3 py-1.5 text-left text-sm text-ink-faint hover:bg-surface-muted sm:flex"
            onClick={() => setSearchOpen(true)}>
            <span className="truncate">Поиск по объектам, документам и гипотезам</span>
            <kbd className="ml-auto shrink-0 rounded border border-surface-line px-1 text-[10px]">Ctrl K</kbd>
          </button>
          <div className="ml-auto flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-xs font-medium leading-tight">{principal?.full_name}</p>
              <p className="text-[11px] leading-tight text-ink-faint">{principal?.position}</p>
            </div>
            <Link to="/login" className="btn-ghost px-2 py-1 text-xs">Сменить роль</Link>
            <button className="btn-ghost px-2 py-1 text-xs" onClick={logout}>Выход</button>
          </div>
        </header>

        <div className="no-print flex items-center gap-2 px-4 pt-3 text-xs text-ink-faint">
          <Link to="/" className="hover:text-accent">Главная</Link>
          <span>/</span>
          <span className="text-ink-muted">{TITLES[location.pathname] ?? 'Раздел'}</span>
        </div>

        <main className="min-w-0 flex-1 p-4">{children}</main>
      </div>

      {searchOpen && <GlobalSearch onClose={() => setSearchOpen(false)} />}
      <Toasts />
    </div>
  )
}

function GlobalSearch({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const objects = useQuery({
    queryKey: ['objects'],
    queryFn: () => api.get<{ items: ObjectBrief[] }>('/objects'),
  })
  const findings = useQuery({
    queryKey: ['findings', 'all'],
    queryFn: () => api.get<{ items: Finding[] }>('/findings'),
  })

  const q = query.trim().toLowerCase()
  const objectHits = (objects.data?.items ?? []).filter(
    (o) => !q || o.name.toLowerCase().includes(q) || o.address.toLowerCase().includes(q) ||
      o.permit_number.includes(q)).slice(0, 5)
  const findingHits = (findings.data?.items ?? []).filter(
    (f) => !q || f.title.toLowerCase().includes(q) || f.public_id.toLowerCase().includes(q) ||
      f.code.toLowerCase().includes(q)).slice(0, 6)

  const go = (path: string) => { navigate(path); onClose() }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-ink/30 p-4 pt-24"
      role="dialog" aria-modal="true" onClick={onClose}>
      <div className="card w-full max-w-2xl" onClick={(e) => e.stopPropagation()}>
        <input autoFocus className="input rounded-b-none border-0 border-b text-base"
          placeholder="Объект, номер разрешения, гипотеза или код расхождения"
          value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="max-h-96 overflow-auto p-2">
          {!objectHits.length && !findingHits.length && (
            <p className="p-4 text-sm text-ink-muted">Ничего не найдено. Уточните запрос.</p>
          )}
          {objectHits.length > 0 && (
            <p className="px-2 py-1 text-[11px] uppercase text-ink-faint">Объекты</p>
          )}
          {objectHits.map((o) => (
            <button key={o.id} className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-surface-muted"
              onClick={() => go(`/objects/${o.id}`)}>
              <span className="font-medium">{o.name}</span>
              <span className="block text-xs text-ink-faint">{o.address}</span>
            </button>
          ))}
          {findingHits.length > 0 && (
            <p className="px-2 pb-1 pt-2 text-[11px] uppercase text-ink-faint">Гипотезы</p>
          )}
          {findingHits.map((f) => (
            <button key={f.id} className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-surface-muted"
              onClick={() => go(`/findings/${f.id}`)}>
              <span className="font-medium">{f.public_id} · {f.code}</span>
              <span className="block truncate text-xs text-ink-faint">{f.title}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
