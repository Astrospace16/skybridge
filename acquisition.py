"""Download compact Sentinel-1 and Sentinel-2 composites with Earth Engine."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import ee
import requests


WAYANAD = {
    # Compact Wayanad sample tile (about 11 km across), sized for direct downloads.
    "bbox": (75.98, 11.48, 76.08, 11.58),
    "pre_start": "2024-07-01",
    "pre_end": "2024-07-25",
    "post_start": "2024-07-30",
    "post_end": "2024-08-20",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", default="wayanad-2024", help="Event label stored in the manifest")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                        help="AOI bounds in lon/lat; defaults to a compact Wayanad example")
    parser.add_argument("--pre-start", default=WAYANAD["pre_start"])
    parser.add_argument("--pre-end", default=WAYANAD["pre_end"])
    parser.add_argument("--post-start", default=WAYANAD["post_start"])
    parser.add_argument("--post-end", default=WAYANAD["post_end"])
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--ee-project", default=os.environ.get("EE_PROJECT"),
                        help="Earth Engine-enabled Google Cloud project (or set EE_PROJECT)")
    parser.add_argument("--scale", type=int, default=10, help="Output pixel size in metres")
    parser.add_argument("--orbit-pass", choices=("ASCENDING", "DESCENDING"), default="ASCENDING",
                        help="Use one pass direction in both periods to reduce geometry mismatch")
    return parser.parse_args()


def initialize_earth_engine(project: str | None) -> None:
    try:
        if project:
            ee.Initialize(project=project)
        else:
            ee.Initialize()
    except Exception as exc:
        raise RuntimeError(
            "Earth Engine initialization failed. Authenticate with `earthengine authenticate` "
            "and provide --ee-project or set EE_PROJECT."
        ) from exc


def download_image(image: ee.Image, bands: list[str], bbox: tuple[float, ...],
                   scale: int, destination: Path, target_crs: str) -> None:
    region = ee.Geometry.BBox(*bbox)
    url = image.select(bands).getDownloadURL({
        "name": destination.stem,
        "bands": bands,
        "region": region,
        "scale": scale,
        "crs": target_crs,
        "format": "GEO_TIFF",
    })
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)


def sentinel1_composite(start: str, end: str, region: ee.Geometry, orbit_pass: str) -> tuple[ee.Image, int]:
    collection = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.eq("orbitProperties_pass", orbit_pass))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
    )
    count = collection.size().getInfo()
    if count == 0:
        raise RuntimeError(f"No Sentinel-1 IW/VV scenes found for {start} to {end}.")
    return collection.median().rename("VV"), count


def mask_s2_clouds(image: ee.Image) -> ee.Image:
    scl = image.select("SCL")
    clear = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
    # Harmonized SR reflectance is stored with a 10000 scale factor.
    return image.updateMask(clear).select(["B4", "B3", "B2", "B8"]).multiply(0.0001)


def sentinel2_composite(start: str, end: str, region: ee.Geometry) -> tuple[ee.Image, int]:
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90))
        .map(mask_s2_clouds)
    )
    count = collection.size().getInfo()
    if count == 0:
        raise RuntimeError(f"No Sentinel-2 SR scenes found for {start} to {end}.")
    return collection.median().rename(["B4", "B3", "B2", "B8"]), count


def main() -> None:
    args = parse_args()
    bbox = tuple(args.bbox) if args.bbox else WAYANAD["bbox"]
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise SystemExit("Invalid bbox: require west < east and south < north.")
    for value in (args.pre_start, args.pre_end, args.post_start, args.post_end):
        datetime.strptime(value, "%Y-%m-%d")

    initialize_earth_engine(args.ee_project)
    region = ee.Geometry.BBox(*bbox)
    zone = max(1, min(60, int((west + east) / 2 + 180) // 6 + 1))
    target_crs = f"EPSG:{32600 + zone if (south + north) / 2 >= 0 else 32700 + zone}"
    periods = {
        "pre": (args.pre_start, args.pre_end),
        "post": (args.post_start, args.post_end),
    }
    manifest: dict[str, Any] = {
        "event": args.event,
        "bbox_lonlat": list(bbox),
        "scale_m": args.scale,
        "output_crs": target_crs,
        "sentinel1_orbit_pass": args.orbit_pass,
        "periods": {},
        "generated_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "sources": {"sar": "COPERNICUS/S1_GRD", "optical": "COPERNICUS/S2_SR_HARMONIZED"},
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for label, (start, end) in periods.items():
        s1, s1_count = sentinel1_composite(start, end, region, args.orbit_pass)
        s2, s2_count = sentinel2_composite(start, end, region)
        s1_path = args.out_dir / f"s1_{label}.tif"
        s2_path = args.out_dir / f"s2_{label}_rgbn.tif"
        download_image(s1, ["VV"], bbox, args.scale, s1_path, target_crs)
        download_image(s2, ["B4", "B3", "B2", "B8"], bbox, args.scale, s2_path, target_crs)
        manifest["periods"][label] = {
            "start_inclusive": start,
            "end_exclusive": end,
            "sentinel1_scene_count": s1_count,
            "sentinel2_scene_count": s2_count,
            "files": {"sar_vv_db": s1_path.name, "optical_rgb": s2_path.name},
        }
        print(f"Downloaded {label}: S1 {s1_count} scenes, S2 {s2_count} scenes")

    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
