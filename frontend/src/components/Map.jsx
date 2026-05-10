import { Map, AdvancedMarker } from '@vis.gl/react-google-maps'
import RouteRenderer from './RouteRenderer'

function CurrentLocationMarker({ location }) {
  if (!location) return null
  return (
    <AdvancedMarker position={location} title="Your location">
      <div className="current-location-marker">
        <div className="clm-pulse" />
        <div className="clm-dot" />
      </div>
    </AdvancedMarker>
  )
}

export default function RouteMap({ points, currentLocation, deliveryStatus, activeStopIndex, onSelectStop }) {
  if (!Array.isArray(points) || points.length === 0) {
    return null
  }

  return (
    <Map
      mapId="DEMO_MAP_ID"
      defaultCenter={{ lat: points[0].lat, lng: points[0].lng }}
      defaultZoom={13}
      style={{ width: '100%', height: '100%' }}
      gestureHandling="greedy"
    >
      <RouteRenderer points={points} deliveryStatus={deliveryStatus} />

      {points.map((point, i) => {
        const delivered = deliveryStatus?.[i] === 'delivered'
        const isActive = activeStopIndex === i
        return (
          <AdvancedMarker
            key={i}
            position={{ lat: point.lat, lng: point.lng }}
            title={point?.address ?? `Stop ${i + 1}`}
            onClick={() => onSelectStop?.(i)}
          >
            <div className={`map-pin${delivered ? ' delivered' : ''}${isActive ? ' active' : ''}`}>
              <span>{i + 1}</span>
            </div>
          </AdvancedMarker>
        )
      })}

      <CurrentLocationMarker location={currentLocation} />
    </Map>
  )
}
