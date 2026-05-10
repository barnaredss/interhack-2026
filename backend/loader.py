"""Parse a request dict + product/van catalogues into solver inputs.

The shape of the request dict matches `sample_request.json`. The catalogues
(`products.json`, `vans.json`) are read from disk lazily — keeps startup
cheap and avoids globals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_BASE = Path(__file__).parent
DEFAULT_PRODUCTS = _BASE / "products.json"
DEFAULT_VANS = _BASE / "vans.json"

SERVICE_BASE_S = 180.0          # parking + paperwork buffer per stop
SERVICE_PER_CELL_S = 10.0       # crate-handling time per unit cell


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
class Stop:
    id: str
    lat: float
    lng: float
    t_open_s: int
    t_close_s: int
    delivery_cells: int
    delivery_kg: float
    pickup_cells: int
    pickup_kg: float
    service_time_s: float


def _hms(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 3600 + int(m) * 60


def _van_capacity_cells(spec: dict) -> int:
    cube = spec["cube_size_m"]
    return (
        int(round(spec["length_m"] / cube))
        * int(round(spec["width_m"] / cube))
        * int(round(spec["height_m"] / cube))
    )


def _van_spec(vans_path: Path, van_type: str) -> dict:
    data = json.loads(vans_path.read_text(encoding="utf-8"))
    for v in data["van_types"]:
        if v["type"] == van_type:
            return v
    raise KeyError(f"unknown van_type: {van_type}")


def _line_cells(p: dict, qty: int) -> int:
    return p["length_cells"] * p["width_cells"] * p["height_cells"] * qty


def load(
    req: dict,
    products_path: Path = DEFAULT_PRODUCTS,
    vans_path: Path = DEFAULT_VANS,
) -> tuple[Depot, Fleet, list[Driver], list[Stop]]:
    products = {
        p["id"]: p
        for p in json.loads(products_path.read_text(encoding="utf-8"))["products"]
    }
    spec = _van_spec(vans_path, req["fleet"]["van_type"])

    depot = Depot(
        id=req["depot"]["id"],
        lat=req["depot"]["coords"]["lat"],
        lng=req["depot"]["coords"]["lng"],
        open_s=_hms(req["depot"]["open"]),
        close_s=_hms(req["depot"]["close"]),
    )
    fleet = Fleet(
        num_vans=req["fleet"]["num_vans"],
        capacity_kg=float(spec["max_payload_kg"]),
        capacity_cells=_van_capacity_cells(spec),
    )
    drivers = [
        Driver(d["id"], _hms(d["shift_start"]), _hms(d["shift_end"]))
        for d in req["drivers"]
    ]

    stops: list[Stop] = []
    for s in req["stops"]:
        d_cells = sum(_line_cells(products[ln["product_id"]], ln["qty"]) for ln in s["deliveries"])
        d_kg = sum(products[ln["product_id"]]["weight_kg"] * ln["qty"] for ln in s["deliveries"])
        p_cells = sum(_line_cells(products[ln["product_id"]], ln["qty"]) for ln in s.get("pickups", []))
        p_kg = sum(products[ln["product_id"]]["weight_kg"] * ln["qty"] for ln in s.get("pickups", []))
        stops.append(Stop(
            id=s["id"],
            lat=s["coords"]["lat"], lng=s["coords"]["lng"],
            t_open_s=_hms(s["time_window"]["open"]),
            t_close_s=_hms(s["time_window"]["close"]),
            delivery_cells=d_cells, delivery_kg=d_kg,
            pickup_cells=p_cells, pickup_kg=p_kg,
            service_time_s=SERVICE_BASE_S + SERVICE_PER_CELL_S * (d_cells + p_cells),
        ))
    return depot, fleet, drivers, stops


def build_travel_matrix(depot: Depot, stops: list[Stop]):
    """Travel-time + distance matrix scoped to depot + the request's stops."""
    from travel_time import build_matrix
    from graph_manager import get_or_build_graph
    points = [("DEPOT", depot.lat, depot.lng)] + [(s.id, s.lat, s.lng) for s in stops]
    return build_matrix(get_or_build_graph(), points)
