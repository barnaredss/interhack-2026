import React, { useEffect, useMemo, useState } from 'react'
import { Map, AdvancedMarker, useMap } from '@vis.gl/react-google-maps'
import { useAuth } from '../context/AuthContext'
import { subscribeToRoutes } from '../firebase'
import {
  API_BASE,
  DEFAULT_DEPOT,
  DEFAULT_FLEET,
  DEFAULT_SHIFT,
  DEFAULT_WINDOW,
  MAP_DEFAULT_CENTER,
  MAP_DEFAULT_ZOOM,
  MAP_ID,
} from '../config'

function MapClickHandler({ onClick }) {
  const map = useMap()
  useEffect(() => {
    if (!map) return
    const listener = map.addListener('click', (e) => {
      if (!e.latLng) return
      onClick({ lat: e.latLng.lat(), lng: e.latLng.lng() })
    })
    return () => listener.remove()
  }, [map, onClick])
  return null
}

function ProductPicker({ products, onAdd }) {
  const [productId, setProductId] = useState(products[0]?.id ?? '')
  const [qty, setQty] = useState(1)

  useEffect(() => {
    if (!productId && products.length) setProductId(products[0].id)
  }, [products, productId])

  const submit = () => {
    if (!productId || qty < 1) return
    onAdd({ product_id: productId, qty: Number(qty) })
    setQty(1)
  }

  return (
    <div className="rc-product-picker">
      <select value={productId} onChange={(e) => setProductId(e.target.value)}>
        {products.map((p) => (
          <option key={p.id} value={p.id}>{p.id}</option>
        ))}
      </select>
      <input
        type="number"
        min="1"
        value={qty}
        onChange={(e) => setQty(e.target.value)}
      />
      <button className="rc-btn-add" onClick={submit}>Add</button>
    </div>
  )
}

function StopEditor({ stop, index, products, onChange, onRemove }) {
  const updateField = (patch) => onChange({ ...stop, ...patch })
  const updateWindow = (patch) =>
    onChange({ ...stop, time_window: { ...stop.time_window, ...patch } })
  const addDelivery = (line) =>
    onChange({ ...stop, deliveries: [...stop.deliveries, line] })
  const removeDelivery = (i) =>
    onChange({ ...stop, deliveries: stop.deliveries.filter((_, j) => j !== i) })

  return (
    <div className="rc-stop">
      <div className="rc-stop-head">
        <div className="rc-stop-num">{index + 1}</div>
        <input
          className="rc-stop-id"
          value={stop.id}
          onChange={(e) => updateField({ id: e.target.value })}
          placeholder="Stop ID"
        />
        <button className="rc-btn-remove" onClick={onRemove} title="Remove stop">×</button>
      </div>

      <div className="rc-stop-coords">
        {stop.coords.lat.toFixed(5)}, {stop.coords.lng.toFixed(5)}
      </div>

      <div className="rc-stop-row">
        <label>Window</label>
        <input
          type="time"
          value={stop.time_window.open}
          onChange={(e) => updateWindow({ open: e.target.value })}
        />
        <span className="rc-dash">–</span>
        <input
          type="time"
          value={stop.time_window.close}
          onChange={(e) => updateWindow({ close: e.target.value })}
        />
      </div>

      <div className="rc-stop-section">
        <div className="rc-stop-section-title">Deliveries</div>
        {stop.deliveries.length === 0 && (
          <div className="rc-empty-small">No items yet — add at least one.</div>
        )}
        {stop.deliveries.map((line, i) => (
          <div className="rc-line" key={i}>
            <span className="rc-line-id">{line.product_id}</span>
            <span className="rc-line-qty">×{line.qty}</span>
            <button className="rc-btn-remove-sm" onClick={() => removeDelivery(i)}>×</button>
          </div>
        ))}
        <ProductPicker products={products} onAdd={addDelivery} />
      </div>
    </div>
  )
}

function FleetReadout({ drivers, loading }) {
  if (loading) return <div className="rc-fleet-readout">Loading fleet…</div>
  if (drivers.length === 0) {
    return (
      <div className="rc-fleet-readout rc-fleet-empty">
        No drivers in Firestore — seed drivers before creating routes.
      </div>
    )
  }
  return (
    <div className="rc-fleet-readout">
      <div className="rc-fleet-label">Fleet ({drivers.length})</div>
      <div className="rc-fleet-chips">
        {drivers.map((d) => (
          <span key={d.driver_id} className="rc-fleet-chip" title={d.truck_id ?? ''}>
            {d.driver_id}
          </span>
        ))}
      </div>
    </div>
  )
}

