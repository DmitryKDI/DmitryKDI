import { type ReactNode, useEffect, useState } from 'react'
import { getToken } from '../api'
import { theme } from '../theme'
import { useApp } from '../store'

/** Белая карточка со сворачиваемой секцией — основной строительный блок платформы. */
export function SectionCard({
  title, subtitle, right, children, collapsible = false, defaultOpen = true, className = '',
}: {
  title?: ReactNode; subtitle?: ReactNode; right?: ReactNode; children: ReactNode
  collapsible?: boolean; defaultOpen?: boolean; className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className={`card ${className}`}>
      {title && (
        <header className="flex items-start justify-between gap-3 border-b border-surface-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {right}
            {collapsible && (
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setOpen(!open)}
                aria-expanded={open}>
                {open ? 'Свернуть' : 'Развернуть'}
              </button>
            )}
          </div>
        </header>
      )}
      {open && <div className="p-4">{children}</div>}
    </section>
  )
}

export function SeverityChip({ value }: { value: string }) {
  const s = theme.severity[value] ?? { label: value, cls: 'bg-surface-muted text-ink-muted border-surface-line' }
  return <span className={`chip ${s.cls}`}>{s.label}</span>
}

export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'accent' | 'warn' }) {
  const map = {
    neutral: 'bg-surface-muted text-ink-muted border-surface-line',
    accent: 'bg-accent-soft text-accent border-accent-line',
    warn: 'bg-major-soft text-major border-major/30',
  }
  return <span className={`chip ${map[tone]}`}>{children}</span>
}

/** Уверенность показывается всегда и не округляется до «да/нет». */
export function Confidence({ value }: { value: number }) {
  const percent = Math.round(value * 100)
  return (
    <span className="inline-flex items-center gap-2" title={`Уверенность модели ${percent}%`}>
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-line">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
      </span>
      <span className="text-xs tabular-nums text-ink-muted">{percent}%</span>
    </span>
  )
}

export function Hint({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex align-middle">
      <span className="ml-1 cursor-help text-ink-faint" aria-label={text}>ⓘ</span>
      <span className="pointer-events-none absolute left-1/2 top-6 z-30 hidden w-64 -translate-x-1/2
        rounded-md border border-surface-line bg-surface p-2 text-xs text-ink-muted shadow-card
        group-hover:block group-focus-within:block">{text}</span>
    </span>
  )
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Загрузка">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-surface-muted" style={{ width: `${92 - i * 7}%` }} />
      ))}
    </div>
  )
}

/** Пустое состояние объясняет, что произошло и что делать дальше. */
export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="max-w-md text-sm text-ink-muted">{hint}</p>}
      {action}
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : 'Не удалось загрузить данные.'
  return (
    <Empty title={message}
      hint="Проверьте подключение к сети и повторите. Если повторяется — обратитесь к администратору системы."
      action={retry && <button className="btn-primary mt-2" onClick={retry}>Повторить</button>} />
  )
}

export function DemoNotice({ text = 'Демонстрационные данные' }: { text?: string }) {
  return (
    <span className="chip border-dashed border-ink-faint/40 bg-surface-muted text-ink-faint">
      {text}
    </span>
  )
}

export function StatTile({ label, value, unit, hint, tone = 'default' }: {
  label: string; value: ReactNode; unit?: string; hint?: string
  tone?: 'default' | 'accent' | 'critical'
}) {
  const toneCls = tone === 'accent' ? 'text-accent' : tone === 'critical' ? 'text-critical' : 'text-ink'
  return (
    <div className="card p-4">
      <p className="flex items-center text-xs font-medium uppercase tracking-wide text-ink-faint">
        {label}{hint && <Hint text={hint} />}
      </p>
      <p className={`mt-2 text-3xl font-semibold tabular-nums ${toneCls}`}>
        {value}{unit && <span className="ml-1 text-base font-normal text-ink-muted">{unit}</span>}
      </p>
    </div>
  )
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  const density = useApp((s) => s.density)
  return (
    <div className="overflow-x-auto">
      <table className={`w-full border-collapse ${density === 'compact' ? 'text-xs' : 'text-sm'}`}>
        <thead className="border-b border-surface-line bg-surface-muted/60">
          <tr>{head.map((h, i) => <th key={i} className="th">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-surface-line">{children}</tbody>
      </table>
    </div>
  )
}

export function Toasts() {
  const { toasts, dropToast } = useApp()
  if (!toasts.length) return null
  return (
    <div className="no-print fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <div key={t.id} role="status"
          className={`card flex items-start gap-3 p-3 text-sm ${t.kind === 'error' ? 'border-critical/40' : ''}`}>
          <span className="flex-1">{t.text}</span>
          {t.undo && (
            <button className="text-xs font-medium text-accent"
              onClick={() => { t.undo?.(); dropToast(t.id) }}>Отменить</button>
          )}
          <button className="text-xs text-ink-faint" onClick={() => dropToast(t.id)} aria-label="Закрыть">✕</button>
        </div>
      ))}
    </div>
  )
}

export function Modal({ open, title, onClose, children }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-ink/30 p-4"
      role="dialog" aria-modal="true" onClick={onClose}>
      <div className="card max-h-[85vh] w-full max-w-3xl overflow-auto" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-surface-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button className="btn-ghost px-2 py-1 text-xs" onClick={onClose}>Закрыть</button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  )
}

export function formatDate(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('ru-RU')
}

export function elementLocation(element: Record<string, string>): string {
  return [element.mark, element.axes && `оси ${element.axes}`, element.level && `отм. ${element.level}`,
    element.section].filter((p) => p && p !== '—' && p !== 'оси —' && p !== 'отм. —').join(', ')
}


/**
 * Лист документа. Запрашивается с заголовком авторизации, поэтому обычный тег
 * изображения не подходит: без проверки прав сервер данные не отдаёт.
 */
export function AuthImage({ src, alt, className = '', onSize }: {
  src: string; alt: string; className?: string
  onSize?: (size: { width: number; height: number; clientWidth: number }) => void
}) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let objectUrl = ''
    let cancelled = false
    fetch(src, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('Лист документа недоступен.'))))
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [src])

  if (error) return <p className="rounded border border-surface-line p-4 text-sm text-ink-muted">{error}</p>
  if (!url) return <div className="h-64 animate-pulse rounded border border-surface-line bg-surface-muted" />
  return (
    <img src={url} alt={alt} className={className}
      onLoad={(e) => onSize?.({
        width: e.currentTarget.naturalWidth,
        height: e.currentTarget.naturalHeight,
        clientWidth: e.currentTarget.clientWidth,
      })} />
  )
}
