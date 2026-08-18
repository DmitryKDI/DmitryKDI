import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../api'
import { useApp } from '../store'
import { ErrorState, Skeleton } from '../components/ui'
import type { Principal } from '../types'

interface AccountsResponse {
  provider: string
  roles: Record<string, string>
  accounts: Principal[]
}

/** Вход выполняется через единую систему идентификации: локальных паролей нет. */
export default function Login() {
  const navigate = useNavigate()
  const { setSession, pushToast } = useApp()
  const accounts = useQuery({
    queryKey: ['accounts'],
    queryFn: () => api.get<AccountsResponse>('/auth/accounts'),
  })

  const login = useMutation({
    mutationFn: (subject: string) =>
      api.post<{ access_token: string; principal: Principal; permissions: string[] }>(
        '/auth/login', { subject }),
    onSuccess: (data) => {
      setToken(data.access_token)
      setSession(data.principal, data.permissions)
      pushToast(`Вход выполнен: ${data.principal.full_name}`)
      navigate('/')
    },
    onError: () => pushToast('Не удалось войти. Проверьте учётную запись.', 'error'),
  })

  return (
    <div className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-6 p-6">
      <div className="flex items-center gap-3">
        <img src="/icon.svg" alt="" className="h-11 w-11" />
        <div>
          <h1 className="text-xl font-semibold">НАДЗОР.ИИ</h1>
          <p className="text-sm text-ink-muted">
            Модуль предиктивного строительного надзора. Комитет государственного
            строительного надзора города Москвы
          </p>
        </div>
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold">Вход через СУДИР</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Единая система управления доступом к информационным ресурсам города.
          В демонстрационном контуре подключён мок: выберите учётную запись, чтобы
          увидеть, как один и тот же объект выглядит для разных ролей.
        </p>

        {accounts.isLoading && <div className="mt-4"><Skeleton rows={5} /></div>}
        {accounts.isError && <ErrorState error={accounts.error} retry={() => accounts.refetch()} />}

        <ul className="mt-4 grid gap-2 sm:grid-cols-2">
          {accounts.data?.accounts.map((account) => (
            <li key={account.subject}>
              <button
                className="w-full rounded-md border border-surface-line p-3 text-left transition-colors
                  hover:border-accent hover:bg-accent-soft disabled:opacity-60"
                disabled={login.isPending}
                onClick={() => login.mutate(account.subject)}>
                <p className="text-sm font-medium">{account.full_name}</p>
                <p className="text-xs text-ink-muted">{account.position}</p>
                <p className="mt-1 text-xs text-ink-faint">{account.department}</p>
                <p className="mt-1 text-[11px] text-accent">
                  {account.roles.map((r) => accounts.data?.roles[r] ?? r).join(', ')}
                </p>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <p className="text-xs text-ink-faint">
        Система формирует гипотезы, а не заключения. Юридически значимые действия
        совершает только должностное лицо.
      </p>
    </div>
  )
}
