"""Drop coords whose nearest graph node is farther than MAX_DIST_M metres.

Writes the filtered set back to coords.csv and keeps a backup as
coords_original.csv (only created on the first run, never overwritten).
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import osmnx as ox

from graph_manager import DEFAULT_COORDS_PATH, get_or_build_graph

MAX_DIST_M = 100  # any drop-off farther than this from the closest node is dropped


def main() -> None:
    coords_path = Path(DEFAULT_COORDS_PATH)
    backup_path = coords_path.with_name("coords_original.csv")
    if not backup_path.exists():
        shutil.copy2(coords_path, backup_path)
        print(f"backed up original to {backup_path.name}")

    # Read from the backup so reruns are idempotent.
    with open(backup_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    graph = get_or_build_graph()
    # Project to a metric CRS so nearest_nodes returns metres.
    graph_proj = ox.projection.project_graph(graph)

    xs = [float(r["x"]) for r in rows]  # lng
    ys = [float(r["y"]) for r in rows]  # lat

    # Project each (lng, lat) into the same metric CRS as graph_proj
    import pyproj
    transformer = pyproj.Transformer.from_crs(
        graph.graph["crs"], graph_proj.graph["crs"], always_xy=True
    )
    proj_xs, proj_ys = transformer.transform(xs, ys)

    nearest, dists = ox.distance.nearest_nodes(
        graph_proj, X=list(proj_xs), Y=list(proj_ys), return_dist=True
    )

    kept = [row for row, d in zip(rows, dists) if d <= MAX_DIST_M]
    dropped = len(rows) - len(kept)
    print(
        f"kept {len(kept)}/{len(rows)} drop-offs "
        f"(dropped {dropped} farther than {MAX_DIST_M} m from any road node)"
    )

    fieldnames = list(rows[0].keys())
    with open(coords_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    print(f"wrote filtered coords to {coords_path.name}")


if __name__ == "__main__":
    main()
