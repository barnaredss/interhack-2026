import { useEffect, useState, useRef } from 'react'
import { useMap, useMapsLibrary } from '@vis.gl/react-google-maps'

export default function RouteRenderer({ points, deliveryStatus, activeColor = '#C41230', completedColor = '#6B7280' }) {
  const map = useMap()
  const routesLib = useMapsLibrary('routes')
  const [directionsResult, setDirectionsResult] = useState(null)
  const polylinesRef = useRef([])

  // 1. Fetch full route ONCE when points change
  useEffect(() => {
    if (!routesLib || !map || !Array.isArray(points) || points.length < 2) return

    const service = new routesLib.DirectionsService()
    service.route(
      {
        origin: { lat: points[0].lat, lng: points[0].lng },
        destination: { lat: points.at(-1).lat, lng: points.at(-1).lng },
        waypoints: points.slice(1, -1).map((p) => ({ location: { lat: p.lat, lng: p.lng }, stopover: true })),
        travelMode: 'DRIVING',
      },
      (result, status) => {
        if (status === 'OK') {
          setDirectionsResult(result)
        }
      }
    )
  }, [routesLib, map, JSON.stringify(points)])

  // 2. Render legs as Polylines and update their colors
  useEffect(() => {
    if (!map || !window.google || !directionsResult) return

    const route = directionsResult.routes[0]
    if (!route || !route.legs) return

    // Create polylines if they don't exist or if count changed
    if (polylinesRef.current.length !== route.legs.length) {
      polylinesRef.current.forEach((l) => l.setMap(null))
      polylinesRef.current = route.legs.map((leg) => {
        // Collect all path points for this leg
        const path = []
        leg.steps.forEach((step) => {
          step.path.forEach((p) => path.push(p))
        })
        return new window.google.maps.Polyline({
          path,
          strokeWeight: 6,
          map,
        })
      })
    }

    const firstPending = deliveryStatus ? deliveryStatus.findIndex((s) => s === 'pending') : 0

    polylinesRef.current.forEach((polyline, i) => {
      // Leg i goes from point i to point i+1
      // If the stop we are heading TO (i+1) is delivered, it's completed.
      // If firstPending is the stop we are heading to or we're at, it's active.
      const isCompleted = firstPending === -1 || i < firstPending - 1
      
      polyline.setOptions({
        strokeColor: isCompleted ? completedColor : activeColor,
        strokeOpacity: isCompleted ? 0.6 : 0.85,
        zIndex: isCompleted ? 1 : 2
      })
    })

  }, [map, directionsResult, deliveryStatus, activeColor, completedColor])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      polylinesRef.current.forEach((l) => l.setMap(null))
      polylinesRef.current = []
    }
  }, [])

  return null
}
