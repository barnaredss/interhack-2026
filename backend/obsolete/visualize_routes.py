"""Render the k-means + SA pipeline output on the road graph.

For each van: a coloured polyline that follows the actual OSM streets between
consecutive stops, stop markers annotated with id and ETA, and the depot as
a red star. Sanity-check tool — if the route zig-zags or crosses cities the
clustering or SA is wrong.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import osmnx as ox
import pyproj

from backend.obsolete.clustering import Cluster, Depot, Stop, _matrix_for_problem, load_problem
from graph_manager import _edge_colors, get_or_build_graph
from backend.obsolete.route_sa import sa_optimize_clusters

OUT_PATH = Path(__file__).with_name("routes.png")
VAN_COLORS = ["#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#e377c2"]


def _snap_nodes(graph: nx.MultiDiGraph, points: list[tuple[float, float]]) -> list[int]:
    proj = ox.projection.project_graph(graph)
    transformer = pyproj.Transformer.from_crs(
        graph.graph["crs"], proj.graph["crs"], always_xy=True
    )
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    proj_xs, proj_ys = transformer.transform(lngs, lats)
    return list(ox.distance.nearest_nodes(proj, X=list(proj_xs), Y=list(proj_ys)))


def _path_coords(graph: nx.MultiDiGraph, src: int, dst: int) -> tuple[list[float], list[float]]:
    """Return (lng, lat) lists tracing the shortest travel-time path src -> dst."""
    try:
        path = nx.shortest_path(graph, src, dst, weight="travel_time")
    except nx.NetworkXNoPath:
        return [], []
    lngs = [graph.nodes[n]["x"] for n in path]
    lats = [graph.nodes[n]["y"] for n in path]
    return lngs, lats


def _seconds_to_hm(s: float) -> str:
    h, m = divmod(int(s) // 60, 60)
    return f"{h:02d}:{m:02d}"


def _draw_graph_canvas(graph: nx.MultiDiGraph, ax, xlim, ylim) -> None:
    """Plot the road graph as a faded backdrop on the given axes."""
    edge_colors = _edge_colors(graph)
    # We draw edges manually so we don't have to instantiate a new figure.
    edges_xy: list[tuple[list[float], list[float]]] = []
    for u, v, data in graph.edges(data=True):
        if "geometry" in data:
            xs, ys = data["geometry"].xy
            edges_xy.append((list(xs), list(ys)))
        else:
            xs = [graph.nodes[u]["x"], graph.nodes[v]["x"]]
            ys = [graph.nodes[u]["y"], graph.nodes[v]["y"]]
            edges_xy.append((xs, ys))
    for (xs, ys), c in zip(edges_xy, edge_colors):
        ax.plot(xs, ys, color=c, linewidth=0.5, alpha=0.35, zorder=1)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def _draw_route_on_ax(
    ax,
    graph: nx.MultiDiGraph,
    depot: Depot,
    stops_by_id: dict[str, Stop],
    node_of: dict[str, int],
    route_stops: list[str],
    arrivals_s: list[float],
    color: str,
    label_stops: bool = True,
) -> None:
    sequence = ["DEPOT"] + list(route_stops) + ["DEPOT"]
    for src_id, dst_id in zip(sequence, sequence[1:]):
        lngs, lats = _path_coords(graph, node_of[src_id], node_of[dst_id])
        if not lngs:
            continue
        ax.plot(lngs, lats, color=color, linewidth=2.8, alpha=0.9, zorder=4)
    for k, sid in enumerate(route_stops, start=1):
        stop = stops_by_id[sid]
        ax.scatter(
            stop.lng, stop.lat,
            s=110, c=color, edgecolors="black", linewidth=0.9, zorder=6,
        )
        if label_stops:
            eta = _seconds_to_hm(arrivals_s[k - 1]) if k - 1 < len(arrivals_s) else "??"
            ax.annotate(
                f"{k}. {sid}\n{eta}",
                xy=(stop.lng, stop.lat),
                xytext=(7, 7), textcoords="offset points",
                fontsize=8, color="black", zorder=7,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.7),
            )
    # Depot marker on every panel
    ax.scatter(
        [depot.lng], [depot.lat],
        s=260, c="#d62728", marker="*", zorder=10,
        edgecolors="black", linewidth=1.0,
    )


def visualize(
    save_path: Path | str = OUT_PATH,
    show: bool = True,
) -> Path:
    depot, fleet, drivers, stops = load_problem()
    stops_by_id = {s.id: s for s in stops}
    matrix = _matrix_for_problem(depot, stops)

    from backend.obsolete.clustering import W_GEO, W_TMID, W_TWIDTH, _cluster_with_weights
    clusters = _cluster_with_weights(
        stops, depot, fleet, drivers, stops_by_id, matrix,
        W_GEO, W_TMID, W_TWIDTH,
    )
    optimized = sa_optimize_clusters(clusters, depot, fleet, drivers, stops_by_id, matrix)

    graph = get_or_build_graph()
    point_coords = [(depot.lat, depot.lng)] + [(s.lat, s.lng) for s in stops]
    point_ids = ["DEPOT"] + [s.id for s in stops]
    node_of = dict(zip(point_ids, _snap_nodes(graph, point_coords)))

    pad = 0.005
    all_lats = [depot.lat] + [s.lat for s in stops]
    all_lngs = [depot.lng] + [s.lng for s in stops]
    xlim = (min(all_lngs) - pad, max(all_lngs) + pad)
    ylim = (min(all_lats) - pad, max(all_lats) + pad)

    n_vans = fleet.num_vans
    cols = n_vans + 1  # combined panel + one per van
    fig, axes = plt.subplots(1, cols, figsize=(7 * cols, 9))
    if cols == 1:
        axes = [axes]

    # Combined panel
    ax_all = axes[0]
    _draw_graph_canvas(graph, ax_all, xlim, ylim)
    for cluster, route in optimized:
        color = VAN_COLORS[cluster.van_idx % len(VAN_COLORS)]
        _draw_route_on_ax(
            ax_all, graph, depot, stops_by_id, node_of,
            route.stops, route.arrival_times_s, color, label_stops=False,
        )
    ax_all.set_title(
        f"All vans · drive = {sum(r.travel_time_s for _,r in optimized)/60:.1f} min",
        fontsize=11,
    )
    legend_handles = [
        plt.Line2D(
            [0], [0],
            color=VAN_COLORS[c.van_idx % len(VAN_COLORS)], linewidth=3,
            label=(
                f"van {c.van_idx} ({drivers[c.van_idx].id}) · "
                f"{len(r.stops)} stops · "
                f"{r.travel_time_s/60:.1f}min · "
                f"{'OK' if r.feasible else 'INFEAS'}"
            ),
        )
        for c, r in optimized
    ]
    legend_handles.append(
        plt.Line2D([0], [0], marker="*", color="w", markerfacecolor="#d62728",
                   markersize=14, label="Warehouse", linestyle="")
    )
    ax_all.legend(handles=legend_handles, loc="upper right", framealpha=0.9, fontsize=8)

    # Per-van panels
    for i, (cluster, route) in enumerate(optimized):
        ax = axes[i + 1]
        color = VAN_COLORS[cluster.van_idx % len(VAN_COLORS)]
        _draw_graph_canvas(graph, ax, xlim, ylim)
        _draw_route_on_ax(
            ax, graph, depot, stops_by_id, node_of,
            route.stops, route.arrival_times_s, color, label_stops=True,
        )
        feas = "FEASIBLE" if route.feasible else "INFEASIBLE"
        ax.set_title(
            f"van {cluster.van_idx} ({drivers[cluster.van_idx].id}) · "
            f"{len(route.stops)} stops · "
            f"{route.travel_time_s/60:.1f} min drive · "
            f"total {route.total_time_s/3600:.2f}h · {feas}",
            fontsize=10,
        )

    fig.suptitle(
        f"Pipeline: k-means + rebalance + SA · {n_vans} vans · {len(stops)} stops",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    out = Path(save_path)
    fig.savefig(out, dpi=140, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    return out


if __name__ == "__main__":
    out = visualize(show=False)
    print(f"saved {out}")
