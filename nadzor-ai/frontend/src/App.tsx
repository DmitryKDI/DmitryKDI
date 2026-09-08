import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Shell from './layout/Shell'
import NewAnalysis from './pages/NewAnalysis'
import AttentionMap from './pages/AttentionMap'
import ParsedDocs from './pages/ParsedDocs'

// Г.85 — старый бэкенд (`packages/api`, порт 8000) удалён по прямому решению
// пользователя, вместе с ним ушли 14 экранов CRM-витрины и авторизация:
// логин, роли и права жили только там. Реальный движок (`packages/backend`,
// порт 8010) аутентификации не требует вообще, поэтому обёртки `Protected`
// больше нет — не заглушка, а следствие удаления единственного источника
// сессии. Остались два экрана, которые реально работают на живом движке.
export default function App() {
  const location = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [location.pathname])

  return (
    <Shell>
      <Routes>
        <Route path="/" element={<NewAnalysis />} />
        <Route path="/parsed" element={<ParsedDocs />} />
        <Route path="/attention" element={<AttentionMap />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}
