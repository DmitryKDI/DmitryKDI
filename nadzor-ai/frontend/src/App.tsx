import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Shell from './layout/Shell'
import Dashboard from './pages/Dashboard'
import Workspace from './pages/Workspace'
import AttentionMap from './pages/AttentionMap'
import Evidence from './pages/Evidence'
import Reports from './pages/Reports'
import History from './pages/History'
import ParsedDocs from './pages/ParsedDocs'

export default function App() {
  const location = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [location.pathname])

  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/documents" element={<Workspace />} />
        <Route path="/analysis/new" element={<Navigate to="/documents" replace />} />
        <Route path="/attention" element={<AttentionMap />} />
        <Route path="/evidence" element={<Evidence />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/history" element={<History />} />
        <Route path="/parsed" element={<ParsedDocs />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}
