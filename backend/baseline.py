"""Naive comparator: polar sweep + per-van nearest-neighbor.

Sort stops by angle from depot, round-robin assign to vans, then NN-order
each van's stops from the depot. No window or capacity guarantee — that's
the point. The OR-tools solver's "X% better" pitch number is computed
against this baseline.
"""

from __future__ import annotations

import math

from loader import Depot, Driver, Fleet, Stop
from solver import FleetPlan, StopPlan, VanPlan
from travel_time import TravelMatrix


def _polar(s: Stop, depot: Depot) -> float:
    return math.atan2(s.lat - depot.lat, s.lng - depot.lng)


def solve_baseline(
    depot: Depot,
    fleet: Fleet,
    drivers: list[Driver],
    stops: list[Stop],
    matrix: TravelMatrix,
) -> FleetPlan:
    if not stops:
        return FleetPlan([], 0, 0, True)

    K = fleet.num_vans
    swept = sorted(stops, key=lambda s: _polar(s, depot))
    assigned: list[list[Stop]] = [[] for _ in range(K)]
    for i, s in enumerate(swept):
        assigned[i % K].append(s)

    vans: list[VanPlan] = []
    fleet_drive = fleet_total = 0
    all_feas = True
    depot_i = matrix.index_of("DEPOT")

    for vid, van_stops in enumerate(assigned):
        order: list[Stop] = []
        remaining = {s.id: s for s in van_stops}
        cur = depot_i
        while remaining:
            nxt = min(
                remaining.values(),
                key=lambda s: float(matrix.time_s[cur, matrix.index_of(s.id)]),
            )
            order.append(nxt)
            cur = matrix.index_of(nxt.id)
            del remaining[nxt.id]

        # Simulate
        t = float(drivers[vid].shift_start_s)
        prev = depot_i
        plan: list[StopPlan] = []
        v_drive = cum_c = cum_k = peak_c = peak_k = 0
        late = False
        for seq, s in enumerate(order, start=1):
            i = matrix.index_of(s.id)
            travel = float(matrix.time_s[prev, i])
            v_drive += int(travel)
            t += travel
            if t < s.t_open_s:
                t = float(s.t_open_s)
            if t > s.t_close_s:
                late = True
            plan.append(StopPlan(seq, s.id, int(t)))
            t += s.service_time_s
            cum_c += s.delivery_cells + s.pickup_cells
            cum_k += int(round(s.delivery_kg + s.pickup_kg))
            peak_c = max(peak_c, cum_c)
            peak_k = max(peak_k, cum_k)
            prev = i
        ret = float(matrix.time_s[prev, depot_i])
        v_drive += int(ret)
        t += ret
        v_total = int(t - drivers[vid].shift_start_s)
        shift_ok = t <= drivers[vid].shift_end_s
        cap_ok = peak_c <= fleet.capacity_cells and peak_k <= fleet.capacity_kg
        feas = shift_ok and cap_ok and not late
        all_feas &= feas
        vans.append(VanPlan(
            van_idx=vid, driver_id=drivers[vid].id, stops=plan,
            travel_s=v_drive, total_s=v_total,
            peak_cells=peak_c, peak_kg=peak_k, feasible=feas,
        ))
        fleet_drive += v_drive
        fleet_total += v_total
    return FleetPlan(vans, fleet_drive, fleet_total, all_feas)
