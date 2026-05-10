"""FastAPI server: OR-tools VRPSPD-TW + analytics layer.

    GET  /health           liveness probe
    GET  /sample-request   bundled example body
    POST /optimize         main endpoint, returns plan + polylines + KPIs + explanations
    POST /whatif           perturb the request (driver out, traffic, stop cancelled) and re-solve
    POST /baseline         naive comparator alone
    GET  /docs             auto Swagger UI
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import os
import httpx
from anthropic import Anthropic
import numpy as np
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from analytics import (
    CO2_KG_PER_KM, KPIs, compute_kpis, explain_van, load_profile, warehouse_prep,
)
from baseline import solve_baseline
from firestore_writer import build_route_doc, is_enabled as fs_enabled, write_routes
from geometry import route_polyline
from graph_manager import get_or_build_graph
from loader import build_travel_matrix, load
from solver import solve_vrp

SAMPLE = Path(__file__).with_name("sample_request.json")
PRODUCTS = Path(__file__).with_name("products.json")


# --- Request schema (mirrors sample_request.json) ----------------------

class Coords(BaseModel):
    lat: float
    lng: float


class TimeWindow(BaseModel):
    open: str
    close: str


class Line(BaseModel):
    product_id: str
    qty: int


class StopReq(BaseModel):
    id: str
    coords: Coords
    time_window: TimeWindow
    deliveries: list[Line]
    pickups: list[Line] = []


class DepotReq(BaseModel):
    id: str
    coords: Coords
    open: str
    close: str


class FleetReq(BaseModel):
    num_vans: int
    van_type: str
    vans_ref: str | None = None
    products_ref: str | None = None


class DriverReq(BaseModel):
    id: str
    shift_start: str
    shift_end: str


class OptimizeRequest(BaseModel):
    request_id: str | None = None
    date: str | None = None
    depot: DepotReq
    fleet: FleetReq
    drivers: list[DriverReq]
    stops: list[StopReq]


class Disruption(BaseModel):
    type: Literal["driver_unavailable", "stop_cancelled", "traffic"]
    driver_id: str | None = None
    stop_id: str | None = None
    multiplier: float | None = None     # for "traffic"


class WhatIfRequest(BaseModel):
    request: OptimizeRequest
    disruption: Disruption

class ChatRequest(BaseModel):
    transcript: str
    context: str

class TTSRequest(BaseModel):
    text: str


# --- Response schema ---------------------------------------------------

class StopOut(BaseModel):
    sequence: int
    id: str
    arrival_time: str
    coords: Coords


class LoadPointOut(BaseModel):
    after_stop: str
    cells: int
    kg: int


class VanOut(BaseModel):
    van_idx: int
    driver_id: str
    feasible: bool
    travel_time_min: float
    total_time_h: float
    peak_cells: int
    peak_kg: int
    stops: list[StopOut]
    polyline: list[Coords]
    load_profile: list[LoadPointOut]
    explanations: list[str]


class KPIsOut(BaseModel):
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


class OptimizeResponse(BaseModel):
    request_id: str | None
    fleet_drive_min: float
    fleet_total_h: float
    all_feasible: bool
    depot: DepotReq
    vans: list[VanOut]
    kpis: KPIsOut
    warehouse_prep: list[str]
    firestore_written: int = 0


# --- App ---------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    get_or_build_graph()
    yield


app = FastAPI(title="Damm Smart Truck API", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_methods=["GET", "POST"], allow_headers=["*"],
)


def _hm(s: int) -> str:
    h, m = divmod(int(s) // 60, 60)
    return f"{h:02d}:{m:02d}"


def _run_pipeline(
    body: dict,
    *,
    write_firestore: bool,
    traffic_multiplier: float = 1.0,
    time_limit_s: int = 5,
) -> OptimizeResponse:
    """Shared core for /optimize and /whatif."""
    try:
        depot, fleet, drivers, stops = load(body)
    except KeyError as e:
        raise HTTPException(422, f"unknown reference: {e}")
    if not drivers:
        raise HTTPException(422, "no drivers in fleet")
    if len(drivers) != fleet.num_vans:
        raise HTTPException(
            422, f"num_vans={fleet.num_vans} but {len(drivers)} drivers"
        )
    if not stops:
        raise HTTPException(422, "no stops to deliver")

    matrix = build_travel_matrix(depot, stops)
    if traffic_multiplier != 1.0:
        matrix.time_s = (matrix.time_s * float(traffic_multiplier)).astype(np.float32)

    plan = solve_vrp(depot, fleet, drivers, stops, matrix, time_limit_s=time_limit_s)
    if not plan.vans:
        # The disruption made the day infeasible — return a structured
        # "no plan" response instead of 500 so the UI can show it.
        return OptimizeResponse(
            request_id=body.get("request_id"),
            fleet_drive_min=0.0,
            fleet_total_h=0.0,
            all_feasible=False,
            depot=DepotReq(**body["depot"]),
            vans=[],
            kpis=KPIsOut(
                fleet_drive_min=0.0, baseline_drive_min=0.0, savings_pct=0.0,
                fleet_km=0.0, baseline_km=0.0, co2_kg_saved=0.0,
                driver_utilization_pct=0.0, capacity_utilization_pct=0.0,
                stops_per_van=[], feasible_vans=0, total_vans=fleet.num_vans,
            ),
            warehouse_prep=[
                "INFEASIBLE: no valid plan exists for this scenario. "
                "Try relaxing time windows, adding a backup driver, or rescheduling stops."
            ],
            firestore_written=0,
        )

    baseline_plan = solve_baseline(depot, fleet, drivers, stops, matrix)

    stops_by_id = {s.id: s for s in stops}
    graph = get_or_build_graph()

    vans_out: list[VanOut] = []
    for v in plan.vans:
        sequence = ["DEPOT"] + [p.id for p in v.stops] + ["DEPOT"]
        polyline = (
            [Coords(lat=lat, lng=lng) for lat, lng in route_polyline(graph, matrix, sequence)]
            if v.stops else []
        )
        prof = load_profile(v, stops_by_id)
        vans_out.append(VanOut(
            van_idx=v.van_idx,
            driver_id=v.driver_id,
            feasible=v.feasible,
            travel_time_min=round(v.travel_s / 60, 2),
            total_time_h=round(v.total_s / 3600, 3),
            peak_cells=v.peak_cells,
            peak_kg=v.peak_kg,
            stops=[
                StopOut(
                    sequence=p.sequence, id=p.id,
                    arrival_time=_hm(p.arrival_s),
                    coords=Coords(lat=stops_by_id[p.id].lat, lng=stops_by_id[p.id].lng),
                )
                for p in v.stops
            ],
            polyline=polyline,
            load_profile=[
                LoadPointOut(after_stop=lp.after_stop, cells=lp.cells, kg=lp.kg)
                for lp in prof
            ],
            explanations=explain_van(v, stops_by_id, drivers[v.van_idx]),
        ))

    kpis = compute_kpis(plan, baseline_plan, fleet, drivers, matrix)
    prep = warehouse_prep(plan, body["stops"], stops_by_id)

    written = 0
    if write_firestore and fs_enabled():
        service_times_s = {s.id: s.service_time_s for s in stops}
        docs = [
            build_route_doc(
                driver_id=v.driver_id,
                truck_id=f"T-{v.van_idx + 1:02d}",
                van_type=body["fleet"]["van_type"],
                depot=body["depot"],
                request_stops=body["stops"],
                van_plan=v,
                service_times_s=service_times_s,
            )
            for v in plan.vans
        ]
        written = write_routes(docs)

    return OptimizeResponse(
        request_id=body.get("request_id"),
        fleet_drive_min=round(plan.drive_s / 60, 2),
        fleet_total_h=round(plan.total_s / 3600, 3),
        all_feasible=plan.all_feasible,
        depot=DepotReq(**body["depot"]),
        vans=vans_out,
        kpis=KPIsOut(**kpis.__dict__),
        warehouse_prep=prep,
        firestore_written=written,
    )


# --- Endpoints ---------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/sample-request", response_model=OptimizeRequest)
def sample_request():
    if not SAMPLE.exists():
        raise HTTPException(404, "sample_request.json missing")
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


@app.get("/products")
def products():
    if not PRODUCTS.exists():
        raise HTTPException(404, "products.json missing")
    return json.loads(PRODUCTS.read_text(encoding="utf-8"))


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest) -> OptimizeResponse:
    return _run_pipeline(req.model_dump(), write_firestore=True)


@app.post("/whatif", response_model=OptimizeResponse)
def whatif(req: WhatIfRequest) -> OptimizeResponse:
    body = req.request.model_dump()
    multiplier = 1.0
    d = req.disruption

    if d.type == "driver_unavailable":
        if not d.driver_id:
            raise HTTPException(422, "driver_id required for driver_unavailable")
        body["drivers"] = [x for x in body["drivers"] if x["id"] != d.driver_id]
        body["fleet"]["num_vans"] = len(body["drivers"])
    elif d.type == "stop_cancelled":
        if not d.stop_id:
            raise HTTPException(422, "stop_id required for stop_cancelled")
        body["stops"] = [s for s in body["stops"] if s["id"] != d.stop_id]
    elif d.type == "traffic":
        multiplier = float(d.multiplier or 1.3)

    # What-ifs are exploratory; don't overwrite the real plan in Firestore.
    # Larger time budget — disrupted scenarios are tighter to solve.
    return _run_pipeline(
        body, write_firestore=False, traffic_multiplier=multiplier, time_limit_s=15,
    )


@app.post("/baseline", response_model=OptimizeResponse)
def baseline_endpoint(req: OptimizeRequest) -> OptimizeResponse:
    """Run only the naive baseline (for explicit comparison demos)."""
    body = req.model_dump()
    depot, fleet, drivers, stops = load(body)
    matrix = build_travel_matrix(depot, stops)
    plan = solve_baseline(depot, fleet, drivers, stops, matrix)
    stops_by_id = {s.id: s for s in stops}
    graph = get_or_build_graph()

    vans_out: list[VanOut] = []
    for v in plan.vans:
        sequence = ["DEPOT"] + [p.id for p in v.stops] + ["DEPOT"]
        polyline = (
            [Coords(lat=lat, lng=lng) for lat, lng in route_polyline(graph, matrix, sequence)]
            if v.stops else []
        )
        vans_out.append(VanOut(
            van_idx=v.van_idx, driver_id=v.driver_id, feasible=v.feasible,
            travel_time_min=round(v.travel_s / 60, 2),
            total_time_h=round(v.total_s / 3600, 3),
            peak_cells=v.peak_cells, peak_kg=v.peak_kg,
            stops=[
                StopOut(
                    sequence=p.sequence, id=p.id,
                    arrival_time=_hm(p.arrival_s),
                    coords=Coords(lat=stops_by_id[p.id].lat, lng=stops_by_id[p.id].lng),
                )
                for p in v.stops
            ],
            polyline=polyline,
            load_profile=[
                LoadPointOut(after_stop=lp.after_stop, cells=lp.cells, kg=lp.kg)
                for lp in load_profile(v, stops_by_id)
            ],
            explanations=explain_van(v, stops_by_id, drivers[v.van_idx]),
        ))

    return OptimizeResponse(
        request_id=req.request_id,
        fleet_drive_min=round(plan.drive_s / 60, 2),
        fleet_total_h=round(plan.total_s / 3600, 3),
        all_feasible=plan.all_feasible,
        depot=req.depot,
        vans=vans_out,
        kpis=KPIsOut(
            fleet_drive_min=round(plan.drive_s / 60, 2),
            baseline_drive_min=round(plan.drive_s / 60, 2),
            savings_pct=0.0,
            fleet_km=0.0, baseline_km=0.0, co2_kg_saved=0.0,
            driver_utilization_pct=0.0, capacity_utilization_pct=0.0,
            stops_per_van=[len(v.stops) for v in plan.vans],
            feasible_vans=sum(1 for v in plan.vans if v.feasible),
            total_vans=len(plan.vans),
        ),
        warehouse_prep=warehouse_prep(plan, body["stops"], stops_by_id),
        firestore_written=0,
    )

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
    client = Anthropic(api_key=api_key)
    system = (
        "You are a hands-free voice assistant for Damm Motion delivery drivers.\n\n"
        "Current route status:\n" + req.context + "\n\n"
        "Based on what the driver said, decide the best action and give a short spoken reply (max 2 sentences).\n"
        "Respond in the same language the driver used. Be concise — this will be read aloud.\n"
        "You MUST return ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "action": "mark_delivered" | "next_stop" | "navigate" | "status" | "unknown",\n'
        '  "response": "..."\n'
        "}"
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": req.transcript}]
        )
        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            if lines[0].startswith("```"): lines = lines[1:]
            if lines[-1].startswith("```"): lines = lines[:-1]
            raw_text = "\n".join(lines).strip()
        
        return json.loads(raw_text)
    except Exception as e:
        raise HTTPException(500, f"Anthropic API error: {e}")

@app.post("/api/tts")
def tts_endpoint(req: TTSRequest):
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    if not api_key:
        raise HTTPException(500, "ELEVENLABS_API_KEY not configured")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "text": req.text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }
    try:
        with httpx.Client() as client:
            resp = client.post(url, headers=headers, json=payload, timeout=10.0)
            resp.raise_for_status()
            return Response(content=resp.content, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(500, f"ElevenLabs API error: {e}")



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
