"""Push solver output into Firestore so the frontend reads it live.

Document shape:

    routes/{driverId}
      driver_id, truck_id,
      truck_layout   {rows, cols}                pallet floor grid
      item_grid      {L, W, H}                   lattice cells (cube_size_m per van)
      items          [{position {x,y,z},
                       shape    {w_x, w_y, w_z},
                       stop_index, product_id, is_returnable}]
      points         [{lat, lng, address?}]      ordered route (depot at index 0)
      pallets        [{row, col, products[]}]    legacy floor-only view
      deliveries     [{pallet_positions[]}]      aligned with points
      windows        [{start, end}]
      service_times  [number]                    minutes per stop
      delivery_status, status

The Firebase Admin app is initialised lazily so the FastAPI process starts
fine without credentials (e.g. local solver-only dev). When the
`seed/service-account.json` is missing we log a warning and noop on writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).resolve().parent
_REPO = _BASE.parent
_DEFAULT_CRED = _REPO / "seed" / "service-account.json"

_db = None
_init_attempted = False


def _try_init() -> None:
    """Lazy init. Credential precedence:
      1. FIREBASE_SERVICE_ACCOUNT_JSON  — full JSON inline (best for serverless secrets)
      2. FIREBASE_SERVICE_ACCOUNT       — path to a service-account.json on disk
      3. seed/service-account.json      — legacy default for local dev
    """
    global _db, _init_attempted
    if _init_attempted:
        return
    _init_attempted = True

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError as exc:
        print(f"[firestore_writer] firebase_admin missing: {exc}")
        return

    cred = None
    inline = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if inline:
        try:
            cred = credentials.Certificate(json.loads(inline))
            print("[firestore_writer] loaded credentials from FIREBASE_SERVICE_ACCOUNT_JSON")
        except Exception as exc:  # noqa: BLE001
            print(f"[firestore_writer] FIREBASE_SERVICE_ACCOUNT_JSON parse failed: {exc}")

    if cred is None:
        cred_path = Path(os.environ.get("FIREBASE_SERVICE_ACCOUNT", _DEFAULT_CRED))
        if not cred_path.exists():
            print(f"[firestore_writer] no service account (env or {cred_path}) — writes disabled")
            return
        try:
            cred = credentials.Certificate(str(cred_path))
            print(f"[firestore_writer] loaded credentials from {cred_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"[firestore_writer] cert load failed: {exc}")
            return

    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        _db = firestore.client()
        print("[firestore_writer] firestore client initialised")
    except Exception as exc:  # noqa: BLE001
        print(f"[firestore_writer] init failed: {exc}")


def is_enabled() -> bool:
    _try_init()
    return _db is not None


def _hm(s: int) -> str:
    h, m = divmod(int(s) // 60, 60)
    return f"{h:02d}:{m:02d}"


def _van_layout(van_type: str) -> dict:
    """Pallet floor layout (rows × cols) for the visual grid."""
    return {"6_pallets": {"rows": 2, "cols": 3}, "8_pallets": {"rows": 2, "cols": 4}}.get(
        van_type, {"rows": 2, "cols": 3}
    )


def _van_lattice(van_type: str) -> tuple[int, int, int]:
    """Lattice dimensions in cells: each axis is van_dim_m / cube_size_m.

    cube_size_m and physical interior dimensions both live in vans.json — keep
    the source of truth there. Returns (L, W, H) where L is along the truck
    length, W its depth, H its stacking height.
    """
    data = json.loads((_BASE / "vans.json").read_text(encoding="utf-8"))
    spec = next((v for v in data["van_types"] if v["type"] == van_type), None)
    if spec is None:
        raise KeyError(f"unknown van_type: {van_type}")
    cube = spec["cube_size_m"]
    return (
        int(round(spec["length_m"] / cube)),
        int(round(spec["width_m"]  / cube)),
        int(round(spec["height_m"] / cube)),
    )


def _compute_item_layout(
    L: int, W: int, H: int,
    deliveries_per_stop: list[tuple[int, list[str]]],
    products: dict,
) -> tuple[list[dict], dict]:
    """Run SmartTruckOptimizer3D with real product shapes. Returns one entry
    per *item* (not per cell): each carries an anchor `position` + box `shape`
    so the frontend can render multi-cell products as single boxes.

    `deliveries_per_stop` is an ordered list of (stop_index, [product_id per
    unit]); the optimizer respects that route order so earlier stops are
    extractable first. Every unit of an `is_returnable` product is created as
    a return-type instance so the optimizer's EMPTY_KEG ablation models the
    space empties leave behind.
    """
    counts_per_stop: dict[int, dict[str, int]] = {}
    for stop_idx, units in deliveries_per_stop:
        if not units:
            continue
        bucket: dict[str, int] = {}
        for pid in units:
            bucket[pid] = bucket.get(pid, 0) + 1
        counts_per_stop[stop_idx] = bucket

    if not counts_per_stop:
        return [], {"L": L, "W": W, "H": H}

    needed_pids = {pid for counts in counts_per_stop.values() for pid in counts}
    item_shapes = {
        pid: (
            int(products[pid]["length_cells"]),
            int(products[pid]["width_cells"]),
            int(products[pid]["height_cells"]),
        )
        for pid in needed_pids
    }

    capacity = L * W * H
    total_cells = sum(
        item_shapes[pid][0] * item_shapes[pid][1] * item_shapes[pid][2] * cnt
        for counts in counts_per_stop.values()
        for pid, cnt in counts.items()
    )
    if total_cells > capacity:
        # OR-tools VRP capacity already enforces this; defensive scaling so the
        # lattice optimizer doesn't deadlock if a request slips through.
        scale = capacity / total_cells
        counts_per_stop = {
            stop_idx: {pid: max(1, int(cnt * scale)) for pid, cnt in counts.items()}
            for stop_idx, counts in counts_per_stop.items()
        }

    returns_per_stop = {
        stop_idx: {pid: cnt for pid, cnt in counts.items() if products[pid]["is_returnable"]}
        for stop_idx, counts in counts_per_stop.items()
    }

    route_ids = [stop_idx for stop_idx, _ in deliveries_per_stop if stop_idx in counts_per_stop]

    import numpy as np  # noqa: F401  (used by SmartTruckOptimizer3D)
    from optimize_box import SmartTruckOptimizer3D

    opt = SmartTruckOptimizer3D(L, W, H, route_ids, item_shapes=item_shapes)
    initial = opt.generate_initial_state(counts_per_stop, returns_per_stop)
    final, _, _ = opt.optimize(initial, steps=2000)
    opt._sync_instances_to_state(final)

    items: list[dict] = []
    for inst in opt._instances.values():
        x, y, z = inst.anchor
        lx, ly, lz = inst.shape
        items.append({
            "position": {"x": int(x), "y": int(y), "z": int(z)},
            "shape": {"w_x": int(lx), "w_y": int(ly), "w_z": int(lz)},
            "stop_index": int(inst.client),
            "product_id": inst.item_type,
            "is_returnable": bool(products[inst.item_type]["is_returnable"]),
        })
    return items, {"L": L, "W": W, "H": H}


def _pallets_for_stop(stop: dict, products: dict, capacity_cells: int = 9) -> list[list[dict]]:
    """Greedy packer: group a stop's deliveries into pallets capped at
    `capacity_cells` cube-units each. One pallet's `products` list is in the
    same shape the frontend already consumes."""
    items: list[tuple[str, int]] = []
    for line in stop.get("deliveries", []):
        pid = line["product_id"]
        cells = (
            products[pid]["length_cells"]
            * products[pid]["width_cells"]
            * products[pid]["height_cells"]
        )
        items.extend([(pid, cells)] * line["qty"])

    pallets: list[list[dict]] = []
    current: dict[str, int] = {}
    used = 0
    for pid, cells in items:
        if used + cells > capacity_cells and current:
            pallets.append([{"product_id": p, "quantity": q} for p, q in current.items()])
            current, used = {}, 0
        current[pid] = current.get(pid, 0) + 1
        used += cells
    if current:
        pallets.append([{"product_id": p, "quantity": q} for p, q in current.items()])
    return pallets


def build_route_doc(
    *,
    driver_id: str,
    truck_id: str,
    van_type: str,
    depot: dict,
    request_stops: list[dict],
    van_plan,
    service_times_s: dict[str, float],
) -> dict:
    """Translate one VanPlan into the Firestore route document shape."""
    layout = _van_layout(van_type)
    rows, cols = layout["rows"], layout["cols"]
    L, W, H = _van_lattice(van_type)
    products = {p["id"]: p for p in json.loads((_BASE / "products.json").read_text())["products"]}
    by_id = {s["id"]: s for s in request_stops}

    points: list[dict] = [
        {"lat": depot["coords"]["lat"], "lng": depot["coords"]["lng"], "address": depot.get("id", "Depot")}
    ]
    windows: list[dict] = [{"start": depot["open"], "end": depot["close"]}]
    service_times_min: list[float] = [0]
    pallets: list[dict] = []
    deliveries: list[dict] = [{"pallet_positions": []}]

    next_slot = 0  # row-major fill of the truck floor
    deliveries_per_stop: list[tuple[int, list[str]]] = []

    for stop_idx, sp in enumerate(van_plan.stops, start=1):
        s = by_id[sp.id]
        points.append({
            "lat": s["coords"]["lat"],
            "lng": s["coords"]["lng"],
            "address": s.get("address", s["id"]),
        })
        windows.append({"start": s["time_window"]["open"], "end": s["time_window"]["close"]})
        service_times_min.append(round(service_times_s.get(sp.id, 0) / 60, 1))

        stop_pallets = _pallets_for_stop(s, products)
        positions: list[dict] = []
        for pdata in stop_pallets:
            if next_slot >= rows * cols:
                break
            r, c = divmod(next_slot, cols)
            pallets.append({"row": r, "col": c, "products": pdata})
            positions.append({"row": r, "col": c})
            next_slot += 1
        deliveries.append({"pallet_positions": positions})

        units: list[str] = []
        for line in s.get("deliveries", []):
            units.extend([line["product_id"]] * line["qty"])
        deliveries_per_stop.append((stop_idx, units))

    items, item_grid = _compute_item_layout(L, W, H, deliveries_per_stop, products)

    return {
        "driver_id": driver_id,
        "truck_id": truck_id,
        "truck_layout": layout,
        "item_grid": item_grid,
        "items": items,
        "points": points,
        "pallets": pallets,
        "deliveries": deliveries,
        "windows": windows,
        "service_times": service_times_min,
        "delivery_status": ["pending"] * len(points),
        "status": "pending",
    }


def write_routes(docs: list[dict]) -> int:
    """Write a batch of route docs to `routes/{driver_id}`. Returns count
    written; 0 when Firestore is disabled."""
    if not is_enabled():
        return 0
    written = 0
    for doc in docs:
        _db.collection("routes").document(doc["driver_id"]).set(doc)
        written += 1
    return written
