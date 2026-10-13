"""Overlay flood candidates on OpenStreetMap infrastructure and settlements.

The community cut-off flag is a screening proxy: water around a place plus nearby
flooded roads. Confirm access with responders and current road/bridge reports.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.transform import rowcol
from shapely.geometry import Point, mapping
from shapely.ops import transform as transform_geom


def args_parser() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mask", type=Path, required=True, help="Flood mask: 1=candidate, 0=not detected")
    p.add_argument("--confidence", type=Path, help="Optional 0-1 confidence raster on same grid")
    p.add_argument("--out", type=Path, default=Path("outputs/damage.geojson"))
    p.add_argument("--community-radius-m", type=float, default=500)
    return p.parse_args()


def _get_tags() -> dict[str, Any]:
    return {
        "highway": True, "building": True, "bridge": True,
        "amenity": ["hospital", "clinic", "school", "fire_station", "shelter"],
        "healthcare": True, "emergency": True,
        "place": ["city", "town", "village", "hamlet", "suburb", "neighbourhood", "locality"],
    }


def _raster_samples(geometries: list[Any], ds: rasterio.io.DatasetReader,
                    band: np.ndarray, valid: np.ndarray, spacing_m: float = 25) -> tuple[list[float], list[float]]:
    """Return flood and confidence fractions sampled along lines / within places."""
    to_raster = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
    flood_fractions: list[float] = []
    confidence_means: list[float] = []
    if ds.crs.is_projected:
        pixel_step = max(abs(ds.transform.a), abs(ds.transform.e))
        step = max(spacing_m, pixel_step)
    else:
        # Approximate 25 m in latitude degrees; adequate for sampling, not area measurement.
        step = max(spacing_m / 111_000, 0.0001)
    for geom in geometries:
        pts: list[Point] = []
        if geom is None or geom.is_empty:
            flood_fractions.append(0.0); confidence_means.append(0.0); continue
        projected = transform_geom(to_raster.transform, geom)
        if projected.geom_type in ("LineString", "MultiLineString"):
            line = projected
            length = line.length
            n = max(2, min(500, int(length / step) + 1))
            pts = [line.interpolate(i / (n - 1), normalized=True) for i in range(n)]
        else:
            p = projected if projected.geom_type == "Point" else projected.representative_point()
            pts = [p]
        vals, confs = [], []
        for point in pts:
            rr, cc = rowcol(ds.transform, point.x, point.y)
            if 0 <= rr < ds.height and 0 <= cc < ds.width and valid[rr, cc]:
                vals.append(int(band[rr, cc] == 1))
                confs.append(float(confidence[rr, cc]))
        flood_fractions.append(float(np.mean(vals)) if vals else 0.0)
        confidence_means.append(float(np.mean(confs)) if confs else 0.0)
    return flood_fractions, confidence_means


def analyze(mask_path: Path, out_path: Path, confidence_path: Path | None = None,
            community_radius_m: float = 500) -> dict[str, int]:
    with rasterio.open(mask_path) as ds:
        mask = ds.read(1)
        valid = (ds.read_masks(1) > 0) & (mask != 255)
        bounds = ds.bounds
        to_wgs = Transformer.from_crs(ds.crs, "EPSG:4326", always_xy=True)
        west, south = to_wgs.transform(bounds.left, bounds.bottom)
        east, north = to_wgs.transform(bounds.right, bounds.top)
        if confidence_path and confidence_path.exists():
            with rasterio.open(confidence_path) as conf_ds:
                if conf_ds.crs != ds.crs or conf_ds.transform != ds.transform:
                    raise ValueError("Confidence raster must share the flood mask grid.")
                confidence = conf_ds.read(1)
                confidence = np.where(confidence == conf_ds.nodata, 0, confidence)
        else:
            confidence = np.where(mask == 1, 0.5, 0.0).astype(np.float32)

        # OSMnx v2 expects (left, bottom, right, top) in EPSG:4326.
        features = ox.features_from_bbox((west, south, east, north), _get_tags())
        if features.empty:
            raise RuntimeError("OpenStreetMap returned no features for the mask extent.")
        features = features.reset_index(drop=True)
        if features.crs is None:
            features = features.set_crs("EPSG:4326")
        else:
            features = features.to_crs("EPSG:4326")

        line_mask = features.geometry.geom_type.isin(["LineString", "MultiLineString"])
        highway = features["highway"] if "highway" in features else pd.Series(index=features.index, dtype=object)
        roads = features[line_mask & highway.notna()].copy()
        if not roads.empty:
            frac, conf = _raster_samples(list(roads.geometry), ds, mask, valid, confidence)
            roads["flood_fraction"] = frac
            roads["mean_confidence"] = conf
            roads["feature_type"] = "road"
            roads["status"] = np.where(roads.flood_fraction >= 0.20, "flood_exposed", "no_flood_overlap_detected")
            bridge_tag = roads["bridge"] if "bridge" in roads else pd.Series("no", index=roads.index)
            roads["is_bridge"] = bridge_tag.fillna("no").astype(str).str.lower().isin(["yes", "true", "1"])
            roads.loc[roads.is_bridge & (roads.flood_fraction >= 0.20), "feature_type"] = "bridge"

        place_values = {"city", "town", "village", "hamlet", "suburb", "neighbourhood", "locality"}
        place_tag = features["place"].fillna("") if "place" in features else pd.Series("", index=features.index)
        has_place = place_tag.isin(place_values)
        building_tag = features["building"] if "building" in features else pd.Series(index=features.index, dtype=object)
        building = building_tag.notna()
        places = features[has_place].copy()
        # Use mapped settlements where available. If absent, derive a light-touch
        # community proxy from residential building centroids, grouped by 500 m.
        if places.empty and building.any():
            b = features[building].copy()
            metric = b.to_crs(b.estimate_utm_crs())
            centroids = metric.geometry.centroid
            cells = np.floor(centroids.x / 500).astype(int).astype(str) + ":" + np.floor(centroids.y / 500).astype(int).astype(str)
            b["_cell"] = cells.values
            places = b.dissolve(by="_cell", as_index=False)
            places = places.to_crs("EPSG:4326")
            places["name"] = "Building cluster"
        if not places.empty:
            local_crs = places.estimate_utm_crs()
            metric_places = places.to_crs(local_crs)
            xy = list(zip(metric_places.geometry.centroid.x, metric_places.geometry.centroid.y))
            mapped_population = places["population"] if "population" in places else pd.Series(np.nan, index=places.index)
            pops = []
            for i, pt in enumerate(xy):
                parsed = None
                try:
                    parsed = int(str(mapped_population.iloc[i]).replace(",", "")) if not np.isnan(float(mapped_population.iloc[i])) else None
                except (ValueError, TypeError):
                    parsed = None
                pops.append(parsed or 100)
            places["estimated_population"] = pops
            # Ring sampling approximates water surrounding a settlement.
            geom432 = list(places.geometry)
            ring_geoms = []
            for geom in geom432:
                projected = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(local_crs).iloc[0]
                ring = projected.buffer(community_radius_m).boundary
                ring_geoms.append(gpd.GeoSeries([ring], crs=local_crs).to_crs("EPSG:4326").iloc[0])
            ring_flood, _ = _raster_samples(ring_geoms, ds, mask, valid, spacing_m=25)
            places["surrounding_flood_fraction"] = ring_flood
            places["feature_type"] = "settlement"
            places["status"] = np.where(places.surrounding_flood_fraction >= 0.20, "potentially_cut_off", "no_cutoff_signal")
            places["population"] = places["estimated_population"]
            pop_score = np.clip(np.log1p(np.asarray(pops, dtype=float)) / np.log1p(5000), 0, 1)
            places["priority_score"] = np.round(100 * (0.45 * pop_score + 0.55 * np.asarray(ring_flood)), 1)

        # Point/polygon sites tagged with critical service keys.
        amenities = features["amenity"].fillna("") if "amenity" in features else pd.Series("", index=features.index)
        healthcare = features["healthcare"].fillna("") if "healthcare" in features else pd.Series("", index=features.index)
        emergency = features["emergency"].fillna("") if "emergency" in features else pd.Series("", index=features.index)
        critical = features[
            amenities.isin(["hospital", "clinic", "school", "fire_station", "shelter"])
            | healthcare.ne("") | emergency.ne("")
        ].copy()
        if not critical.empty:
            ff, cf = _raster_samples(list(critical.geometry), ds, mask, valid)
            critical["flood_fraction"] = ff
            critical["mean_confidence"] = cf
            critical["feature_type"] = "critical_infrastructure"
            critical["status"] = np.where(critical.flood_fraction >= 0.10, "at_risk", "no_flood_overlap_detected")

    frames = [f for f in (roads, critical, places) if f is not None and not f.empty]
    if not frames:
        raise RuntimeError("No roads, critical sites, or settlements to export.")
    output = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True, sort=False),
        geometry="geometry", crs="EPSG:4326",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_file(out_path, driver="GeoJSON")
    counts = output.groupby(["feature_type", "status"]).size().to_dict()
    print(json.dumps({f"{kind}:{status}": int(n) for (kind, status), n in counts.items()}, indent=2))
    print(f"Wrote {len(output)} features to {out_path}")
    return {f"{kind}:{status}": int(n) for (kind, status), n in counts.items()}


def main() -> None:
    args = args_parser()
    analyze(args.mask, args.out, args.confidence, args.community_radius_m)


if __name__ == "__main__":
    main()
