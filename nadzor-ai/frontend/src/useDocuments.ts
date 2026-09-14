import { useQuery } from '@tanstack/react-query'
import { backendApi, type BackendDocument } from './backendApi'

export const DOCUMENTS_KEY = ['backend-documents']

/**
 * Список загруженных документов — С СЕРВЕРА, а не из памяти браузера (Г.114).
 *
 * Раньше он лежал в localStorage и переживал что угодно: пересборку базы,
 * удаление файла в другой вкладке, чужой прогон. После обновления страницы
 * инспектор видел документы, которых на сервере уже нет, а ручная правка
 * раздела не отображалась вовсе — правка уходила на сервер, а список в
 * браузере оставался прежним, и выглядело это как «не нажимается».
 *
 * Пока хоть один том разбирается, список перечитывается: разбор идёт в
 * фоне, и его окончание должно быть видно само, без обновления страницы.
 * Прогоны при этом не сбрасываются — они живут на сервере, и в браузере
 * хранится только их номер.
 */
export function useDocuments() {
  const query = useQuery({
    queryKey: DOCUMENTS_KEY,
    queryFn: backendApi.listDocuments,
    refetchInterval: (q) =>
      (q.state.data ?? []).some((d: BackendDocument) => d.status === 'parsing') ? 2000 : false,
  })
  const docs = query.data ?? []
  return {
    docs,
    before: docs.filter((d) => d.side === 'before'),
    after: docs.filter((d) => d.side === 'after'),
    isLoading: query.isLoading,
    error: query.error,
  }
}
