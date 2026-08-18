import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { useApp } from '../store'
import { Chip, ErrorState, Hint, SectionCard, Skeleton, Table } from '../components/ui'

interface ProvidersResponse {
  default: string
  verifier: string
  policy: Record<string, unknown>
  providers: { name: string; model: string; enabled: boolean; is_sovereign: boolean
    is_default: boolean; is_verifier: boolean; max_context_tokens: number }[]
}

interface RulesResponse {
  rules: Record<string, { enabled: boolean; severity?: string; description: string }>
  llm_layer: Record<string, unknown>
  scoring: { weights: Record<string, number>; structural_criticality: Record<string, number> }
}

export default function Admin() {
  const queryClient = useQueryClient()
  const { can, pushToast } = useApp()
  const providers = useQuery({ queryKey: ['providers'], queryFn: () => api.get<ProvidersResponse>('/admin/providers') })
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => api.get<RulesResponse>('/admin/rules') })

  const setProvider = useMutation({
    mutationFn: (name: string) => api.post('/admin/providers/default', { name }),
    onSuccess: (_d, name) => {
      pushToast(`Провайдер переключён на ${name}. Конфигурация сохранена, пересборка не требуется.`)
      queryClient.invalidateQueries({ queryKey: ['providers'] })
    },
    onError: (e) => pushToast(e instanceof Error ? e.message : 'Не удалось переключить провайдера', 'error'),
  })
  const toggleRule = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.post(`/admin/rules/${name}`, { enabled }),
    onSuccess: () => {
      pushToast('Настройка правила сохранена')
      queryClient.invalidateQueries({ queryKey: ['rules'] })
    },
  })

  return (
    <div className="space-y-4">
      <SectionCard title="Провайдеры языковых моделей"
        subtitle="Переключение выполняется правкой конфигурации и не требует пересборки приложения">
        {providers.isLoading && <Skeleton rows={5} />}
        {providers.isError && <ErrorState error={providers.error} retry={() => providers.refetch()} />}
        {providers.data && (
          <Table head={['Провайдер', 'Модель', 'Размещение', 'Контекст', 'Состояние', '']}>
            {providers.data.providers.map((p) => (
              <tr key={p.name} className={p.is_default ? 'bg-accent-soft/40' : ''}>
                <td className="td font-medium">{p.name}</td>
                <td className="td text-ink-muted">{p.model}</td>
                <td className="td">
                  <Chip tone={p.is_sovereign ? 'accent' : 'warn'}>
                    {p.is_sovereign ? 'российский контур' : 'зарубежный, только разработка'}
                  </Chip>
                </td>
                <td className="td tabular-nums text-ink-muted">
                  {p.max_context_tokens.toLocaleString('ru-RU')}
                </td>
                <td className="td">
                  <span className="flex flex-wrap gap-1">
                    {p.is_default && <Chip tone="accent">основной</Chip>}
                    {p.is_verifier && <Chip>перепроверка</Chip>}
                    {!p.enabled && <Chip>отключён</Chip>}
                  </span>
                </td>
                <td className="td">
                  {can('admin:write') && !p.is_default && (
                    <button className="btn-ghost px-2 py-1 text-xs" disabled={setProvider.isPending}
                      onClick={() => setProvider.mutate(p.name)}>Сделать основным</button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
        <p className="mt-3 text-xs text-ink-faint">
          Для объектов с повышенными требованиями к защите данных допускаются только провайдеры,
          размещённые в российском контуре. Ключи доступа задаются переменными окружения и в
          интерфейсе не отображаются.
        </p>
      </SectionCard>

      <div className="grid gap-4 lg:grid-cols-2">
        <SectionCard title="Правила детектора"
          subtitle="Детерминированный слой не подвержен внушению через содержимое документа">
          {rules.isLoading && <Skeleton rows={6} />}
          {rules.data && (
            <ul className="space-y-2">
              {Object.entries(rules.data.rules).map(([name, rule]) => (
                <li key={name} className="flex items-start gap-3 rounded-md border border-surface-line p-3">
                  <input type="checkbox" className="mt-1" checked={rule.enabled}
                    disabled={!can('admin:write') || toggleRule.isPending}
                    onChange={(e) => toggleRule.mutate({ name, enabled: e.target.checked })} />
                  <span className="min-w-0 flex-1">
                    <span className="text-sm font-medium">{rule.description}</span>
                    <span className="block font-mono text-xs text-ink-faint">{name}</span>
                  </span>
                  {rule.severity && <Chip>{rule.severity}</Chip>}
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard title="Веса приоритизации"
          subtitle="Итоговый балл гипотезы, 0–100"
          right={<Hint text="Балл отвечает на вопрос «куда идти в первую очередь», а не «насколько модель уверена»." />}>
          {rules.data && (
            <>
              <ul className="space-y-2">
                {Object.entries(rules.data.scoring.weights).map(([key, value]) => (
                  <li key={key} className="flex items-center gap-3 text-sm">
                    <span className="w-52 text-ink-muted">{WEIGHT_LABELS[key] ?? key}</span>
                    <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface-muted">
                      <span className="block h-full rounded-full bg-accent" style={{ width: `${value * 100}%` }} />
                    </span>
                    <span className="w-10 text-right tabular-nums">{value}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-ink-faint">
                Критичность конструктива задаётся в config/scoring.yaml: несущие конструкции
                весомее отделочных.
              </p>
            </>
          )}
        </SectionCard>
      </div>
    </div>
  )
}

const WEIGHT_LABELS: Record<string, string> = {
  severity: 'Тяжесть расхождения',
  confidence: 'Уверенность вывода',
  structural_criticality: 'Критичность конструктива',
  legal_weight: 'Тяжесть по КоАП',
  readiness_stage: 'Стадия готовности',
  contractor_history: 'История подрядчика',
}
