import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Shell from './layout/Shell'
import OfficialAnalysis from './pages/OfficialAnalysis'

export default function App() {
  const location = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [location.pathname])

  return (
    <Shell>
      <Routes>
        <Route path="/" element={<OfficialAnalysis />} />
        <Route path="/documents" element={<Navigate to="/" replace />} />
        <Route path="/analysis/new" element={<Navigate to="/" replace />} />
        <Route path="/parsed" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}
