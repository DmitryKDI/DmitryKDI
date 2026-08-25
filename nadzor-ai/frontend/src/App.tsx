import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { getToken } from './api'
import { useApp } from './store'
import Shell from './layout/Shell'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import NewAnalysis from './pages/NewAnalysis'
import AnalysisLog from './pages/AnalysisLog'
import DiscrepancyLog from './pages/DiscrepancyLog'
import HypothesisLog from './pages/HypothesisLog'
import HypothesisCard from './pages/HypothesisCard'
import AttentionMap from './pages/AttentionMap'
import { ObjectCard, ObjectsList } from './pages/Objects'
import AuditLog from './pages/AuditLog'
import Admin from './pages/Admin'
import Integrations from './pages/Integrations'
import Classifier from './pages/Classifier'
import Users from './pages/Users'
import ContractorAnalytics from './pages/ContractorAnalytics'
import Workload from './pages/Workload'

function Protected({ children }: { children: React.ReactNode }) {
  const principal = useApp((s) => s.principal)
  const location = useLocation()
  if (!getToken() || !principal) return <Navigate to="/login" state={{ from: location }} replace />
  // Локальная сессия умеет только «Новый анализ» — остальные экраны требуют
  // packages/api, которого при этом входе нет и не будет. Не пускаем на них
  // даже по прямой ссылке/истории браузера, а не только прячем в меню.
  if (principal.source === 'local' && location.pathname !== '/analysis/new') {
    return <Navigate to="/analysis/new" replace />
  }
  return <Shell>{children}</Shell>
}

export default function App() {
  const location = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [location.pathname])

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Protected><Dashboard /></Protected>} />
      <Route path="/analysis/new" element={<Protected><NewAnalysis /></Protected>} />
      <Route path="/analysis" element={<Protected><AnalysisLog /></Protected>} />
      <Route path="/discrepancies" element={<Protected><DiscrepancyLog /></Protected>} />
      <Route path="/findings" element={<Protected><HypothesisLog /></Protected>} />
      <Route path="/findings/:id" element={<Protected><HypothesisCard /></Protected>} />
      <Route path="/attention" element={<Protected><AttentionMap /></Protected>} />
      <Route path="/objects" element={<Protected><ObjectsList /></Protected>} />
      <Route path="/objects/:id" element={<Protected><ObjectCard /></Protected>} />
      <Route path="/audit" element={<Protected><AuditLog /></Protected>} />
      <Route path="/admin" element={<Protected><Admin /></Protected>} />
      <Route path="/admin/integrations" element={<Protected><Integrations /></Protected>} />
      <Route path="/admin/users" element={<Protected><Users /></Protected>} />
      <Route path="/classifier" element={<Protected><Classifier /></Protected>} />
      <Route path="/analytics" element={<Protected><ContractorAnalytics /></Protected>} />
      <Route path="/workload" element={<Protected><Workload /></Protected>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
