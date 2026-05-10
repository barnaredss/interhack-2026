"""KPIs, explanations, load profile, warehouse-staging recommendations.

Everything here turns a `FleetPlan` from `solver.py` into the human-readable
add-ons that make the demo pitch land:
  - per-van capacity-over-time profile (returnables visualization)
  - per-stop arrival reasons
  - fleet-level KPIs vs the naive baseline (savings %, CO2, utilization)
  - warehouse staging recommendations for warehouse prep
"""

from __future__ import annotations

from dataclasses import dataclass

from loader import Depot, Driver, Fleet, Stop
from solver import FleetPlan, VanPlan
from travel_time import TravelMatrix


# UK DEFRA factor for diesel light commercial vehicle, kg CO2 per km.
CO2_KG_PER_KM = 0.20


@dataclass
class LoadPoint:
    after_stop: str
    cells: int
    kg: int


@dataclass
class KPIs:
    fleet_drive_min: float
    baseline_drive_min: float
    savings_pct: float
    fleet_km: float
    baseline_km: float
    co2_kg_saved: float
    driver_utilization_pct: float
    capacity_utilization_pct: float
    stops_per_van: list[int]
    feasible_vans: int
    total_vans: int


def _hm(s: int) -> str:
    h, m = divmod(int(s) // 60, 60)
    return f"{h:02d}:{m:02d}"


def load_profile(van: VanPlan, stops_by_id: dict[str, Stop]) -> list[LoadPoint]:
    """Truck departs depot fully loaded with all deliveries; load decreases as
    we drop off and increases as we collect empties."""
    if not van.stops:
        return []
    cells = sum(stops_by_id[p.id].delivery_cells for p in van.stops)
    kg = sum(stops_by_id[p.id].delivery_kg for p in van.stops)
    profile = [LoadPoint("DEPOT", int(cells), int(round(kg)))]
    for p in van.stops:
        s = stops_by_id[p.id]
        cells = cells - s.delivery_cells + s.pickup_cells
        kg = kg - s.delivery_kg + s.pickup_kg
        profile.append(LoadPoint(p.id, int(cells), int(round(kg))))
    return profile


def explain_van(van: VanPlan, stops_by_id: dict[str, Stop], driver: Driver) -> list[str]:
    """One line per visited stop, naming the driver-readable reason."""
    out: list[str] = []
    if not van.stops:
        out.append(f"{driver.id} idle (no stops in this run).")
        return out

    first = van.stops[0]
    s_first = stops_by_id[first.id]
    idle_min = max(0, (first.arrival_s - driver.shift_start_s) // 60)
    win_w_min = (s_first.t_close_s - s_first.t_open_s) // 60
    if idle_min > 30:
        out.append(
            f"{driver.id} departs {_hm(driver.shift_start_s)}, parks at {first.id} "
            f"by {_hm(first.arrival_s)} — its {win_w_min}min window opens at "
            f"{_hm(s_first.t_open_s)}, so the truck waits then unloads at the open."
        )
    else:
        out.append(
            f"{driver.id} hits {first.id} at {_hm(first.arrival_s)} — "
            f"first stop is the closest with an open window."
        )

    for prev_p, cur_p in zip(van.stops, van.stops[1:]):
        s_cur = stops_by_id[cur_p.id]
        slack_min = max(0, (s_cur.t_close_s - cur_p.arrival_s) // 60)
        out.append(
            f"  → {cur_p.id} at {_hm(cur_p.arrival_s)}, "
            f"{slack_min}min before window {_hm(s_cur.t_close_s)} closes."
        )

    out.append(
        f"  → returns to depot. Peak load {van.peak_cells} cells / "
        f"{van.peak_kg} kg ({van.travel_s // 60} min driving)."
    )
    return out


def _van_distance_km(van: VanPlan, matrix: TravelMatrix) -> float:
    if not van.stops:
        return 0.0
    depot_i = matrix.index_of("DEPOT")
    total_m = 0.0
    prev = depot_i
    for sp in van.stops:
        i = matrix.index_of(sp.id)
        total_m += float(matrix.dist_m[prev, i])
        prev = i
    total_m += float(matrix.dist_m[prev, depot_i])
    return total_m / 1000.0


def compute_kpis(
    plan: FleetPlan,
    baseline: FleetPlan,
    fleet: Fleet,
    drivers: list[Driver],
    matrix: TravelMatrix,
) -> KPIs:
    drive_min = plan.drive_s / 60
    base_drive_min = baseline.drive_s / 60
    savings = (
        ((base_drive_min - drive_min) / base_drive_min * 100)
        if base_drive_min > 0 else 0.0
    )

    fleet_km = sum(_van_distance_km(v, matrix) for v in plan.vans)
    base_km = sum(_van_distance_km(v, matrix) for v in baseline.vans)
    co2_saved = max(0.0, (base_km - fleet_km) * CO2_KG_PER_KM)

    busy = sum(v.total_s for v in plan.vans)
    shift = sum(d.shift_end_s - d.shift_start_s for d in drivers)
    driver_util = (busy / shift * 100) if shift > 0 else 0.0

    used = [v for v in plan.vans if v.stops]
    cap_util = (
        sum(v.peak_cells / fleet.capacity_cells for v in used) / len(used) * 100
        if used else 0.0
    )

    return KPIs(
        fleet_drive_min=round(drive_min, 2),
        baseline_drive_min=round(base_drive_min, 2),
        savings_pct=round(savings, 1),
        fleet_km=round(fleet_km, 2),
        baseline_km=round(base_km, 2),
        co2_kg_saved=round(co2_saved, 2),
        driver_utilization_pct=round(driver_util, 1),
        capacity_utilization_pct=round(cap_util, 1),
        stops_per_van=[len(v.stops) for v in plan.vans],
        feasible_vans=sum(1 for v in plan.vans if v.feasible),
        total_vans=len(plan.vans),
    )


def warehouse_prep(
    plan: FleetPlan,
    request_stops: list[dict],
    stops_by_id: dict[str, Stop],
) -> list[str]:
    """Per-van staging hints, derived from each van's actual delivery list."""
    recs: list[str] = []
    by_id = {s["id"]: s for s in request_stops}
    for v in plan.vans:
        if not v.stops:
            continue
        sku_qty: dict[str, int] = {}
        total_returnable = 0
        for sp in v.stops:
            req = by_id[sp.id]
            for line in req.get("deliveries", []):
                sku_qty[line["product_id"]] = sku_qty.get(line["product_id"], 0) + line["qty"]
            for line in req.get("pickups", []):
                total_returnable += line["qty"]
        top = sorted(sku_qty.items(), key=lambda x: -x[1])[:3]
        recs.append(
            f"{v.driver_id}: stage "
            f"{', '.join(f'{q}× {p}' for p, q in top)} "
            f"({sum(sku_qty.values())} units total)"
        )
        if total_returnable:
            first = stops_by_id[v.stops[0].id]
            recs.append(
                f"  reserve ~{first.pickup_cells} returnable cells near {v.driver_id}'s door "
                f"(pickups start at {v.stops[0].id})"
            )
    return recs
