import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { Chip, DemoNotice, ErrorState, SectionCard, Skeleton, Table } from '../components/ui'

interface UserItem {
  id: string; full_name: string; department: string; position: string
  roles: string[]; valid_until: string
}

export default function Users() {
  const query = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<{ items: UserItem[]; roles: Record<string, string> }>('/admin/users'),
  })
  if (query.isLoading) return <div className="card p-6"><Skeleton rows={6} /></div>
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />

  return (
    <SectionCard title="Пользователи и роли"
      subtitle="Учётные записи и полномочия приходят из СУДИР. Внутри системы роли только отображаются на внутреннюю модель"
      right={<DemoNotice />}>
      <Table head={['Сотрудник', 'Должность', 'Подразделение', 'Роли', 'Срок действия']}>
        {query.data!.items.map((u) => (
          <tr key={u.id}>
            <td className="td font-medium">{u.full_name}</td>
            <td className="td text-ink-muted">{u.position}</td>
            <td className="td text-ink-muted">{u.department}</td>
            <td className="td">
              <span className="flex flex-wrap gap-1">
                {u.roles.map((r) => <Chip key={r} tone="accent">{query.data!.roles[r] ?? r}</Chip>)}
              </span>
            </td>
            <td className="td whitespace-nowrap text-ink-muted">{u.valid_until}</td>
          </tr>
        ))}
      </Table>
      <p className="mt-3 text-xs text-ink-faint">
        Изменение полномочий выполняется в СУДИР. Отзыв прав закрывает доступ к системе
        при следующем обращении.
      </p>
    </SectionCard>
  )
}
