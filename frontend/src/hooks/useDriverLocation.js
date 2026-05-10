import { useEffect, useState } from 'react'
import { doc, updateDoc } from 'firebase/firestore'
import { db } from '../firebase'

export function useDriverLocation(driverId) {
  const [location, setLocation] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!navigator.geolocation) {
      setError('Geolocation not supported by this browser.')
      return
    }

    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const loc = { lat: pos.coords.latitude, lng: pos.coords.longitude }
        console.log('[GPS] position:', loc)
        setLocation(loc)
        updateDoc(doc(db, 'routes', driverId), { location: loc })
          .catch((e) => console.error('[GPS] Firestore write failed:', e))
      },
      (err) => {
        console.error('[GPS] error:', err.code, err.message)
        setError(err.message)
      },
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 }
    )

    // Clear location from Firestore on unmount
    return () => {
      navigator.geolocation.clearWatch(watchId)
      updateDoc(doc(db, 'routes', driverId), { location: null }).catch(() => {})
    }
  }, [driverId])

  return { location, error }
}