export default function AdminRouteCreator({ onBack }) {
  const { logout } = useAuth()
  const [drivers, setDrivers] = useState([])
  const [driversLoading, setDriversLoading] = useState(true)
  const [products, setProducts] = useState([])
  const [stops, setStops] = useState([])
  const [submitting, setSubmitting] = useState(false)
  const [feedback, setFeedback] = useState(null)

  useEffect(() => {
    const unsub = subscribeToRoutes(
      (rs) => {
        setDrivers(rs.filter((r) => r.driver_id && r.driver_id !== 'admin'))
        setDriversLoading(false)
      },
      (err) => {
        console.error('subscribeToRoutes:', err)
        setDriversLoading(false)
      }
    )
    return unsub
  }, [])

  useEffect(() => {
    fetch(`${API_BASE}/products`)
      .then((r) => r.json())
      .then((data) => setProducts(data.products ?? []))
      .catch((e) => console.error('Failed to load products:', e))
  }, [])

  const addStop = (coords) => {
    setStops((prev) => [
      ...prev,
      {
        id: `S${String(prev.length + 1).padStart(3, '0')}`,
        coords,
        time_window: { ...DEFAULT_WINDOW },
        deliveries: [],
        pickups: [],
      },
    ])
  }

  const updateStop = (i, next) =>
    setStops((prev) => prev.map((s, j) => (j === i ? next : s)))

  const removeStop = (i) =>
    setStops((prev) => prev.filter((_, j) => j !== i))

  const canSubmit =
    drivers.length > 0 &&
    stops.length > 0 &&
    stops.every((s) => s.deliveries.length > 0)

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setFeedback(null)
    const today = new Date().toISOString().slice(0, 10)
    const body = {
      request_id: `ADMIN-${Date.now()}`,
      date: today,
      depot: DEFAULT_DEPOT,
      fleet: { ...DEFAULT_FLEET, num_vans: drivers.length },
      drivers: drivers.map((d) => ({
        id: d.driver_id,
        shift_start: DEFAULT_SHIFT.start,
        shift_end: DEFAULT_SHIFT.end,
      })),
      stops,
    }
    try {
      const res = await fetch(`${API_BASE}/optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `HTTP ${res.status}`)
      }
      const data = await res.json()
      const assigned = (data.vans ?? []).filter((v) => v.stops?.length > 0).length
      setFeedback({
        kind: 'ok',
        msg: `Optimized: ${data.fleet_drive_min} min total, ${assigned}/${data.vans?.length ?? 0} vans used, ${data.firestore_written} route(s) written.`,
      })
    } catch (e) {
      setFeedback({ kind: 'err', msg: e.message })
    } finally {
      setSubmitting(false)
    }
  }

  const mapMarkers = useMemo(
    () =>
      stops.map((s, i) => (
        <AdvancedMarker key={i} position={s.coords} title={s.id}>
          <div className="admin-stop-pin" style={{ borderColor: '#C41230', color: '#C41230' }}>
            {i + 1}
          </div>
        </AdvancedMarker>
      )),
    [stops]
  )

  return (
    <div className="dashboard">
      <nav className="navbar">
        <div className="navbar-brand">
          <span className="star">★</span>
          Damm Motion
          <span className="admin-badge">Route builder</span>
        </div>
        <div className="navbar-driver">
          <span>{stops.length}</span> stop{stops.length !== 1 ? 's' : ''} ·
          {' '}<span>{drivers.length}</span> driver{drivers.length !== 1 ? 's' : ''}
        </div>
        <div className="rc-nav-actions">
          <button className="btn-logout" onClick={onBack}>← Back</button>
          <button className="btn-logout" onClick={logout}>Log out</button>
        </div>
      </nav>

      <div className="dashboard-body">
        <aside className="rc-sidebar">
          <div className="rc-sidebar-head">
            <div>
              <div className="rc-sidebar-title">Build delivery plan</div>
              <div className="rc-sidebar-hint">
                Click the map to add stops. The optimizer will assign them to the available drivers.
              </div>
            </div>
          </div>

          <FleetReadout drivers={drivers} loading={driversLoading} />

          <div className="rc-stop-list">
            {stops.length === 0 && (
              <div className="rc-empty">No stops yet — click the map to add one.</div>
            )}
            {stops.map((s, i) => (
              <StopEditor
                key={i}
                stop={s}
                index={i}
                products={products}
                onChange={(next) => updateStop(i, next)}
                onRemove={() => removeStop(i)}
              />
            ))}
          </div>

          <div className="rc-sidebar-foot">
            {feedback && (
              <div className={`rc-feedback ${feedback.kind}`}>{feedback.msg}</div>
            )}
            <button
              className="rc-btn-submit"
              disabled={!canSubmit || submitting}
              onClick={submit}
            >
              {submitting ? 'Optimizing…' : 'Optimize & dispatch to fleet'}
            </button>
            {!canSubmit && stops.length > 0 && drivers.length > 0 && (
              <div className="rc-hint">Each stop needs at least one delivery item.</div>
            )}
          </div>
        </aside>

        <main className="map-container">
          <Map
            mapId={MAP_ID}
            defaultCenter={MAP_DEFAULT_CENTER}
            defaultZoom={MAP_DEFAULT_ZOOM}
            style={{ width: '100%', height: '100%' }}
            gestureHandling="greedy"
          >
            <MapClickHandler onClick={addStop} />
            <AdvancedMarker position={DEFAULT_DEPOT.coords} title="Depot">
              <div className="rc-depot-pin">D</div>
            </AdvancedMarker>
            {mapMarkers}
          </Map>
        </main>
      </div>
    </div>
  )
}
