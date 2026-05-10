import { useMemo, useState } from 'react'
import * as THREE from 'three'
import { Canvas } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import mockRoute from '@shared/mock_5_stops.json'

const SLOT_STYLES = {
  target_unload: {
    fill: '#facc15',
    border: '#facc15',
    emissive: '#f59e0b',
    emissiveIntensity: 0.45,
    transparent: false,
    opacity: 1,
    wireframe: false,
    label: 'UNLOAD NOW',
  },
  full: {
    fill: '#3b82f6',
    border: '#60a5fa',
    emissive: '#1d4ed8',
    emissiveIntensity: 0.08,
    transparent: true,
    opacity: 0.7,
    wireframe: false,
    label: 'NEXT STOP',
  },
  free: {
    fill: '#93c5fd',
    border: '#7dd3fc',
    emissive: '#0f172a',
    emissiveIntensity: 0,
    transparent: true,
    opacity: 0.15,
    wireframe: true,
    label: 'FREE SPOT',
  },
  empty_return: {
    fill: '#4b5563',
    border: '#9ca3af',
    emissive: '#111827',
    emissiveIntensity: 0.05,
    transparent: false,
    opacity: 1,
    wireframe: false,
    label: 'EMPTY RETURN',
  },
}

const SLOT_SIZE = [1.55, 0.9, 1.05]
const SLOT_GAP = { x: 0.32, z: 0.4 }

function getSlotStyle(type) {
  return SLOT_STYLES[type] ?? SLOT_STYLES.full
}

function buildFallbackMatrix() {
  return Array.from({ length: 2 }, (_, rowIndex) =>
    Array.from({ length: 4 }, (_, colIndex) => ({
      id: `fallback-${rowIndex}-${colIndex}`,
      row: rowIndex,
      col: colIndex,
      type: 'free',
      product: null,
    })),
  )
}

function matrixFromPallets(pallets) {
  if (!Array.isArray(pallets) || pallets.length === 0) {
    return buildFallbackMatrix()
  }

  const rows = Math.max(2, ...pallets.map((pallet) => Number(pallet?.row ?? pallet?.z ?? 0) + 1))
  const cols = Math.max(4, ...pallets.map((pallet) => Number(pallet?.col ?? pallet?.x ?? 0) + 1))
  const matrix = Array.from({ length: rows }, (_, rowIndex) =>
    Array.from({ length: cols }, (_, colIndex) => ({
      id: `slot-${rowIndex}-${colIndex}`,
      row: rowIndex,
      col: colIndex,
      type: 'free',
      product: null,
    })),
  )

  pallets.forEach((pallet, index) => {
    const row = Math.max(0, Number(pallet?.row ?? pallet?.z ?? Math.floor(index / cols)))
    const col = Math.max(0, Number(pallet?.col ?? pallet?.x ?? (index % cols)))
    if (!matrix[row]?.[col]) return
    matrix[row][col] = {
      ...matrix[row][col],
      id: pallet?.id ?? `pallet-${index + 1}`,
      type: pallet?.type ?? 'full',
      product: pallet?.product ?? pallet?.label ?? null,
    }
  })

  return matrix
}

function normalizeCargoItems(cargo) {
  if (!Array.isArray(cargo)) return []
  return cargo.map((item, index) => ({
    ...item,
    row: Number(item?.position?.row ?? item?.row ?? item?.z ?? Math.floor(index / 4)),
    col: Number(item?.position?.col ?? item?.col ?? item?.x ?? (index % 4)),
    product: item?.product ?? item?.label ?? null,
    type: item?.type ?? 'full',
  }))
}

function normalizeMatrix(rawMatrix) {
  if (!Array.isArray(rawMatrix) || rawMatrix.length === 0) {
    return buildFallbackMatrix()
  }

  const colCount = Math.max(
    1,
    rawMatrix.reduce((maxCols, row) => Math.max(maxCols, Array.isArray(row) ? row.length : 0), 0),
  )

  return rawMatrix.map((row, rowIndex) => {
    const safeRow = Array.isArray(row) ? row : []
    return Array.from({ length: colCount }, (_, colIndex) => {
      const slot = safeRow[colIndex]
      return {
        ...slot,
        id: slot?.id ?? `slot-${rowIndex}-${colIndex}`,
        type: slot?.type ?? 'free',
        product: slot?.product ?? slot?.label ?? null,
      }
    })
  })
}

function Wheel({ position, radius = 0.34, wheelWidth = 0.24 }) {
  return (
    <group position={position} rotation={[Math.PI / 2, 0, 0]}>
      <mesh castShadow receiveShadow>
        <cylinderGeometry args={[radius, radius, wheelWidth, 28]} />
        <meshStandardMaterial color="#111111" metalness={0.08} roughness={0.85} />
      </mesh>
      <mesh>
        <cylinderGeometry args={[radius * 0.52, radius * 0.52, wheelWidth + 0.02, 24]} />
        <meshStandardMaterial color="#9ca3af" metalness={0.45} roughness={0.35} />
      </mesh>
    </group>
  )
}

