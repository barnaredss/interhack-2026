import { useEffect, useMemo, useState } from 'react'
import mockRoute from '@shared/mock_5_stops.json'
import { useAuth } from '../context/AuthContext'
import { subscribeToRoute, markStopDelivered } from '../firebase'
import { useDriverLocation } from '../hooks/useDriverLocation'
import RouteMap from './Map'
import TruckStopDrawer from './TruckStopDrawer'
import VoiceAssistant from './VoiceAssistant'
import TruckView from './TruckView'

function generateMockCubes(mockRoute) {
  if (!mockRoute?.stops) return []
  const cubes = []
  mockRoute.stops.forEach((stop, stopIndex) => {
    stop.cargo?.forEach((item) => {
      for (let i = 0; i < 9; i++) {
        cubes.push({
          x: (item.position?.col ?? 0) * 3 + (i % 3),
          y: (item.position?.row ?? 0) * 3 + Math.floor(i / 3),
          z: 0,
          stop_index: stopIndex + 1,
          product_id: item.product,
        })
      }
    })
  })
  return cubes
}

function buildGoogleMapsUrl(points) {
  if (!points || points.length < 2) return '#'
  const origin = `${points[0].lat},${points[0].lng}`
  const dest = `${points.at(-1).lat},${points.at(-1).lng}`
  const waypoints = points.slice(1, -1).map((p) => `${p.lat},${p.lng}`).join('|')
  const base = `https://www.google.com/maps/dir/?api=1&origin=${origin}&destination=${dest}&travelmode=driving`
  return waypoints ? `${base}&waypoints=${waypoints}` : base
}

