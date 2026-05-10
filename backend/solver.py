"""OR-tools VRPSPD-TW solver.

One-shot solve: capacity (cells + kg), per-stop time windows, per-driver
shifts, simultaneous pickup-delivery, single depot.

Capacity model is the conservative VRPSPD bound:
    sum(delivery_cells + pickup_cells on route) <= van_capacity_cells
    sum(delivery_kg    + pickup_kg    on route) <= van_capacity_kg

This bounds the *peak* in-truck load by the *sum* of inflow + outflow on the
route. Slightly suboptimal vs. the exact load-profile model but correct
(never accepts an infeasible plan) and fast.

Objective: minimize total travel time across the fleet.
Time window + shift constraints are HARD (set via SetRange).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from loader import Depot, Driver, Fleet, Stop
from travel_time import TravelMatrix


@dataclass
class StopPlan:
    sequence: int
    id: str
    arrival_s: int


@dataclass
class VanPlan:
    van_idx: int
    driver_id: str
    stops: list[StopPlan]
    travel_s: int
    total_s: int          # depot-to-depot wall clock incl. waits + service
    peak_cells: int
    peak_kg: int
    feasible: bool
    violations: list[str] = field(default_factory=list)


@dataclass
class FleetPlan:
    vans: list[VanPlan]
    drive_s: int
    total_s: int
    all_feasible: bool


def solve_vrp(
    depot: Depot,
    fleet: Fleet,
    drivers: list[Driver],
    stops: list[Stop],
    matrix: TravelMatrix,
    time_limit_s: int = 5,
) -> FleetPlan:
    """Index 0 = depot, 1..n = stops in input order."""
    n = len(stops) + 1
    K = fleet.num_vans
    H = 24 * 3600

    manager = pywrapcp.RoutingIndexManager(n, K, 0)
    routing = pywrapcp.RoutingModel(manager)

    # Arc cost = pure travel time (objective).
    def arc_cb(fi, ti):
        i, j = manager.IndexToNode(fi), manager.IndexToNode(ti)
        return int(matrix.time_s[i, j])
    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(arc_cb))

    # Time dimension = travel + service at "from" node.
    def time_cb(fi, ti):
        i, j = manager.IndexToNode(fi), manager.IndexToNode(ti)
        s = stops[i - 1].service_time_s if i >= 1 else 0
        return int(matrix.time_s[i, j] + s)
    time_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(time_idx, H, H, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    for vid, d in enumerate(drivers):
        time_dim.CumulVar(routing.Start(vid)).SetRange(d.shift_start_s, d.shift_end_s)
        time_dim.CumulVar(routing.End(vid)).SetRange(d.shift_start_s, d.shift_end_s)
    for i, s in enumerate(stops):
        time_dim.CumulVar(manager.NodeToIndex(i + 1)).SetRange(s.t_open_s, s.t_close_s)

    # Capacity: cells.
    def cells_cb(fi):
        i = manager.IndexToNode(fi)
        return 0 if i == 0 else stops[i - 1].delivery_cells + stops[i - 1].pickup_cells
    routing.AddDimensionWithVehicleCapacity(
        routing.RegisterUnaryTransitCallback(cells_cb),
        0, [fleet.capacity_cells] * K, True, "Cells",
    )

    # Capacity: kg.
    def kg_cb(fi):
        i = manager.IndexToNode(fi)
        return 0 if i == 0 else int(round(stops[i - 1].delivery_kg + stops[i - 1].pickup_kg))
    routing.AddDimensionWithVehicleCapacity(
        routing.RegisterUnaryTransitCallback(kg_cb),
        0, [int(fleet.capacity_kg)] * K, True, "Kg",
    )

    sp = pywrapcp.DefaultRoutingSearchParameters()
    sp.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    sp.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    sp.time_limit.seconds = int(time_limit_s)

    sol = routing.SolveWithParameters(sp)
    if sol is None:
        return FleetPlan([], 0, 0, False)

    vans: list[VanPlan] = []
    fleet_drive = fleet_total = 0
    all_feas = True
    for vid in range(K):
        idx = routing.Start(vid)
        plan: list[StopPlan] = []
        seq = peak_c = peak_k = cum_c = cum_k = v_drive = 0
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node != 0:
                seq += 1
                arr = sol.Value(time_dim.CumulVar(idx))
                s = stops[node - 1]
                cum_c += s.delivery_cells + s.pickup_cells
                cum_k += int(round(s.delivery_kg + s.pickup_kg))
                peak_c = max(peak_c, cum_c)
                peak_k = max(peak_k, cum_k)
                plan.append(StopPlan(seq, s.id, int(arr)))
            nxt = sol.Value(routing.NextVar(idx))
            v_drive += int(matrix.time_s[manager.IndexToNode(idx), manager.IndexToNode(nxt)])
            idx = nxt
        end_t = sol.Value(time_dim.CumulVar(routing.End(vid)))
        v_total = end_t - drivers[vid].shift_start_s
        feas = v_total <= drivers[vid].shift_end_s - drivers[vid].shift_start_s
        vans.append(VanPlan(
            van_idx=vid,
            driver_id=drivers[vid].id,
            stops=plan,
            travel_s=v_drive,
            total_s=v_total,
            peak_cells=peak_c,
            peak_kg=peak_k,
            feasible=feas,
        ))
        fleet_drive += v_drive
        fleet_total += v_total
        all_feas &= feas
    return FleetPlan(vans, fleet_drive, fleet_total, all_feas)