function TruckFrame({ width, depth }) {
  const frameHeight = 1.2
  const cabWidth = THREE.MathUtils.clamp(depth * 0.8, 2, 3.3)
  const cabFrontX = -(width / 2 + 1.45)
  const cargoDeckLength = width + 1.15
  const cargoDeckHalfLength = cargoDeckLength / 2
  const cabBodyLength = 1.62
  const cabFrontOverhang = Math.max(0, Math.abs(cabFrontX) + cabBodyLength / 2 - cargoDeckHalfLength)
  const chassisLength = cargoDeckLength + cabFrontOverhang
  const chassisCenterX = -cabFrontOverhang / 2
  const wheelTrack = depth + 1
  const frontAxleX = cabFrontX + 0.15
  const rearAxle1X = width * 0.14
  const rearAxle2X = width * 0.36

  return (
    <group>
      <mesh receiveShadow position={[0, -0.62, 0]}>
        <boxGeometry args={[width + 1.15, 0.15, depth + 1.05]} />
        <meshStandardMaterial color="#111827" metalness={0.2} roughness={0.8} />
      </mesh>

      <mesh receiveShadow position={[chassisCenterX, -0.88, 0]}>
        <boxGeometry args={[chassisLength, 0.22, depth + 1.05]} />
        <meshStandardMaterial color="#1f2937" metalness={0.35} roughness={0.45} />
      </mesh>

      <mesh position={[0, 0.24, -(depth / 2 + 0.52)]}>
        <boxGeometry args={[width + 1.05, frameHeight, 0.08]} />
        <meshStandardMaterial color="#9ca3af" metalness={0.2} roughness={0.55} />
      </mesh>

      <mesh position={[0, 0.24, depth / 2 + 0.52]}>
        <boxGeometry args={[width + 1.05, frameHeight, 0.08]} />
        <meshStandardMaterial color="#9ca3af" metalness={0.2} roughness={0.55} />
      </mesh>

      <group position={[cabFrontX, 0, 0]} rotation={[0, Math.PI / 2, 0]}>
        <mesh castShadow receiveShadow position={[0.28, -0.22, 0]}>
          <boxGeometry args={[cabWidth + 0.42, 0.34, 1.62]} />
          <meshStandardMaterial color="#374151" metalness={0.3} roughness={0.48} />
        </mesh>

        <mesh castShadow receiveShadow position={[0, -0.18, 0]}>
          <boxGeometry args={[cabWidth, 0.72, 1.5]} />
          <meshStandardMaterial color="#1f2937" metalness={0.25} roughness={0.5} />
        </mesh>

        <mesh castShadow receiveShadow position={[0, 0.27, 0.16]}>
          <boxGeometry args={[cabWidth * 0.9, 0.62, 1.02]} />
          <meshStandardMaterial color="#334155" metalness={0.22} roughness={0.48} />
        </mesh>

        <mesh position={[0, 0.34, -0.33]}>
          <boxGeometry args={[cabWidth * 0.84, 0.36, 0.08]} />
          <meshStandardMaterial
            color="#7dd3fc"
            emissive="#0f172a"
            emissiveIntensity={0.14}
            metalness={0.1}
            roughness={0.12}
            transparent
            opacity={0.7}
          />
        </mesh>

        <mesh castShadow receiveShadow position={[-0.52, -0.36, -0.01]}>
          <boxGeometry args={[0.06, 0.14, cabWidth * 0.72]} />
          <meshStandardMaterial color="#9ca3af" metalness={0.28} roughness={0.42} />
        </mesh>
      </group>

      <Wheel position={[frontAxleX, -1.16, wheelTrack / 2]} />
      <Wheel position={[frontAxleX, -1.16, -wheelTrack / 2]} />
      <Wheel position={[rearAxle1X, -1.16, wheelTrack / 2]} />
      <Wheel position={[rearAxle1X, -1.16, -wheelTrack / 2]} />
      <Wheel position={[rearAxle2X, -1.16, wheelTrack / 2]} />
      <Wheel position={[rearAxle2X, -1.16, -wheelTrack / 2]} />
    </group>
  )
}