export default function Dashboard() {
  const FORCE_MOCK_ROUTE = false
  const { driverId, logout } = useAuth()
  const [route, setRoute] = useState(null)
  const [deliveryStatus, setDeliveryStatus] = useState(null)
  const [selectedStop, setSelectedStop] = useState(null)
  const [loading, setLoading] = useState(true)
  const [showTruck, setShowTruck] = useState(false)
  const { location: currentLocation } = useDriverLocation(driverId)

  useEffect(() => {
    if (FORCE_MOCK_ROUTE) {
      setRoute(null)
      setDeliveryStatus(mockRoute?.stops?.map((stop) => stop?.status ?? 'pending') ?? [])
      setLoading(false)
      return () => {}
    }

    // TEMPORARILY DISABLED DURING 3D DEBUG:
    // return subscribeToRoute(driverId, (data) => {
    //   setRoute(data)
    //   setLoading(false)
    //   setSelectedStop((prev) => {
    //     if (!prev) return null
    //     const nextStops = data?.stops ?? data?.points ?? mockRoute?.stops ?? []
    //     return nextStops[prev.index] ? prev : null
    //   })
    //   if (data) {
    //     setDeliveryStatus((prev) =>
    //       prev ?? (
    //         data.delivery_status ??
    //         (Array.isArray(data?.stops)
    //           ? data.stops.map((stop) => stop?.status ?? 'pending')
    //           : new Array(data?.points?.length ?? 0).fill('pending'))
    //       )
    //     )
    //   }
    // })
    return subscribeToRoute(driverId, (data) => {
      setRoute(data)
      setLoading(false)
      setSelectedStop((prev) => {
        if (!prev) return null
        const nextStops = data?.stops ?? data?.points ?? mockRoute?.stops ?? []
        return nextStops[prev.index] ? prev : null
      })
      if (data) {
        setDeliveryStatus((prev) =>
          prev ?? (
            data.delivery_status ??
            (Array.isArray(data?.stops)
              ? data.stops.map((stop) => stop?.status ?? 'pending')
              : new Array(data?.points?.length ?? 0).fill('pending'))
          )
        )
      }
    })
  }, [driverId])

  const normalizedRoute = useMemo(() => {
    const stopsLookLikeUnified =
      Array.isArray(route?.stops) &&
      route.stops.every((stop) => Number.isFinite(stop?.location?.lat) && Number.isFinite(stop?.location?.lng))

    const unifiedStops = stopsLookLikeUnified
      ? route.stops
      : Array.isArray(route?.points)
        ? route.points.map((point, index) => ({
            stopId: `legacy-${index + 1}`,
            sequence: index + 1,
            status: route?.delivery_status?.[index] ?? 'pending',
            serviceMinutes: route?.service_times?.[index] ?? null,
            location: {
              lat: point?.lat,
              lng: point?.lng,
              address: point?.address ?? `Stop ${index + 1}`,
            },
            cargo: route?.stops?.[index]?.cargo ?? route?.stop_data?.[index]?.cargo ?? route?.stopSnapshots?.[index]?.cargo ?? [],
          }))
        : mockRoute.stops

    const points = unifiedStops.map((stop, index) => ({
      lat: stop?.location?.lat,
      lng: stop?.location?.lng,
      address: stop?.location?.address ?? `Stop ${index + 1}`,
    }))

    const fallbackStatus = unifiedStops.map((stop) => stop?.status ?? 'pending')
    const normalizedStatus = deliveryStatus ?? route?.delivery_status ?? fallbackStatus

    return {
      truckId: route?.truck_id ?? route?.truckId ?? mockRoute?.truckId ?? null,
      points,
      stops: unifiedStops,
      deliveryStatus: normalizedStatus,
      windows: route?.windows ?? new Array(unifiedStops.length).fill(null),
      serviceTimes:
        route?.service_times ??
        unifiedStops.map((stop) => stop?.serviceMinutes ?? null),
      isMock: !route,
    }
  }, [route, deliveryStatus])

  function canToggle(index) {
    const statusArray = normalizedRoute.deliveryStatus ?? []
    const delivered = statusArray[index] === 'delivered'
    if (!delivered) {
      // Can only mark delivered if all previous stops are delivered
      return statusArray.slice(0, index).every((s) => s === 'delivered')
    } else {
      // Can only undo if all subsequent stops are still pending
      return statusArray.slice(index + 1).every((s) => s === 'pending')
    }
  }

  async function handleToggleDelivered(index) {
    if (!canToggle(index)) return
    const updated = [...(normalizedRoute.deliveryStatus ?? [])]
    updated[index] = updated[index] === 'delivered' ? 'pending' : 'delivered'
    setDeliveryStatus(updated)
    if (!normalizedRoute.isMock) {
      await markStopDelivered(driverId, updated)
    }
  }

  function handleSelectStop(index) {
    const unifiedStop = normalizedRoute?.stops?.[index] ?? null
    const legacyStop =
      route?.stops?.[index] ??
      route?.stop_data?.[index] ??
      route?.stopSnapshots?.[index] ??
      null
    const mergedStopData = {
      ...(legacyStop ?? {}),
      ...(unifiedStop ?? {}),
      cargo: unifiedStop?.cargo ?? legacyStop?.cargo ?? [],
    }
    const point = normalizedRoute?.points?.[index]
    if (!point) {
      setSelectedStop(null)
      return
    }

    setSelectedStop({
      index,
      stopId: mergedStopData?.stopId ?? mergedStopData?.id ?? unifiedStop?.stopId ?? null,
      point,
      truckId: normalizedRoute?.truckId ?? null,
      driverId,
      window: normalizedRoute?.windows?.[index] ?? null,
      serviceTime: normalizedRoute?.serviceTimes?.[index] ?? null,
      deliveryStatus: normalizedRoute?.deliveryStatus?.[index] ?? 'pending',
      stopData: mergedStopData,
      routeContext: route ?? mockRoute ?? null,
    })
  }

  const routeForAssistant = useMemo(
    () => ({
      truck_id: normalizedRoute.truckId,
      points: normalizedRoute.points,
      windows: normalizedRoute.windows,
      service_times: normalizedRoute.serviceTimes,
    }),
    [normalizedRoute],
  )

  return (
    <div className="dashboard">
      <nav className="navbar">
        <div className="navbar-brand">
          <span className="star">★</span>
          Damm Motion
        </div>
        <div className="navbar-driver">
          Driver: <span>{driverId}</span>
        </div>
        <button className="btn-logout" onClick={logout}>Log out</button>
      </nav>

      <div className="dashboard-body">
        <aside className="sidebar">
          <div className="sidebar-header">
            <h2>Today's route</h2>
            <span className="truck-badge">🚛 {normalizedRoute.truckId ?? 'N/A'}</span>
          </div>

          <div className="stops-list">
            {loading && <p className="sidebar-state">Loading…</p>}
            {!loading && normalizedRoute.isMock && (
              <p className="sidebar-state">Using shared mock route for testing.</p>
            )}
            {normalizedRoute?.points.map((point, i) => {
              const delivered = normalizedRoute?.deliveryStatus?.[i] === 'delivered'
              return (
                <div className={`stop-item${delivered ? ' delivered' : ''}`} key={i}>
                  <div className="stop-number">{i + 1}</div>
                  <div className="stop-details">
                    <div className="stop-address">{point.address || 'Delivery point'}</div>
                    {normalizedRoute?.windows?.[i] && (
                      <div className="stop-meta">
                        <span className="meta-label">Window</span>
                        {normalizedRoute.windows[i].start} – {normalizedRoute.windows[i].end}
                      </div>
                    )}
                    {normalizedRoute?.serviceTimes?.[i] != null && (
                      <div className="stop-meta">
                        <span className="meta-label">Service</span>
                        {normalizedRoute.serviceTimes[i]} min
                      </div>
                    )}
                    <div className="stop-coords">
                      {point.lat.toFixed(4)}, {point.lng.toFixed(4)}
                    </div>
                  </div>
                  <button
                    className={`btn-deliver${delivered ? ' done' : ''}`}
                    onClick={() => handleToggleDelivered(i)}
                    disabled={!canToggle(i)}
                    title={
                      delivered
                        ? canToggle(i) ? 'Click to undo' : 'Complete later stops first'
                        : canToggle(i) ? 'Mark as delivered' : 'Complete previous stops first'
                    }
                  >
                    {delivered ? '✓' : '○'}
                  </button>
                </div>
              )
            })}
          </div>

          {normalizedRoute.points.length > 0 && (
            <div className="sidebar-footer">
              {(route?.items || route?.cubes || route?.pallets || normalizedRoute.isMock) && (
                <button className="btn-truck-view" onClick={() => setShowTruck(true)}>
                  View truck interior
                </button>
              )}
              <a
                className="btn-gmaps"
                href={buildGoogleMapsUrl(normalizedRoute.points)}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open in Google Maps ↗
              </a>
            </div>
          )}
        </aside>

        {showTruck && (route?.items || route?.cubes || route?.pallets || normalizedRoute.isMock) && (
          <TruckView
            layout={route?.truck_layout ?? { rows: 2, cols: 4 }}
            items={route?.items}
            itemGrid={route?.item_grid}
            cubes={route?.cubes ?? (route?.items ? null : generateMockCubes(mockRoute))}
            cubeGrid={route?.cube_grid ?? { L: 12, W: 6, H: 1 }}
            pallets={route?.pallets}
            deliveries={route?.deliveries}
            deliveryStatus={deliveryStatus}
            points={normalizedRoute.points}
            truckId={normalizedRoute.truckId}
            onClose={() => setShowTruck(false)}
          />
        )}

        <main className="map-container">
          {loading ? (
            <div className="map-empty"><div className="spinner" /></div>
          ) : normalizedRoute.points.length === 0 ? (
            <div className="map-empty"><p>No route assigned for today.</p></div>
          ) : (
            <>
              <RouteMap
                points={normalizedRoute.points}
                currentLocation={currentLocation}
                deliveryStatus={normalizedRoute.deliveryStatus}
                activeStopIndex={selectedStop?.index ?? null}
                onSelectStop={handleSelectStop}
              />
              <TruckStopDrawer selectedStop={selectedStop} onClose={() => setSelectedStop(null)} />
              <VoiceAssistant
                route={routeForAssistant}
                deliveryStatus={normalizedRoute.deliveryStatus}
                canToggle={canToggle}
                onMarkDelivered={handleToggleDelivered}
              />
            </>
          )}
        </main>
      </div>
    </div>
  )
}
