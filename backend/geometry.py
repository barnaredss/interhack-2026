"""Trace each van's route along actual OSM roads, returning a polyline.

Used to render routes on the frontend map as curves following the streets,
not as straight chord lines between stops.
"""

from __future__ import annotations

import networkx as nx

from travel_time import TravelMatrix


def _edge_coords(graph: nx.MultiDiGraph, u: int, v: int) -> list[tuple[float, float]]:
    """Coords along the (u, v) edge. Uses curve geometry when present."""
    data = graph.get_edge_data(u, v)
    if data:
        first = next(iter(data.values()))
        geom = first.get("geometry")
        if geom is not None:
            xs, ys = geom.xy
            return [(float(y), float(x)) for x, y in zip(xs, ys)]
    return [
        (float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"])),
        (float(graph.nodes[v]["y"]), float(graph.nodes[v]["x"])),
    ]


def route_polyline(
    graph: nx.MultiDiGraph,
    matrix: TravelMatrix,
    stop_sequence: list[str],
) -> list[tuple[float, float]]:
    """Concatenate OSM-traced legs into one (lat, lng) polyline.

    `stop_sequence` typically starts and ends with "DEPOT", e.g.
    ["DEPOT", "S005", "S007", "DEPOT"].
    """
    out: list[tuple[float, float]] = []
    for src_id, dst_id in zip(stop_sequence, stop_sequence[1:]):
        src_node = matrix.node_ids[matrix.index_of(src_id)]
        dst_node = matrix.node_ids[matrix.index_of(dst_id)]
        try:
            path = nx.shortest_path(graph, src_node, dst_node, weight="travel_time")
        except nx.NetworkXNoPath:
            continue
        for u, v in zip(path, path[1:]):
            seg = _edge_coords(graph, u, v)
            # Avoid duplicating the joint node at the boundary between legs.
            if out and seg and out[-1] == seg[0]:
                out.extend(seg[1:])
            else:
                out.extend(seg)
    return out
