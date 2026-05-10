"""Build and persist a road graph covering Granollers and Mollet del Vallès.

Within each city we keep the full drivable network so deliveries can be routed
to any address. Between the cities we only keep major roads (motorway, trunk,
primary) since the truck just needs the fastest corridor from one to the other.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import osmnx as ox
from shapely.ops import unary_union

GRANOLLERS = "Granollers, Catalunya, Spain"
MOLLET_DEL_VALLES = "Mollet del Vallès, Catalunya, Spain"

NETWORK_TYPE = "drive"

# Inter-city corridor: only fast roads. Includes link ramps so the corridor
# actually connects to the local networks at on/off-ramps.
MAJOR_ROAD_FILTER = (
    '["highway"~"motorway|trunk|primary|'
    'motorway_link|trunk_link|primary_link"]'
)

DEFAULT_GRAPH_PATH = Path(__file__).with_name("cities_graph.graphml")
DEFAULT_COORDS_PATH = Path(__file__).with_name("coords.csv")
DEFAULT_REQUEST_PATH = Path(__file__).with_name("data") / "sample_request.json"


def load_depot_coords(path: Path | str = DEFAULT_REQUEST_PATH) -> tuple[float, float] | None:
    """Return (lat, lng) of the depot if `sample_request.json` exists, else None."""
    path = Path(path)
    if not path.exists():
        return None
    req = json.loads(path.read_text(encoding="utf-8"))
    c = req.get("depot", {}).get("coords")
    if not c:
        return None
    return float(c["lat"]), float(c["lng"])


def _draw_depot(ax, depot: tuple[float, float] | None) -> None:
    if depot is None:
        return
    lat, lng = depot
    ax.scatter(
        [lng], [lat],
        s=160, c="#d62728", marker="*", zorder=10,
        edgecolors="black", linewidth=1.0,
        label="Warehouse",
    )


def _city_graph(place: str) -> nx.MultiDiGraph:
    return ox.graph_from_place(place, network_type=NETWORK_TYPE)


def _corridor_graph(places: list[str]) -> nx.MultiDiGraph:
    """Major-roads-only graph spanning the bounding box of the given places."""
    boundaries = [ox.geocode_to_gdf(p).geometry.iloc[0] for p in places]
    minx, miny, maxx, maxy = unary_union(boundaries).bounds
    return ox.graph_from_bbox(
        bbox=(minx, miny, maxx, maxy),
        custom_filter=MAJOR_ROAD_FILTER,
        truncate_by_edge=True,
        retain_all=False,
    )


def build_combined_graph() -> nx.MultiDiGraph:
    """Return a single graph with both cities plus the minimal corridor between."""
    g_granollers = _city_graph(GRANOLLERS)
    g_mollet = _city_graph(MOLLET_DEL_VALLES)
    g_corridor = _corridor_graph([GRANOLLERS, MOLLET_DEL_VALLES])

    combined = nx.compose_all([g_granollers, g_mollet, g_corridor])
    # Preserve graph-level metadata (CRS) that compose_all drops.
    combined.graph.update(g_granollers.graph)

    # Drop disconnected fragments left over after composition.
    combined = ox.truncate.largest_component(combined, strongly=True)

    # Enrich edges with speed_kph + travel_time (seconds) so downstream
    # routing can use realistic times instead of raw distance.
    combined = ox.routing.add_edge_speeds(combined)
    combined = ox.routing.add_edge_travel_times(combined)
    return combined


def save_graph(graph: nx.MultiDiGraph, path: Path | str = DEFAULT_GRAPH_PATH) -> Path:
    path = Path(path)
    ox.save_graphml(graph, path)
    return path


def load_graph(path: Path | str = DEFAULT_GRAPH_PATH) -> nx.MultiDiGraph:
    return ox.load_graphml(Path(path))


def get_or_build_graph(path: Path | str = DEFAULT_GRAPH_PATH) -> nx.MultiDiGraph:
    path = Path(path)
    if path.exists():
        return load_graph(path)
    graph = build_combined_graph()
    save_graph(graph, path)
    return graph


def load_dropoff_coords(path: Path | str = DEFAULT_COORDS_PATH) -> list[tuple[float, float]]:
    """Read drop-off coordinates from a CSV with columns `id, x, y` (x=lng, y=lat)."""
    points: list[tuple[float, float]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            points.append((float(row["y"]), float(row["x"])))
    return points


def _edge_colors(graph: nx.MultiDiGraph) -> list[str]:
    keywords = ("motorway", "trunk", "primary")
    # Iterate with keys=True so the list aligns 1:1 with osmnx's internal
    # edge ordering in MultiDiGraph plotting.
    return [
        "#d62728" if any(k in str(data.get("highway", "")) for k in keywords) else "#444444"
        for _, _, _, data in graph.edges(keys=True, data=True)
    ]


def plot_graph(
    graph: nx.MultiDiGraph,
    save_path: Path | str | None = None,
    depot: tuple[float, float] | None = None,
) -> Path | None:
    """Show the graph with city streets in grey and the inter-city corridor in red.

    Always writes a PNG (default `cities_graph.png` next to this file) because the
    interactive tkagg window can fail to render edges on some Windows setups —
    the file is the source of truth.
    """
    fig, ax = ox.plot_graph(
        graph,
        figsize=(12, 12),
        edge_color=_edge_colors(graph),
        edge_linewidth=1.2,
        edge_alpha=1.0,
        node_size=3,
        node_color="#1f77b4",
        bgcolor="white",
        show=False,
        close=False,
    )
    _draw_depot(ax, depot)
    if depot is not None:
        ax.legend(loc="upper right", framealpha=0.9)
    ax.set_title(
        f"Granollers + Mollet del Vallès "
        f"({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)"
    )
    out = Path(save_path) if save_path else Path(__file__).with_name("cities_graph.png")
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.show()
    return out


def plot_graph_with_dropoffs(
    graph: nx.MultiDiGraph,
    coords: list[tuple[float, float]],
    title: str | None = None,
    save_path: Path | str | None = None,
    depot: tuple[float, float] | None = None,
) -> Path | None:
    """Overlay drop-off coordinates on the road graph.

    `coords` is a list of (lat, lng) pairs (matches `load_dropoff_coords`).
    Axis limits expand to include any drop-offs that fall outside the graph
    extent so off-area points are still visible (they may indicate the graph
    needs to be rebuilt over a different region).
    """
    fig, ax = ox.plot_graph(
        graph,
        figsize=(12, 12),
        edge_color=_edge_colors(graph),
        edge_linewidth=1.2,
        edge_alpha=1.0,
        node_size=3,
        node_color="#1f77b4",
        bgcolor="white",
        show=False,
        close=False,
    )
    pad = 0.005
    node_lngs = [graph.nodes[n]["x"] for n in graph.nodes()]
    node_lats = [graph.nodes[n]["y"] for n in graph.nodes()]
    xmin, xmax = min(node_lngs) - pad, max(node_lngs) + pad
    ymin, ymax = min(node_lats) - pad, max(node_lats) + pad

    inside = [(lat, lng) for lat, lng in coords if xmin <= lng <= xmax and ymin <= lat <= ymax]
    outside_count = len(coords) - len(inside)
    if outside_count:
        print(
            f"warning: {outside_count}/{len(coords)} drop-offs fall outside the "
            f"graph extent and were cropped from the plot"
        )

    lats = [c[0] for c in inside]
    lngs = [c[1] for c in inside]
    ax.scatter(
        lngs, lats,
        s=10, c="#2ca02c", alpha=0.7, zorder=5,
        edgecolors="white", linewidth=0.3,
        label=f"{len(inside)} drop-offs (in extent)",
    )

    _draw_depot(ax, depot)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.legend(loc="upper right", framealpha=0.9)
    ax.set_title(
        title
        or f"Graph + {len(coords)} drop-offs "
           f"({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)"
    )
    out = Path(save_path) if save_path else Path(__file__).with_name("cities_graph_dropoffs.png")
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.show()
    return out


if __name__ == "__main__":
    g = build_combined_graph()
    print(f"Nodes: {g.number_of_nodes()}  Edges: {g.number_of_edges()}")
    out = save_graph(g)
    print(f"Saved to {out}")

    depot = load_depot_coords()
    if depot is not None:
        print(f"Depot at lat={depot[0]:.4f}, lng={depot[1]:.4f}")
    if DEFAULT_COORDS_PATH.exists():
        coords = load_dropoff_coords(DEFAULT_COORDS_PATH)
        print(f"Loaded {len(coords)} drop-off points from {DEFAULT_COORDS_PATH.name}")
        png = plot_graph_with_dropoffs(g, coords, depot=depot)
    else:
        png = plot_graph(g, depot=depot)
    print(f"Saved plot to {png}")
