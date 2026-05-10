import React, { useEffect, useRef, useState } from 'react'
import { Map, AdvancedMarker, useMap } from '@vis.gl/react-google-maps'
import { useAuth } from '../context/AuthContext'
import { subscribeToRoutes } from '../firebase'
import RouteRenderer from './RouteRenderer'
import AdminRouteCreator from './AdminRouteCreator'

const COLORS = ['#C41230', '#2563EB', '#059669', '#D97706', '#7C3AED']


function BoundsFitter({ routes }) {
  const map = useMap()
  const routeKey = routes.map((r) => r.driver_id).join(',')

  useEffect(() => {
    if (!map || !window.google || !routes.length) return
    const bounds = new window.google.maps.LatLngBounds()
    routes.forEach((r) => (r.points ?? []).forEach((p) => bounds.extend({ lat: p.lat, lng: p.lng })))
    if (!bounds.isEmpty()) map.fitBounds(bounds, 60)
  }, [map, routeKey]) // eslint-disable-line

  return null
}

function FleetLegend({ routes, selectedId, onSelect }) {
  return (
    <div className="fleet-legend">
      <div className="fleet-legend-header">
        <div className="fleet-legend-title">Fleet</div>
        {selectedId && (
          <button className="fleet-show-all" onClick={() => onSelect(null)}>
            All trucks
          </button>
        )}
      </div>
      {routes.length === 0 && (
        <div className="fleet-item" style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>Loading…</div>
      )}
      {routes.map((route, i) => {
        const active = selectedId === route.driver_id
        return (
          <div
            className={`fleet-item clickable${active ? ' selected' : ''}`}
            key={route.driver_id}
            onClick={() => onSelect(active ? null : route.driver_id)}
          >
            <span className="fleet-color" style={{ background: COLORS[i % COLORS.length] }} />
            <div className="fleet-info">
              <div className="fleet-driver">{route.driver_id}</div>
              <div className="fleet-truck">{route.truck_id}</div>
            </div>
            <span className={`fleet-gps ${route.location ? 'active' : ''}`}>
              {route.location ? 'LIVE' : '···'}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function AdminMap({ routes }) {
  return (
    <Map
      mapId="DEMO_MAP_ID"
      defaultCenter={{ lat: 41.3951, lng: 2.1734 }}
      defaultZoom={12}
      style={{ width: '100%', height: '100%' }}
      gestureHandling="greedy"
    >
      <BoundsFitter routes={routes} />
      {routes.map((route) => (
        <React.Fragment key={route.driver_id}>
          <RouteRenderer points={route.points ?? []} deliveryStatus={route.delivery_status} activeColor={route.status === 'disrupted' ? '#F59E0B' : route.originalColor} />
        </React.Fragment>
      ))}

      {routes.map((route) =>
        (route.points ?? []).map((point, i) => (
          <AdvancedMarker
            key={`${route.driver_id}-${i}`}
            position={{ lat: point.lat, lng: point.lng }}
            title={`${route.driver_id}: ${point.address}`}
          >
            <div
              className="admin-stop-pin"
              style={{ borderColor: route.originalColor, color: route.originalColor }}
            >
              {i + 1}
            </div>
          </AdvancedMarker>
        ))
      )}

      {routes.map((route) =>
        route.location ? (
          <AdvancedMarker
            key={`live-${route.driver_id}`}
            position={route.location}
            title={`${route.driver_id} — live`}
          >
            <div className="truck-live-marker">
              <div className="tlm-pulse" />
              <div className="tlm-icon">🚛</div>
            </div>
          </AdvancedMarker>
        ) : null
      )}
    </Map>
  )
}

export default function AdminDashboard() {
  const { logout } = useAuth()
  const [routes, setRoutes] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [view, setView] = useState('fleet') // 'fleet' | 'create'

  useEffect(() => {
    return subscribeToRoutes(
      setRoutes,
      (err) => console.error('Fleet subscription error:', err)
    )
  }, [])

  if (view === 'create') {
    return <AdminRouteCreator onBack={() => setView('fleet')} />
  }

  const routesWithColors = routes.map((r, i) => ({ ...r, originalColor: COLORS[i % COLORS.length] }))
  const visibleRoutes = selectedId
    ? routesWithColors.filter((r) => r.driver_id === selectedId)
    : routesWithColors

  return (
    <div className="dashboard">
      <nav className="navbar">
        <div className="navbar-brand">
          <span className="star">★</span>
          Damm Motion
          <span className="admin-badge">Admin</span>
        </div>
        <div className="navbar-driver">
          {selectedId
            ? <>Viewing: <span>{selectedId}</span></>
            : <>Fleet: <span>{routes.length} truck{routes.length !== 1 ? 's' : ''}</span></>
          }
        </div>
        <div className="rc-nav-actions">
          <button className="btn-create-route" onClick={() => setView('create')}>+ Create route</button>
          <button className="btn-logout" onClick={logout}>Log out</button>
        </div>
      </nav>
      <div className="dashboard-body">
        <main className="map-container">
          <AdminMap routes={visibleRoutes} />
          <FleetLegend routes={routes} selectedId={selectedId} onSelect={setSelectedId} />
        </main>
      </div>
    </div>
  )
}
