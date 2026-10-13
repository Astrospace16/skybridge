"""Plan risk-weighted rescue routes on the OSM road graph."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import rasterio
from pyproj import Transformer
from rasterio.transform import rowcol
from shapely.geometry import LineString, mapping


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mask", required=True, type=Path)
    p.add_argument("--confidence", type=Path)
    p.add_argument("--damage", required=True, type=Path, help="GeoJSON from infrastructure.py")
    p.add_argument("--base", required=True, nargs=2, type=float, metavar=("LON", "LAT"))
    p.add_argument("--out", type=Path, default=Path("outputs/rescue_routes.geojson"))
    p.add_argument("--risk-out", type=Path, default=Path("outputs/flood_risk_index.tif"),
                   help="GeoTIFF of flood-mask x heuristic confidence (not a calibrated probability)")
    p.add_argument("--flood-cutoff", type=float, default=0.25,
                   help="Road flood-sample fraction above which an edge is blocked")
    p.add_argument("--risk-weight", type=float, default=5.0,
                   help="Cost multiplier per unit flood/confidence exposure")
    return p.parse_args()


def plan_routes(mask_path: Path, damage_path: Path, base: tuple[float, float], out_path: Path,
                confidence_path: Path | None = None, flood_cutoff: float = 0.25,
                risk_weight: float = 5.0, risk_out: Path | None = None) -> int:
    damage = gpd.read_file(damage_path).to_crs("EPSG:4326")
    targets = damage[(damage.feature_type == "settlement") & (damage.status == "potentially_cut_off")].copy()
    if targets.empty:
        raise RuntimeError("No potentially cut-off settlements in damage GeoJSON.")
    with rasterio.open(mask_path) as ds:
        mask = ds.read(1)
        valid = (ds.read_masks(1) > 0) & (mask != 255)
        if confidence_path and confidence_path.exists():
            with rasterio.open(confidence_path) as c:
                if c.crs != ds.crs or c.transform != ds.transform or c.width != ds.width or c.height != ds.height:
                    raise ValueError("Confidence raster must share the flood mask grid.")
                confidence = c.read(1)
                confidence = np.where(confidence == c.nodata, 0, confidence)
        else:
            confidence = np.where(mask == 1, 0.5, 0.0).astype(np.float32)
        if risk_out:
            risk_profile = ds.profile.copy()
            risk_profile.update(dtype="float32", count=1, nodata=-9999.0, compress="deflate")
            risk_arr = np.where(valid, np.where(mask == 1, np.clip(confidence, 0, 1), 0), -9999.0).astype(np.float32)
            risk_out.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(risk_out, "w", **risk_profile) as risk_ds:
                risk_ds.write(risk_arr, 1)
                risk_ds.set_band_description(1, "heuristic_flood_risk_index_0_1_not_probability")
        to_raster = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
        to_wgs = Transformer.from_crs(ds.crs, "EPSG:4326", always_xy=True)
        west, south = to_wgs.transform(ds.bounds.left, ds.bounds.bottom)
        east, north = to_wgs.transform(ds.bounds.right, ds.bounds.top)

        # Include modest context beyond the raster bounds for bases/settlement approaches.
        pad_lon, pad_lat = 0.02, 0.02
        G = ox.graph.graph_from_bbox((west-pad_lon, south-pad_lat, east+pad_lon, north+pad_lat),
                                     network_type="drive", simplify=True, retain_all=True)
        if G.number_of_nodes() == 0:
            raise RuntimeError("No routable OSM road graph in the selected extent.")
        G = ox.distance.add_edge_lengths(G)
        edges = list(G.edges(keys=True, data=True))
        pixel_step = max(abs(ds.transform.a), abs(ds.transform.e))
        for u, v, key, data in edges:
            geom = data.get("geometry")
            if geom is None:
                geom = LineString([(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])])
            n = max(2, min(400, int(data.get("length", geom.length) / max(25, pixel_step)) + 1))
            points = [geom.interpolate(i / (n - 1), normalized=True) for i in range(n)]
            vals, confs = [], []
            for p in points:
                x, y = to_raster.transform(p.x, p.y)
                rr, cc = rowcol(ds.transform, x, y)
                if 0 <= rr < ds.height and 0 <= cc < ds.width and valid[rr, cc]:
                    vals.append(float(mask[rr, cc] == 1)); confs.append(float(confidence[rr, cc]))
            flood_frac = float(np.mean(vals)) if vals else 0.0
            mean_conf = float(np.mean(confs)) if confs else 0.0
            if flood_frac >= flood_cutoff:
                data["safe_cost"] = None
            else:
                data["safe_cost"] = max(float(data.get("length", 1)), 1.0) * (1.0 + risk_weight * flood_frac * mean_conf)
            data["flood_fraction"] = flood_frac
            data["risk"] = flood_frac * mean_conf

        def edge_cost(u: Any, v: Any, edge_data: dict[str, Any]) -> float | None:
            # NetworkX passes a key->attributes mapping for MultiDiGraph edges.
            if "safe_cost" in edge_data:
                return edge_data["safe_cost"]
            costs = [attrs.get("safe_cost") for attrs in edge_data.values()]
            usable = [float(cost) for cost in costs if cost is not None]
            return min(usable) if usable else None

        def distance_heuristic(a: Any, b: Any) -> float:
            # Haversine distance is an admissible lower bound in metres.
            lat1, lat2 = math.radians(G.nodes[a]["y"]), math.radians(G.nodes[b]["y"])
            dlat = lat2 - lat1
            dlon = math.radians(G.nodes[b]["x"] - G.nodes[a]["x"])
            h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            return 2 * 6_371_000 * math.asin(math.sqrt(h))

        base_node = ox.distance.nearest_nodes(G, X=base[0], Y=base[1])
        route_features: list[dict[str, Any]] = []
        for index, (_, feature) in enumerate(targets.iterrows(), start=1):
            geom = feature.geometry
            center = geom if geom.geom_type == "Point" else geom.representative_point()
            target_node = ox.distance.nearest_nodes(G, X=center.x, Y=center.y)
            try:
                path = nx.astar_path(
                    G, base_node, target_node,
                    heuristic=distance_heuristic,
                    weight=edge_cost,
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                print(f"No low-risk connected road route for {feature.get('name', f'settlement-{index}')}")
                continue
            coordinates = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in path]
            coords = [(float(x), float(y)) for x, y in coordinates]
            length_m = 0.0
            edge_risks = []
            for u, v in zip(path[:-1], path[1:]):
                usable = [attrs for attrs in G[u][v].values() if attrs.get("safe_cost") is not None]
                if usable:
                    selected_edge = min(usable, key=lambda attrs: attrs["safe_cost"])
                    length_m += selected_edge.get("length", 0)
                    edge_risks.append(float(selected_edge.get("risk", 0)))
            population = int(feature.get("population", 100) or 100)
            population_score = min(math.log1p(max(population, 0)) / math.log1p(5000), 1.0)
            severity = float(feature.get("surrounding_flood_fraction", 0) or 0)
            distance_score = max(0.0, 1.0 - min(length_m / 1000, 100.0) / 100.0)
            priorities = 100 * (0.40 * population_score + 0.35 * severity + 0.25 * distance_score)
            route_features.append({
                "type": "Feature", "geometry": mapping(LineString(coords)),
                "properties": {
                    "route_id": f"route-{index}", "settlement": str(feature.get("name", f"settlement-{index}")),
                    "population": population,
                    "distance_km": round(length_m / 1000, 2),
                    "priority_score": round(priorities, 3),
                    "mean_flood_risk_index": round(float(np.mean(edge_risks)) if edge_risks else 0.0, 4),
                    "risk_weight": risk_weight, "flood_cutoff": flood_cutoff,
                    "status": "candidate_route_review_required",
                },
            })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": route_features}, indent=2), encoding="utf-8")
    print(f"Wrote {len(route_features)} candidate routes to {out_path}")
    return len(route_features)


def main() -> None:
    args = parse_args()
    plan_routes(args.mask, args.damage, tuple(args.base), args.out, args.confidence,
                args.flood_cutoff, args.risk_weight, args.risk_out)


if __name__ == "__main__":
    main()
