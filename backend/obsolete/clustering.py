"""K-means warm-start cluster assignment with capacity + time-window feasibility.

Pipeline:
    1. Load sample_request.json + products.json + van_spec.txt.
    2. Compute per-stop service_time, delivery/pickup load (cells, kg).
    3. K-means over (lat, lng, t_mid, -t_width) with z-scored features and a
       tunable weight on the time axis. k = num_vans.
    4. For each cluster, run a depot-anchored nearest-neighbor route and check:
         - capacity profile (delivered down, picked-up up) <= van capacity
         - arrival time within each stop's window
         - return to depot within driver shift
    5. If any cluster is infeasible, rebalance by reassigning boundary stops to
       the nearest feasible neighbor cluster.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from travel_time import TravelMatrix, get_or_build_matrix

DATA_DIR = Path(__file__).with_name("data")
DEFAULT_REQUEST = DATA_DIR / "sample_request.json"
DEFAULT_PRODUCTS = DATA_DIR / "products.json"
DEFAULT_VANS = DATA_DIR / "vans.json"

# Per-cell unload effort and a fixed parking/paperwork buffer (seconds).
SERVICE_PER_CELL_S = 10.0
SERVICE_BASE_S = 180.0

# K-means feature weights. Geography dominates; time-window midpoint pulls
# stops together when their windows overlap; width is a soft anti-pull
# (narrow windows cluster more aggressively). Tunable via grid_search().
W_GEO = 1.5
W_TMID = 1.0
W_TWIDTH = 0.4

# Default sweep ranges for grid_search().
GRID_W_GEO = (0.5, 1.0, 1.5, 2.0, 3.0)
GRID_W_TMID = (0.0, 0.5, 1.0, 1.5, 2.0)
GRID_W_TWIDTH = (0.0, 0.4, 1.0)


@dataclass
class Stop:
    id: str
    lat: float
    lng: float
    t_open_s: int       # seconds since midnight
    t_close_s: int
    delivery_cells: int
    delivery_kg: float
    pickup_cells: int
    pickup_kg: float
    service_time_s: float


@dataclass
class Depot:
    id: str
    lat: float
    lng: float
    open_s: int
    close_s: int


@dataclass
class Driver:
    id: str
    shift_start_s: int
    shift_end_s: int


@dataclass
class Fleet:
    num_vans: int
    capacity_kg: float
    capacity_cells: int


@dataclass
class Cluster:
    van_idx: int
    stop_ids: list[str]
    feasible: bool = False
    violations: list[str] = field(default_factory=list)
    route_order: list[str] = field(default_factory=list)
    total_time_s: float = 0.0
    peak_cells: int = 0
    peak_kg: float = 0.0


# --- Loaders ---------------------------------------------------------------

def _hms_to_seconds(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 3600 + int(m) * 60


def _van_type_spec(vans_path: Path, van_type: str) -> dict:
    data = json.loads(Path(vans_path).read_text(encoding="utf-8"))
    for vt in data["van_types"]:
        if vt["type"] == van_type:
            return vt
    raise KeyError(f"van_type {van_type!r} not found in {vans_path}")


def _van_capacity_cells(van_spec: dict) -> int:
    cube = van_spec["cube_size_m"]
    nx_ = int(round(van_spec["length_m"] / cube))
    ny_ = int(round(van_spec["width_m"] / cube))
    nz_ = int(round(van_spec["height_m"] / cube))
    return nx_ * ny_ * nz_


def _product_index(products_path: Path) -> dict[str, dict]:
    data = json.loads(Path(products_path).read_text(encoding="utf-8"))
    return {p["id"]: p for p in data["products"]}


def _line_cells(p: dict, qty: int) -> int:
    return p["length_cells"] * p["width_cells"] * p["height_cells"] * qty


def load_problem_from_dict(
    req: dict,
    products_path: Path | str = DEFAULT_PRODUCTS,
    vans_path: Path | str = DEFAULT_VANS,
) -> tuple[Depot, Fleet, list[Driver], list[Stop]]:
    products = _product_index(Path(products_path))
    van_spec = _van_type_spec(Path(vans_path), req["fleet"]["van_type"])

    depot = Depot(
        id=req["depot"]["id"],
        lat=req["depot"]["coords"]["lat"],
        lng=req["depot"]["coords"]["lng"],
        open_s=_hms_to_seconds(req["depot"]["open"]),
        close_s=_hms_to_seconds(req["depot"]["close"]),
    )
    fleet = Fleet(
        num_vans=req["fleet"]["num_vans"],
        capacity_kg=float(van_spec["max_payload_kg"]),
        capacity_cells=_van_capacity_cells(van_spec),
    )
    drivers = [
        Driver(
            id=d["id"],
            shift_start_s=_hms_to_seconds(d["shift_start"]),
            shift_end_s=_hms_to_seconds(d["shift_end"]),
        )
        for d in req["drivers"]
    ]

    stops: list[Stop] = []
    for s in req["stops"]:
        d_cells = sum(_line_cells(products[ln["product_id"]], ln["qty"]) for ln in s["deliveries"])
        d_kg = sum(products[ln["product_id"]]["weight_kg"] * ln["qty"] for ln in s["deliveries"])
        p_cells = sum(_line_cells(products[ln["product_id"]], ln["qty"]) for ln in s.get("pickups", []))
        p_kg = sum(products[ln["product_id"]]["weight_kg"] * ln["qty"] for ln in s.get("pickups", []))
        service = SERVICE_BASE_S + SERVICE_PER_CELL_S * (d_cells + p_cells)
        stops.append(
            Stop(
                id=s["id"],
                lat=s["coords"]["lat"],
                lng=s["coords"]["lng"],
                t_open_s=_hms_to_seconds(s["time_window"]["open"]),
                t_close_s=_hms_to_seconds(s["time_window"]["close"]),
                delivery_cells=d_cells,
                delivery_kg=d_kg,
                pickup_cells=p_cells,
                pickup_kg=p_kg,
                service_time_s=service,
            )
        )
    return depot, fleet, drivers, stops


def load_problem(
    request_path: Path | str = DEFAULT_REQUEST,
    products_path: Path | str = DEFAULT_PRODUCTS,
    vans_path: Path | str = DEFAULT_VANS,
) -> tuple[Depot, Fleet, list[Driver], list[Stop]]:
    req = json.loads(Path(request_path).read_text(encoding="utf-8"))
    return load_problem_from_dict(req, products_path, vans_path)


# --- Travel matrix scoped to this request ---------------------------------

def _matrix_for_problem(
    depot: Depot,
    stops: list[Stop],
    use_disk_cache: bool = True,
) -> TravelMatrix:
    points = [("DEPOT", depot.lat, depot.lng)] + [(s.id, s.lat, s.lng) for s in stops]
    if not use_disk_cache:
        from travel_time import build_matrix
        from graph_manager import get_or_build_graph
        return build_matrix(get_or_build_graph(), points)
    return get_or_build_matrix(points, path=Path(__file__).with_name("travel_time_request.npz"))


# --- K-means clustering ---------------------------------------------------

def _features(
    stops: list[Stop],
    w_geo: float = W_GEO,
    w_tmid: float = W_TMID,
    w_twidth: float = W_TWIDTH,
) -> np.ndarray:
    raw = np.array(
        [[s.lat, s.lng, (s.t_open_s + s.t_close_s) / 2.0, s.t_close_s - s.t_open_s] for s in stops],
        dtype=np.float64,
    )
    z = StandardScaler().fit_transform(raw)
    z[:, 0:2] *= w_geo
    z[:, 2] *= w_tmid
    # Invert width so narrow windows (small width) get a LARGER feature value
    # and pull toward distinct clusters; wide windows go to the mean.
    z[:, 3] *= -w_twidth
    return z


def _kmeans(
    stops: list[Stop],
    k: int,
    w_geo: float = W_GEO,
    w_tmid: float = W_TMID,
    w_twidth: float = W_TWIDTH,
    n_init: int = 30,
    seed: int = 42,
) -> np.ndarray:
    feats = _features(stops, w_geo, w_tmid, w_twidth)
    km = KMeans(n_clusters=k, n_init=n_init, random_state=seed)
    return km.fit_predict(feats)


# --- Feasibility ----------------------------------------------------------

def _nn_route(stop_ids: list[str], matrix: TravelMatrix) -> list[str]:
    """Greedy nearest-neighbor route from depot, returning ordered stop ids."""
    if not stop_ids:
        return []
    depot_i = matrix.index_of("DEPOT")
    remaining = set(stop_ids)
    cur = depot_i
    order: list[str] = []
    while remaining:
        nxt = min(remaining, key=lambda sid: matrix.time_s[cur, matrix.index_of(sid)])
        order.append(nxt)
        cur = matrix.index_of(nxt)
        remaining.remove(nxt)
    return order


def evaluate_cluster(
    stop_ids: list[str],
    depot: Depot,
    fleet: Fleet,
    driver: Driver,
    stops_by_id: dict[str, Stop],
    matrix: TravelMatrix,
) -> Cluster:
    cluster = Cluster(van_idx=-1, stop_ids=list(stop_ids))
    if not stop_ids:
        cluster.feasible = True
        return cluster

    cluster.route_order = _nn_route(stop_ids, matrix)
    depot_i = matrix.index_of("DEPOT")

    # Initial load = sum of deliveries (everything is on the truck at depot).
    cur_cells = sum(stops_by_id[sid].delivery_cells for sid in stop_ids)
    cur_kg = sum(stops_by_id[sid].delivery_kg for sid in stop_ids)
    cluster.peak_cells = cur_cells
    cluster.peak_kg = cur_kg

    if cur_cells > fleet.capacity_cells:
        cluster.violations.append(
            f"initial cells {cur_cells} > capacity {fleet.capacity_cells}"
        )
    if cur_kg > fleet.capacity_kg:
        cluster.violations.append(
            f"initial kg {cur_kg:.0f} > capacity {fleet.capacity_kg:.0f}"
        )

    t = float(driver.shift_start_s)
    prev_i = depot_i
    for sid in cluster.route_order:
        stop = stops_by_id[sid]
        cur_i = matrix.index_of(sid)
        t += float(matrix.time_s[prev_i, cur_i])
        if t > stop.t_close_s:
            cluster.violations.append(
                f"{sid}: arrived {t/3600:.2f}h > close {stop.t_close_s/3600:.2f}h"
            )
        if t < stop.t_open_s:
            t = float(stop.t_open_s)  # wait until window opens
        t += stop.service_time_s

        # After service: deliveries leave the van, pickups enter.
        cur_cells += stop.pickup_cells - stop.delivery_cells
        cur_kg += stop.pickup_kg - stop.delivery_kg
        cluster.peak_cells = max(cluster.peak_cells, cur_cells)
        cluster.peak_kg = max(cluster.peak_kg, cur_kg)
        if cur_cells > fleet.capacity_cells:
            cluster.violations.append(
                f"{sid}: cells {cur_cells} > capacity {fleet.capacity_cells}"
            )
        if cur_kg > fleet.capacity_kg:
            cluster.violations.append(
                f"{sid}: kg {cur_kg:.0f} > capacity {fleet.capacity_kg:.0f}"
            )
        prev_i = cur_i

    t += float(matrix.time_s[prev_i, depot_i])
    cluster.total_time_s = t - driver.shift_start_s
    if t > driver.shift_end_s:
        cluster.violations.append(
            f"return {t/3600:.2f}h > shift_end {driver.shift_end_s/3600:.2f}h"
        )

    cluster.feasible = not cluster.violations
    return cluster


# --- Rebalancing ----------------------------------------------------------

def rebalance(
    clusters: list[Cluster],
    depot: Depot,
    fleet: Fleet,
    drivers: list[Driver],
    stops_by_id: dict[str, Stop],
    matrix: TravelMatrix,
    max_iters: int = 50,
) -> list[Cluster]:
    """Greedy: move a boundary stop from each infeasible cluster to its best
    feasible neighbor. Boundary = stop whose closest centroid (in travel-time
    space, summed to all other-cluster members) is in another cluster."""
    for _ in range(max_iters):
        infeasible = [c for c in clusters if not c.feasible]
        if not infeasible:
            return clusters
        moved = False
        for src in infeasible:
            if not src.stop_ids:
                continue
            # Pick the stop in src that's "most attracted" to another cluster
            # i.e. minimizes mean travel time to that other cluster's stops.
            best_move: tuple[str, int, float] | None = None
            for sid in src.stop_ids:
                i = matrix.index_of(sid)
                for dst_idx, dst in enumerate(clusters):
                    if dst is src:
                        continue
                    if not dst.stop_ids:
                        mean_t = float(matrix.time_s[i, matrix.index_of("DEPOT")])
                    else:
                        mean_t = float(np.mean(
                            [matrix.time_s[i, matrix.index_of(t)] for t in dst.stop_ids]
                        ))
                    if best_move is None or mean_t < best_move[2]:
                        best_move = (sid, dst_idx, mean_t)
            if best_move is None:
                continue
            sid, dst_idx, _ = best_move
            new_src_ids = [s for s in src.stop_ids if s != sid]
            new_dst_ids = clusters[dst_idx].stop_ids + [sid]
            new_src = evaluate_cluster(
                new_src_ids, depot, fleet, drivers[src.van_idx], stops_by_id, matrix
            )
            new_dst = evaluate_cluster(
                new_dst_ids, depot, fleet, drivers[dst_idx], stops_by_id, matrix
            )
            # Only accept if dst stays feasible AND src strictly improves
            # (fewer violations or fewer stops while still infeasible).
            if new_dst.feasible and (
                new_src.feasible
                or len(new_src.violations) < len(src.violations)
            ):
                new_src.van_idx = src.van_idx
                new_dst.van_idx = dst_idx
                clusters[src.van_idx] = new_src
                clusters[dst_idx] = new_dst
                moved = True
                break
        if not moved:
            break
    return clusters


# --- Top-level entry point ------------------------------------------------

def _cluster_with_weights(
    stops: list[Stop],
    depot: Depot,
    fleet: Fleet,
    drivers: list[Driver],
    stops_by_id: dict[str, Stop],
    matrix: TravelMatrix,
    w_geo: float,
    w_tmid: float,
    w_twidth: float,
    seed: int = 42,
) -> list[Cluster]:
    labels = _kmeans(stops, k=fleet.num_vans, w_geo=w_geo, w_tmid=w_tmid, w_twidth=w_twidth, seed=seed)
    clusters: list[Cluster] = []
    for v in range(fleet.num_vans):
        ids = [s.id for s, lbl in zip(stops, labels) if lbl == v]
        c = evaluate_cluster(ids, depot, fleet, drivers[v], stops_by_id, matrix)
        c.van_idx = v
        clusters.append(c)
    return rebalance(clusters, depot, fleet, drivers, stops_by_id, matrix)


def _fleet_cost(clusters: list[Cluster]) -> tuple[int, float, float]:
    """Sort key for picking the best clustering.

    1. fewest infeasible clusters (lower is better)
    2. lowest total route time across the fleet
    3. lowest peak-load imbalance (max - min cells across vans)
    """
    n_infeasible = sum(0 if c.feasible else 1 for c in clusters)
    total_time = sum(c.total_time_s for c in clusters)
    peaks = [c.peak_cells for c in clusters if c.stop_ids]
    imbalance = (max(peaks) - min(peaks)) if peaks else 0
    return (n_infeasible, total_time, float(imbalance))


def grid_search(
    request_path: Path | str = DEFAULT_REQUEST,
    w_geo_grid: tuple[float, ...] = GRID_W_GEO,
    w_tmid_grid: tuple[float, ...] = GRID_W_TMID,
    w_twidth_grid: tuple[float, ...] = GRID_W_TWIDTH,
    verbose: bool = True,
) -> tuple[tuple[float, float, float], list[Cluster], list[dict]]:
    """Sweep weight combos and return the (best_weights, best_clusters, full_log)."""
    depot, fleet, drivers, stops = load_problem(request_path)
    stops_by_id = {s.id: s for s in stops}
    matrix = _matrix_for_problem(depot, stops)

    best_key: tuple[int, float, float] | None = None
    best_weights: tuple[float, float, float] | None = None
    best_clusters: list[Cluster] | None = None
    log: list[dict] = []

    for wg in w_geo_grid:
        for wm in w_tmid_grid:
            for ww in w_twidth_grid:
                clusters = _cluster_with_weights(
                    stops, depot, fleet, drivers, stops_by_id, matrix, wg, wm, ww
                )
                key = _fleet_cost(clusters)
                log.append({
                    "w_geo": wg, "w_tmid": wm, "w_twidth": ww,
                    "infeasible": key[0],
                    "total_time_h": key[1] / 3600,
                    "imbalance_cells": int(key[2]),
                })
                if best_key is None or key < best_key:
                    best_key = key
                    best_weights = (wg, wm, ww)
                    best_clusters = [Cluster(**vars(c)) for c in clusters]
    if verbose:
        print(f"grid: {len(log)} combos tried, best={best_weights} key={best_key}")
    assert best_weights is not None and best_clusters is not None
    return best_weights, best_clusters, log


def cluster_request(
    request_path: Path | str = DEFAULT_REQUEST,
    w_geo: float | None = None,
    w_tmid: float | None = None,
    w_twidth: float | None = None,
) -> tuple[list[Cluster], Depot, Fleet, list[Driver], dict[str, Stop], TravelMatrix]:
    depot, fleet, drivers, stops = load_problem(request_path)
    stops_by_id = {s.id: s for s in stops}
    matrix = _matrix_for_problem(depot, stops)

    clusters = _cluster_with_weights(
        stops, depot, fleet, drivers, stops_by_id, matrix,
        w_geo if w_geo is not None else W_GEO,
        w_tmid if w_tmid is not None else W_TMID,
        w_twidth if w_twidth is not None else W_TWIDTH,
    )
    return clusters, depot, fleet, drivers, stops_by_id, matrix


def _format_cluster(c: Cluster) -> str:
    head = (
        f"van {c.van_idx}: {len(c.stop_ids)} stops  "
        f"feasible={c.feasible}  "
        f"peak_cells={c.peak_cells}  peak_kg={c.peak_kg:.0f}  "
        f"total={c.total_time_s/3600:.2f}h"
    )
    body = "\n  route: " + " -> ".join(c.route_order) if c.route_order else ""
    viol = ""
    if c.violations:
        viol = "\n  violations:\n    " + "\n    ".join(c.violations)
    return head + body + viol


if __name__ == "__main__":
    import sys
    do_grid = "--grid" in sys.argv

    if do_grid:
        (wg, wm, ww), clusters, log = grid_search()
        print(f"\nbest weights: w_geo={wg} w_tmid={wm} w_twidth={ww}")
        # Show top 5 combos
        log_sorted = sorted(log, key=lambda r: (r["infeasible"], r["total_time_h"], r["imbalance_cells"]))
        print("\ntop 5 combos:")
        print(f"{'w_geo':>6} {'w_tmid':>7} {'w_twidth':>9} {'infeas':>7} {'time_h':>7} {'imbal':>6}")
        for r in log_sorted[:5]:
            print(
                f"{r['w_geo']:6.2f} {r['w_tmid']:7.2f} {r['w_twidth']:9.2f} "
                f"{r['infeasible']:7d} {r['total_time_h']:7.2f} {r['imbalance_cells']:6d}"
            )
        print()
    else:
        clusters, depot, fleet, _, _, _ = cluster_request()
        print(
            f"depot={depot.id}  vans={fleet.num_vans}  "
            f"capacity={fleet.capacity_kg:.0f}kg / {fleet.capacity_cells} cells\n"
        )

    for c in clusters:
        print(_format_cluster(c))
        print()
