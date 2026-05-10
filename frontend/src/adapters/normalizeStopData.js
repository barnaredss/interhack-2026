const SLOT_TYPES = new Set(['target_unload', 'full', 'free', 'empty_return'])
const TYPE_COLORS = {
  target_unload: '#facc15',
  full: '#3b82f6',
  free: '#93c5fd',
  empty_return: '#6b7280',
}

function toFiniteNumber(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function clampNonNegative(value, fallback = 0) {
  return Math.max(0, toFiniteNumber(value, fallback))
}

function normalizeType(typeValue) {
  return SLOT_TYPES.has(typeValue) ? typeValue : 'free'
}

function buildDefaultMatrix(rows = 2, cols = 4) {
  const safeRows = Math.max(1, clampNonNegative(rows, 2))
  const safeCols = Math.max(1, clampNonNegative(cols, 4))
  return Array.from({ length: safeRows }, (_, rowIndex) =>
    Array.from({ length: safeCols }, (_, colIndex) => ({
      id: `slot-${rowIndex}-${colIndex}`,
      row: rowIndex,
      col: colIndex,
      x: colIndex,
      y: 0,
      z: rowIndex,
      type: 'free',
      color: TYPE_COLORS.free,
      label: 'Free slot',
      product: null,
    })),
  )
}

function normalizeMatrix(rawMatrix) {
  if (!Array.isArray(rawMatrix) || rawMatrix.length === 0) {
    return buildDefaultMatrix()
  }

  const colCount = Math.max(
    1,
    rawMatrix.reduce((maxCols, row) => {
      const rowLength = Array.isArray(row) ? row.length : 0
      return Math.max(maxCols, rowLength)
    }, 0),
  )

  return rawMatrix.map((row, rowIndex) => {
    const normalizedRow = Array.isArray(row) ? row : []
    return Array.from({ length: colCount }, (_, colIndex) => {
      const slot = normalizedRow[colIndex]
      const type = normalizeType(slot?.type)
      const id = slot?.id ?? slot?.pallet_id ?? `slot-${rowIndex}-${colIndex}`
      const label = slot?.label ?? slot?.product ?? slot?.name ?? `Slot ${rowIndex + 1}-${colIndex + 1}`
      const x = toFiniteNumber(slot?.x ?? slot?.position?.x ?? colIndex, colIndex)
      const y = toFiniteNumber(slot?.y ?? slot?.position?.y ?? 0, 0)
      const z = toFiniteNumber(slot?.z ?? slot?.position?.z ?? rowIndex, rowIndex)

      return {
        ...slot,
        id,
        row: rowIndex,
        col: colIndex,
        x,
        y,
        z,
        type,
        color: slot?.color ?? TYPE_COLORS[type],
        label,
        product: slot?.product ?? slot?.sku ?? slot?.name ?? null,
      }
    })
  })
}

function pickPalletArray(rawStop) {
  return (
    rawStop?.cargo ??
    rawStop?.stopData?.cargo ??
    rawStop?.pallets ??
    rawStop?.truck_state?.pallets ??
    rawStop?.cargo?.pallets ??
    rawStop?.stopData?.pallets ??
    rawStop?.routeContext?.pallets ??
    []
  )
}

function normalizeUnifiedCargo(rawStop) {
  const cargo = Array.isArray(rawStop?.cargo)
    ? rawStop.cargo
    : Array.isArray(rawStop?.stopData?.cargo)
      ? rawStop.stopData.cargo
      : []

  return cargo.map((item, index) => {
    const inferredType = normalizeType(item?.type ?? 'full')
    const row = clampNonNegative(item?.position?.row ?? item?.row ?? Math.floor(index / 4), 0)
    const col = clampNonNegative(item?.position?.col ?? item?.col ?? index % 4, 0)

    return {
      id: item?.id ?? `cargo-${index + 1}`,
      x: toFiniteNumber(item?.position?.col ?? item?.x ?? col, col),
      y: toFiniteNumber(item?.position?.y ?? item?.y ?? 0, 0),
      z: toFiniteNumber(item?.position?.row ?? item?.z ?? row, row),
      row,
      col,
      type: inferredType,
      color: item?.color ?? TYPE_COLORS[inferredType],
      label: item?.label ?? item?.product ?? `Cargo ${index + 1}`,
      product: item?.product ?? null,
      weight: clampNonNegative(item?.weightKg ?? item?.weight ?? 0, 0),
    }
  })
}

function normalizePallets(rawStop) {
  const unifiedCargo = normalizeUnifiedCargo(rawStop)
  if (unifiedCargo.length > 0) {
    return unifiedCargo
  }

  const pallets = Array.isArray(pickPalletArray(rawStop)) ? pickPalletArray(rawStop) : []
  return pallets.map((rawPallet, index) => {
    const inferredType = normalizeType(
      rawPallet?.type ??
        rawPallet?.load_type ??
        rawPallet?.status ??
        rawPallet?.category ??
        (rawPallet?.returnable ? 'empty_return' : 'full'),
    )

    const x = toFiniteNumber(rawPallet?.x ?? rawPallet?.position?.x ?? rawPallet?.col ?? index % 4, index % 4)
    const y = toFiniteNumber(rawPallet?.y ?? rawPallet?.position?.y ?? rawPallet?.level ?? 0, 0)
    const z = toFiniteNumber(rawPallet?.z ?? rawPallet?.position?.z ?? rawPallet?.row ?? Math.floor(index / 4), Math.floor(index / 4))

    return {
      id: rawPallet?.id ?? rawPallet?.pallet_id ?? rawPallet?.code ?? `pallet-${index + 1}`,
      x,
      y,
      z,
      row: clampNonNegative(rawPallet?.row ?? z, z),
      col: clampNonNegative(rawPallet?.col ?? x, x),
      type: inferredType,
      color: rawPallet?.color ?? TYPE_COLORS[inferredType],
      label: rawPallet?.label ?? rawPallet?.product ?? rawPallet?.name ?? `Pallet ${index + 1}`,
      product: rawPallet?.product ?? rawPallet?.name ?? null,
      weight: clampNonNegative(rawPallet?.weight ?? rawPallet?.weight_kg ?? 0, 0),
    }
  })
}

function matrixFromPallets(pallets, fallbackRows = 2, fallbackCols = 4) {
  if (!Array.isArray(pallets) || pallets.length === 0) {
    return buildDefaultMatrix(fallbackRows, fallbackCols)
  }

  const computedRows = pallets.reduce((maxRows, pallet) => Math.max(maxRows, clampNonNegative(pallet?.row, 0) + 1), 0)
  const computedCols = pallets.reduce((maxCols, pallet) => Math.max(maxCols, clampNonNegative(pallet?.col, 0) + 1), 0)
  const rows = Math.max(1, computedRows, fallbackRows)
  const cols = Math.max(1, computedCols, fallbackCols)
  const matrix = buildDefaultMatrix(rows, cols)

  pallets.forEach((pallet, index) => {
    const row = clampNonNegative(pallet?.row, Math.floor(index / cols)) % rows
    const col = clampNonNegative(pallet?.col, index % cols) % cols
    const type = normalizeType(pallet?.type)
    matrix[row][col] = {
      ...matrix[row][col],
      id: pallet?.id ?? `slot-${row}-${col}`,
      type,
      color: pallet?.color ?? TYPE_COLORS[type],
      label: pallet?.label ?? pallet?.product ?? `Pallet ${index + 1}`,
      product: pallet?.product ?? pallet?.label ?? null,
      x: toFiniteNumber(pallet?.x ?? col, col),
      y: toFiniteNumber(pallet?.y ?? 0, 0),
      z: toFiniteNumber(pallet?.z ?? row, row),
    }
  })

  return matrix
}

export function normalizeStopData(rawStop) {
  const rawMatrix =
    rawStop?.truck_state?.matrix ??
    rawStop?.matrix ??
    rawStop?.stopData?.truck_state?.matrix ??
    rawStop?.stopData?.matrix ??
    rawStop?.routeContext?.truck_state?.matrix

  const normalizedMatrixFromSource = normalizeMatrix(rawMatrix)
  const pallets = normalizePallets(rawStop)

  const rowCount = normalizedMatrixFromSource?.length ?? 2
  const colCount = normalizedMatrixFromSource?.[0]?.length ?? 4
  const matrix = Array.isArray(rawMatrix)
    ? normalizedMatrixFromSource
    : matrixFromPallets(pallets, rowCount, colCount)

  return {
    stopId: rawStop?.id ?? rawStop?.stopId ?? rawStop?.stopData?.stopId ?? rawStop?.stopData?.id ?? rawStop?.index ?? null,
    index: rawStop?.index ?? null,
    truckId: rawStop?.truckId ?? rawStop?.routeContext?.truck_id ?? rawStop?.routeContext?.truckId ?? null,
    address:
      rawStop?.point?.address ??
      rawStop?.address ??
      rawStop?.location?.address ??
      rawStop?.stopData?.location?.address ??
      'Delivery point',
    pallets,
    matrix,
  }
}
