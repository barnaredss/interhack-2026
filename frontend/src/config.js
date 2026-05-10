// Edit these values to retarget the admin route builder.
// Imported by AdminRouteCreator.jsx — change here, not in the component.

export const API_BASE = 'https://danvancea-interhack26.hf.space'

// Depot the optimizer routes vans out of.
// `id` is a free label; coords drive the actual routing.
export const DEFAULT_DEPOT = {
  id: 'WH-GR',
  coords: { lat: 41.5750, lng: 2.2500 },
  open: '07:00',
  close: '20:00',
}

// Fleet config sent to /optimize. `van_type` must match an entry in backend/vans.json.
// `vans_ref` and `products_ref` tell the backend which catalog files to use.
export const DEFAULT_FLEET = {
  van_type: '8_pallets',
  vans_ref: 'vans.json',
  products_ref: 'products.json',
}

// Default shift assigned to every driver in the request.
export const DEFAULT_SHIFT = { start: '07:00', end: '15:00' }

// Default time window prefilled when the admin clicks the map to add a stop.
export const DEFAULT_WINDOW = { open: '08:00', close: '18:00' }

// Where the map opens before any stops exist.
export const MAP_DEFAULT_CENTER = DEFAULT_DEPOT.coords
export const MAP_DEFAULT_ZOOM = 11
export const MAP_ID = 'DEMO_MAP_ID'
