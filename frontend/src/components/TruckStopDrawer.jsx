import { useMemo } from 'react'
import { normalizeStopData } from '../adapters/normalizeStopData'
import TruckCargo3D from './TruckCargo3D'

export default function TruckStopDrawer({ selectedStop, onClose }) {
  const normalizedStop = useMemo(() => normalizeStopData(selectedStop), [selectedStop])

  if (!selectedStop) return null

  const stopIndex = (selectedStop?.index ?? 0) + 1
  const serviceTime = selectedStop?.serviceTime ?? null
  const deliveryStatus = selectedStop?.deliveryStatus ?? 'pending'

  return (
    <aside className="truck-stop-drawer" role="dialog" aria-modal="false" aria-label="Stop cargo details">
      <header className="truck-stop-drawer-header">
        <div>
          <p className="truck-stop-kicker">Stop {stopIndex}</p>
          <h3 className="truck-stop-title">{normalizedStop?.address ?? 'Delivery point'}</h3>
        </div>
        <button type="button" className="truck-stop-close-btn" onClick={onClose}>
          Close
        </button>
      </header>

      <div className="truck-stop-meta-grid">
        <div className="truck-stop-meta-card">
          <span className="truck-stop-meta-label">Truck</span>
          <strong>{selectedStop?.truckId ?? normalizedStop?.truckId ?? 'N/A'}</strong>
        </div>
        <div className="truck-stop-meta-card">
          <span className="truck-stop-meta-label">Status</span>
          <strong>{deliveryStatus}</strong>
        </div>
        <div className="truck-stop-meta-card">
          <span className="truck-stop-meta-label">Service</span>
          <strong>{serviceTime != null ? `${serviceTime} min` : 'N/A'}</strong>
        </div>
        <div className="truck-stop-meta-card">
          <span className="truck-stop-meta-label">Pallets</span>
          <strong>{normalizedStop?.pallets?.length ?? 0}</strong>
        </div>
      </div>

      <div className="truck-stop-canvas-wrap">
        <TruckCargo3D
          stopData={normalizedStop}
          selectedStopId={selectedStop?.stopId ?? normalizedStop?.stopId ?? null}
          selectedStopIndex={selectedStop?.index ?? null}
          cargo={selectedStop?.stopData?.cargo ?? normalizedStop?.pallets ?? []}
        />
      </div>
    </aside>
  )
}