function SlotMesh({ slot, position }) {
  const [isHovered, setIsHovered] = useState(false)
  const style = getSlotStyle(slot?.type)
  const productText = slot?.product ?? 'Free slot'
  const showFullText = slot?.type === 'target_unload' || slot?.type === 'empty_return'

  return (
    <group position={position}>
      <mesh
        castShadow
        receiveShadow
        onPointerOver={() => setIsHovered(true)}
        onPointerOut={() => setIsHovered(false)}
      >
        <boxGeometry args={SLOT_SIZE} />
        <meshStandardMaterial
          color={style.fill}
          emissive={style.emissive}
          emissiveIntensity={style.emissiveIntensity}
          metalness={0.15}
          roughness={0.45}
          transparent={style.transparent}
          opacity={style.opacity}
          wireframe={style.wireframe}
        />
      </mesh>

      <Html
        transform
        sprite
        occlude
        distanceFactor={9.5}
        position={[0, SLOT_SIZE[1] / 2 + 0.28, 0]}
        zIndexRange={[20, 0]}
      >
        <div className="truck-slot-badge-wrap">
          {showFullText || isHovered ? (
            <div className="truck-slot-badge" style={{ borderColor: style.border }}>
              <p className="truck-slot-badge-title">{style.label}</p>
              <p className="truck-slot-badge-product">{productText}</p>
            </div>
          ) : (
            <div className="truck-slot-dot" style={{ backgroundColor: style.fill }} />
          )}
          <div className="truck-slot-line" />
        </div>
      </Html>
    </group>
  )
}

function TruckCargoScene({ matrix }) {
  const rowCount = matrix?.length ?? 0
  const colCount = matrix?.[0]?.length ?? 0
  const stepX = SLOT_SIZE[0] + SLOT_GAP.x
  const stepZ = SLOT_SIZE[2] + SLOT_GAP.z
  const width = Math.max(0, (colCount - 1) * stepX + SLOT_SIZE[0])
  const depth = Math.max(0, (rowCount - 1) * stepZ + SLOT_SIZE[2])
  const baseX = -((colCount - 1) * stepX) / 2
  const baseZ = -((rowCount - 1) * stepZ) / 2

  return (
    <>
      <ambientLight intensity={0.7} />
      <directionalLight
        castShadow
        intensity={1.15}
        position={[6.5, 8.2, 4.2]}
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
      />
      <directionalLight intensity={0.58} position={[-5, 3.8, -4.5]} />

      <TruckFrame width={width} depth={depth} />

      {matrix?.map((row, rowIndex) =>
        row?.map((slot, colIndex) => (
          <SlotMesh
            key={`slot-${rowIndex}-${colIndex}-${slot?.type ?? 'free'}-${slot?.id ?? colIndex}`}
            slot={slot ?? { type: 'free', product: null }}
            position={[baseX + colIndex * stepX, 0, baseZ + rowIndex * stepZ]}
          />
        )),
      )}

      <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.7, 0]}>
        <planeGeometry args={[width + 3, depth + 3]} />
        <shadowMaterial opacity={0.22} />
      </mesh>

      <OrbitControls
        enablePan={false}
        minDistance={4.2}
        maxDistance={18}
        minPolarAngle={0.25}
        maxPolarAngle={Math.PI / 2}
        target={[0, 0, 0]}
      />
    </>
  )
}

export default function TruckCargo3D({ stopData, matrix, pallets, cargo, selectedStopId, selectedStopIndex }) {
  const selectedMockStop =
    mockRoute?.stops?.find((stop, index) => {
      if (selectedStopId && (stop?.stopId === selectedStopId || stop?.id === selectedStopId)) {
        return true
      }
      if (Number.isInteger(selectedStopIndex) && selectedStopIndex >= 0) {
        return index === selectedStopIndex
      }
      return false
    }) ?? mockRoute?.stops?.[0] ?? null

  const activeCargo = Array.isArray(cargo) && cargo.length > 0
    ? cargo
    : selectedMockStop?.cargo ?? []

  const selectedStopKey = selectedStopId ?? selectedMockStop?.stopId ?? `stop-${selectedStopIndex ?? 0}`
  const normalizedMatrix = useMemo(() => {
    if (Array.isArray(activeCargo) && activeCargo.length > 0) {
      return matrixFromPallets(normalizeCargoItems(activeCargo))
    }

    const stopMatrix = stopData?.matrix ?? stopData?.truck_state?.matrix
    if (Array.isArray(stopMatrix) && stopMatrix.length > 0) {
      return normalizeMatrix(stopMatrix)
    }

    if (Array.isArray(matrix) && matrix.length > 0) {
      return normalizeMatrix(matrix)
    }

    const sourcePallets = stopData?.pallets ?? pallets ?? []
    return matrixFromPallets(sourcePallets)
  }, [stopData, matrix, pallets, activeCargo])

  console.log('3D PROPS:', activeCargo)

  return (
    <div className="truck-cargo-canvas">
      <Canvas
        key={selectedStopKey}
        shadows
        camera={{ position: [6.4, 5.8, 6.9], fov: 42, near: 0.1, far: 100 }}
        gl={{ antialias: true, alpha: true }}
      >
        <TruckCargoScene matrix={normalizedMatrix} />
      </Canvas>
    </div>
  )
}
