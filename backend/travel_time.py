"""All-pairs travel-time + distance matrix between snapped OSM road nodes.

Snaps each (lat, lng) to its nearest road node, then runs one Dijkstra per
unique source on the *directed* graph (one-ways respected). The matrix is
asymmetric and dense — every fitness evaluation downstream is now an O(1)
numpy lookup.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from dataclasses import dataclass

import networkx as nx
import numpy as np
import osmnx as ox
import pyproj

from graph_manager import DEFAULT_COORDS_PATH, get_or_build_graph

DEFAULT_MATRIX_PATH = Path(__file__).with_name("travel_time.npz")

@dataclass
class TravelMatrix:
    point_ids: list[str]
    node_ids: list[int]
    time_s: np.ndarray
    dist_m: np.ndarray

    def index_of(self, point_id: str) -> int:
        return self.point_ids.index(point_id)


def _snap(graph: nx.MultiDiGraph, points: list[tuple[float, float]]) -> list[int]:
    proj = ox.projection.project_graph(graph)
    transformer = pyproj.Transformer.from_crs(
        graph.graph["crs"], proj.graph["crs"], always_xy=True
    )
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    proj_x, proj_y = transformer.transform(lngs, lats)
    return list(ox.distance.nearest_nodes(proj, X=list(proj_x), Y=list(proj_y)))


def build_matrix(
    graph: nx.MultiDiGraph,
    points: list[tuple[str, float, float]],
) -> TravelMatrix:
    point_ids = [p[0] for p in points]
    node_ids = _snap(graph, [(p[1], p[2]) for p in points])

    n = len(points)
    time_s = np.full((n, n), np.inf, dtype=np.float32)
    dist_m = np.full((n, n), np.inf, dtype=np.float32)

    by_node: dict[int, list[int]] = {}
    for i, nd in enumerate(node_ids):
        by_node.setdefault(nd, []).append(i)
    targets = set(node_ids)

    for src_node, src_rows in by_node.items():
        t_to = nx.single_source_dijkstra_path_length(graph, src_node, weight="travel_time")
        d_to = nx.single_source_dijkstra_path_length(graph, src_node, weight="length")
        for tgt in targets:
            t = t_to.get(tgt, np.inf)
            d = d_to.get(tgt, np.inf)
            for j in by_node[tgt]:
                for i in src_rows:
                    time_s[i, j] = t
                    dist_m[i, j] = d
    np.fill_diagonal(time_s, 0.0)
    np.fill_diagonal(dist_m, 0.0)
    return TravelMatrix(point_ids, node_ids, time_s, dist_m)
