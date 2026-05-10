"""Simulated annealing on visit order within each cluster.

Inputs are the clusters produced by `clustering.cluster_request` (or
`grid_search`). For each cluster, we optimize the order of stops to minimize
travel time, with soft penalties for time-window misses, capacity overflow
(both cells and kg, profiled along the route because of returnables) and
driver-shift overrun.

Move operators: 2-opt segment reversal, single-stop swap, or-opt relocation
of a 1-3 stop chunk. Cooling is geometric. Restart from best-so-far every
`RESTART_EVERY` iterations to escape basins that pure transposition can't
leave (the non-ergodicity flagged in interhack26.pdf).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from backend.obsolete.clustering import (
    Cluster, Depot, Driver, Fleet, Stop,
    cluster_request, grid_search, _format_cluster,
)
from travel_time import TravelMatrix

# Penalty weights (cost units = seconds-equivalent so everything is comparable
# to travel time).
LAMBDA_WINDOW = 5.0       # 1s late = 5s penalty
LAMBDA_CELLS = 60.0       # 1 cell over = 1 minute penalty
LAMBDA_KG = 6.0           # 1 kg over = 6s penalty
LAMBDA_SHIFT = 10.0       # 1s shift overrun = 10s penalty

# SA hyperparameters
N_ITERS = 8000
T0 = 600.0
TF = 0.5
RESTART_EVERY = 2000


@dataclass
class Route:
    stops: list[str]
    travel_time_s: float = 0.0
    total_time_s: float = 0.0          # depot-to-depot wall clock
    window_viol_s: float = 0.0
    cells_overflow: float = 0.0
    kg_overflow: float = 0.0
    shift_overrun_s: float = 0.0
    cost: float = 0.0
    feasible: bool = False
    arrival_times_s: list[float] = field(default_factory=list)


def evaluate_route(
    stop_ids: list[str],
    depot: Depot,
    fleet: Fleet,
    driver: Driver,
    stops_by_id: dict[str, Stop],
    matrix: TravelMatrix,
) -> Route:
    r = Route(stops=list(stop_ids))
    if not stop_ids:
        r.feasible = True
        return r

    depot_i = matrix.index_of("DEPOT")
    cur_cells = sum(stops_by_id[sid].delivery_cells for sid in stop_ids)
    cur_kg = sum(stops_by_id[sid].delivery_kg for sid in stop_ids)
    cells_over = max(0.0, cur_cells - fleet.capacity_cells)
    kg_over = max(0.0, cur_kg - fleet.capacity_kg)

    t = float(driver.shift_start_s)
    travel = 0.0
    prev_i = depot_i
    for sid in stop_ids:
        stop = stops_by_id[sid]
        cur_i = matrix.index_of(sid)
        leg = float(matrix.time_s[prev_i, cur_i])
        travel += leg
        t += leg
        if t > stop.t_close_s:
            r.window_viol_s += t - stop.t_close_s
        if t < stop.t_open_s:
            t = float(stop.t_open_s)
        r.arrival_times_s.append(t)
        t += stop.service_time_s
        cur_cells += stop.pickup_cells - stop.delivery_cells
        cur_kg += stop.pickup_kg - stop.delivery_kg
        cells_over = max(cells_over, cur_cells - fleet.capacity_cells)
        kg_over = max(kg_over, cur_kg - fleet.capacity_kg)
        prev_i = cur_i

    return_leg = float(matrix.time_s[prev_i, depot_i])
    travel += return_leg
    t += return_leg

    r.travel_time_s = travel
    r.total_time_s = t - driver.shift_start_s
    r.cells_overflow = max(0.0, cells_over)
    r.kg_overflow = max(0.0, kg_over)
    r.shift_overrun_s = max(0.0, t - driver.shift_end_s)
    r.cost = (
        travel
        + LAMBDA_WINDOW * r.window_viol_s
        + LAMBDA_CELLS * r.cells_overflow
        + LAMBDA_KG * r.kg_overflow
        + LAMBDA_SHIFT * r.shift_overrun_s
    )
    r.feasible = (
        r.window_viol_s == 0
        and r.cells_overflow == 0
        and r.kg_overflow == 0
        and r.shift_overrun_s == 0
    )
    return r


# --- Move operators -------------------------------------------------------

def _two_opt(stops: list[str], rng: random.Random) -> list[str]:
    n = len(stops)
    if n < 4:
        return stops
    i, j = sorted(rng.sample(range(n), 2))
    if j - i < 2:
        return stops
    return stops[:i] + stops[i:j + 1][::-1] + stops[j + 1:]


def _swap(stops: list[str], rng: random.Random) -> list[str]:
    n = len(stops)
    if n < 2:
        return stops
    i, j = rng.sample(range(n), 2)
    out = stops.copy()
    out[i], out[j] = out[j], out[i]
    return out


def _or_opt(stops: list[str], rng: random.Random) -> list[str]:
    """Remove a contiguous chunk of size 1..min(3, n-1) and reinsert it elsewhere."""
    n = len(stops)
    if n < 3:
        return stops
    chunk_len = rng.randint(1, min(3, n - 1))
    i = rng.randint(0, n - chunk_len)
    chunk = stops[i:i + chunk_len]
    remainder = stops[:i] + stops[i + chunk_len:]
    if not remainder:
        return stops
    j = rng.randint(0, len(remainder))
    return remainder[:j] + chunk + remainder[j:]


_MOVES = [_two_opt, _swap, _or_opt]


# --- SA core --------------------------------------------------------------

def sa_optimize_route(
    initial_stops: list[str],
    depot: Depot,
    fleet: Fleet,
    driver: Driver,
    stops_by_id: dict[str, Stop],
    matrix: TravelMatrix,
    n_iters: int = N_ITERS,
    t0: float = T0,
    tf: float = TF,
    restart_every: int = RESTART_EVERY,
    seed: int = 0,
) -> tuple[Route, list[float]]:
    rng = random.Random(seed)
    cur = evaluate_route(initial_stops, depot, fleet, driver, stops_by_id, matrix)
    best = cur
    history = [cur.cost]

    if len(initial_stops) < 2:
        return best, history

    log_ratio = math.log(tf / t0) if t0 > 0 else 0.0
    for k in range(n_iters):
        T = t0 * math.exp(log_ratio * (k / n_iters)) if t0 > 0 else 1e-9
        move = rng.choice(_MOVES)
        cand_stops = move(cur.stops, rng)
        if cand_stops == cur.stops:
            history.append(cur.cost)
            continue
        cand = evaluate_route(cand_stops, depot, fleet, driver, stops_by_id, matrix)
        delta = cand.cost - cur.cost
        if delta < 0 or rng.random() < math.exp(-delta / max(T, 1e-9)):
            cur = cand
            if cur.cost < best.cost:
                best = cur
        if restart_every and (k + 1) % restart_every == 0 and cur is not best:
            cur = best  # snap back to best-so-far, continue cooling
        history.append(cur.cost)
    return best, history


def sa_optimize_clusters(
    clusters: list[Cluster],
    depot: Depot,
    fleet: Fleet,
    drivers: list[Driver],
    stops_by_id: dict[str, Stop],
    matrix: TravelMatrix,
    n_iters: int = N_ITERS,
    seed: int = 0,
) -> list[tuple[Cluster, Route]]:
    out: list[tuple[Cluster, Route]] = []
    for c in clusters:
        seed_route = c.route_order if c.route_order else c.stop_ids
        best, _ = sa_optimize_route(
            seed_route, depot, fleet, drivers[c.van_idx], stops_by_id, matrix,
            n_iters=n_iters, seed=seed + c.van_idx,
        )
        # Mutate the cluster in place with the optimized order + metrics
        c.route_order = best.stops
        c.total_time_s = best.total_time_s
        c.feasible = best.feasible
        c.violations = []
        if best.window_viol_s > 0:
            c.violations.append(f"window_violation={best.window_viol_s/60:.1f}min")
        if best.cells_overflow > 0:
            c.violations.append(f"cells_overflow={best.cells_overflow:.0f}")
        if best.kg_overflow > 0:
            c.violations.append(f"kg_overflow={best.kg_overflow:.0f}kg")
        if best.shift_overrun_s > 0:
            c.violations.append(f"shift_overrun={best.shift_overrun_s/60:.1f}min")
        out.append((c, best))
    return out


# --- Pretty printer -------------------------------------------------------

def _format_route_diff(before: Cluster, after_route: Route, driver: Driver) -> str:
    head = (
        f"van {before.van_idx}: feasible={after_route.feasible}  "
        f"travel={after_route.travel_time_s/60:.1f}min  "
        f"total={after_route.total_time_s/3600:.2f}h"
    )
    arr_strs = []
    for sid, t_s in zip(after_route.stops, after_route.arrival_times_s):
        h, m = divmod(int(t_s) // 60, 60)
        arr_strs.append(f"{sid}@{h:02d}:{m:02d}")
    return head + "\n  route: " + " -> ".join(arr_strs)


# --- CLI ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    use_grid = "--grid" in sys.argv

    if use_grid:
        (wg, wm, ww), clusters, _ = grid_search(verbose=False)
        print(f"using grid-best weights w_geo={wg} w_tmid={wm} w_twidth={ww}")
        # rebuild ancillary state
        from backend.obsolete.clustering import load_problem, _matrix_for_problem
        depot, fleet, drivers, stops = load_problem()
        stops_by_id = {s.id: s for s in stops}
        matrix = _matrix_for_problem(depot, stops)
    else:
        clusters, depot, fleet, drivers, stops_by_id, matrix = cluster_request()

    print(f"\n=== before SA ===")
    for c in clusters:
        print(_format_cluster(c))

    optimized = sa_optimize_clusters(clusters, depot, fleet, drivers, stops_by_id, matrix)

    print(f"\n=== after SA ===")
    total_travel_before = 0.0
    total_travel_after = 0.0
    for c, route in optimized:
        # travel-only metric for the headline number
        total_travel_after += route.travel_time_s
        print(_format_route_diff(c, route, drivers[c.van_idx]))
    print(f"\ntotal fleet travel time after SA: {total_travel_after/60:.1f} min")
