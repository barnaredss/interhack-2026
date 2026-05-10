import { useAuth } from './context/AuthContext'
import Login from './components/Login'
import Dashboard from './components/Dashboard'
import AdminDashboard from './components/AdminDashboard'

export default function App() {
  const { user, driverId, loading } = useAuth()

  if (loading) return <div className="loading-screen"><div className="spinner" /></div>
  if (!user) return <Login />
  if (driverId === 'admin') return <AdminDashboard />
  return <Dashboard />
}
