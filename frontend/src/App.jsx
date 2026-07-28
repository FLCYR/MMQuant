import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Backtest from './pages/Backtest'
import Factors from './pages/Factors'
import DataOverview from './pages/DataOverview'

const NAV = [
  { to: '/backtest', label: '回测分析' },
  { to: '/factors', label: '因子分析' },
  { to: '/data', label: '数据概览' },
]

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>A 股量化系统</h1>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? 'active' : '')}>
            {n.label}
          </NavLink>
        ))}
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/backtest" replace />} />
          <Route path="/backtest" element={<Backtest />} />
          <Route path="/factors" element={<Factors />} />
          <Route path="/data" element={<DataOverview />} />
        </Routes>
      </main>
    </div>
  )
}
