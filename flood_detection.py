"""Create a SAR flood-change candidate mask and per-pixel confidence GeoTIFF."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import uniform_filter
from skimage.filters import threshold_otsu


def lee_filter_db(db: np.ndarray, valid: np.ndarray, size: int = 5) -> np.ndarray:
    """Lee filter in linear power, returning dB values."""
    linear = np.power(10.0, db.astype(np.float32) / 10.0)
    safe = np.where(valid, linear, 0.0)
    weight = uniform_filter(valid.astype(np.float32), size=size, mode="reflect")
    mean = uniform_filter(safe, size=size, mode="reflect") / np.maximum(weight, 1e-6)
    second = uniform_filter(safe * safe, size=size, mode="reflect") / np.maximum(weight, 1e-6)
    variance = np.maximum(second - mean * mean, 0.0)
    noise_variance = float(np.nanmedian(variance[valid])) if np.any(valid) else 0.0
    gain = variance / np.maximum(variance + noise_variance, 1e-8)
    filtered = mean + gain * (linear - mean)
    with np.errstate(divide="ignore", invalid="ignore"):
        filtered_db = 10.0 * np.log10(np.maximum(filtered, 1e-10))
    return np.where(valid, filtered_db, np.nan).astype(np.float32)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre", required=True, type=Path, help="Pre-event single-band VV GeoTIFF in dB")
    parser.add_argument("--post", required=True, type=Path, help="Post-event single-band VV GeoTIFF in dB")
    parser.add_argument("--optical-pre", type=Path, help="Optional pre-event B4/B3/B2/B8 Sentinel-2 stack")
    parser.add_argument("--optical-post", type=Path, help="Optional post-event B4/B3/B2/B8 Sentinel-2 stack")
    parser.add_argument("--mask-out", type=Path, default=Path("data/processed/flood_mask.tif"))
    parser.add_argument("--confidence-out", type=Path, default=Path("data/processed/flood_confidence.tif"))
    parser.add_argument("--water-db", type=float, default=-15.0,
                        help="Post-event VV water-like ceiling in dB; scene-dependent")
    parser.add_argument("--min-change-db", type=float, default=1.0,
                        help="Minimum pre-to-post VV decrease to include in Otsu population")
    parser.add_argument("--filter-size", type=int, default=5, help="Odd Lee filter window size")
    parser.add_argument("--ndwi-gain", type=float, default=0.10,
                        help="Minimum positive NDWI change for optical flood support")
    parser.add_argument("--ndwi-post", type=float, default=0.05,
                        help="Minimum post-event NDWI for optical flood support")
    parser.add_argument("--no-water-gate", action="store_true",
                        help="Classify Otsu change candidates without post-event water-like gate")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.filter_size < 3 or args.filter_size % 2 == 0:
        raise SystemExit("--filter-size must be an odd integer >= 3")
    with rasterio.open(args.pre) as pre_ds, rasterio.open(args.post) as post_ds:
        if (pre_ds.crs != post_ds.crs or pre_ds.transform != post_ds.transform or
                pre_ds.width != post_ds.width or pre_ds.height != post_ds.height):
            raise SystemExit("Pre and post rasters must have identical CRS, transform, and dimensions.")
        pre = pre_ds.read(1).astype(np.float32)
        post = post_ds.read(1).astype(np.float32)
        valid = (pre_ds.read_masks(1) > 0) & (post_ds.read_masks(1) > 0)
        valid &= np.isfinite(pre) & np.isfinite(post)
        profile = pre_ds.profile.copy()

    if not np.any(valid):
        raise SystemExit("No valid overlapping pixels in input rasters.")
    pre_f = lee_filter_db(pre, valid, args.filter_size)
    post_f = lee_filter_db(post, valid, args.filter_size)
    decrease = pre_f - post_f  # positive values indicate a VV decrease after flooding
    eligible = valid & (decrease >= args.min_change_db)
    if int(eligible.sum()) < 32:
        raise SystemExit("Too few changed pixels for Otsu thresholding; inspect the dates/AOI or lower --min-change-db.")
    eligible_values = decrease[eligible]
    threshold = float(threshold_otsu(eligible_values))
    # Otsu returns the sole value for a constant histogram; include that class
    # instead of producing an empty mask through a strict greater-than test.
    if float(np.nanmax(eligible_values) - np.nanmin(eligible_values)) < 1e-6:
        threshold = float(eligible_values[0] - 0.01)
    candidates = valid & (decrease > threshold)
    if not args.no_water_gate:
        candidates &= post_f <= args.water_db

    # Optical evidence is an optional supporting modality. Sentinel-2 scene-classification
    # masks leave cloudy pixels invalid, so those pixels continue through the SAR path.
    optical_valid = np.zeros(valid.shape, dtype=bool)
    optical_gain = np.zeros(valid.shape, dtype=np.float32)
    if bool(args.optical_pre) != bool(args.optical_post):
        raise SystemExit("Provide both --optical-pre and --optical-post, or neither.")
    if args.optical_pre and args.optical_post:
        with rasterio.open(args.optical_pre) as op_ds, rasterio.open(args.optical_post) as oq_ds:
            for optical_ds in (op_ds, oq_ds):
                if (optical_ds.crs != profile["crs"] or optical_ds.transform != profile["transform"]
                        or optical_ds.width != profile["width"] or optical_ds.height != profile["height"]
                        or optical_ds.count < 4):
                    raise SystemExit("Optical stacks must be aligned to SAR and contain B4/B3/B2/B8 bands.")
            op = op_ds.read().astype(np.float32)
            oq = oq_ds.read().astype(np.float32)
            optical_valid = (
                (op_ds.read_masks(2) > 0) & (op_ds.read_masks(4) > 0)
                & (oq_ds.read_masks(2) > 0) & (oq_ds.read_masks(4) > 0) & valid
            )
        pre_den = op[1] + op[3]
        post_den = oq[1] + oq[3]
        with np.errstate(divide="ignore", invalid="ignore"):
            ndwi_pre = np.where(np.abs(pre_den) > 1e-8, (op[1] - op[3]) / pre_den, 0)
            ndwi_post = np.where(np.abs(post_den) > 1e-8, (oq[1] - oq[3]) / post_den, 0)
        ndwi_delta = ndwi_post - ndwi_pre
        optical_gain = (
            np.clip((ndwi_delta - args.ndwi_gain) / 0.35, 0, 1)
            * np.clip((ndwi_post - args.ndwi_post) / 0.5, 0, 1)
        ).astype(np.float32)
        optical_support = optical_valid & (ndwi_delta >= args.ndwi_gain) & (ndwi_post >= args.ndwi_post)
        fused_candidates = optical_support & (decrease >= args.min_change_db)
        candidates |= fused_candidates
    flood = candidates.astype(np.uint8)

    # Confidence combines distance beyond the Otsu change threshold and water-gate margin.
    change_scale = max(float(np.nanpercentile(decrease[valid], 95) - threshold), 0.5)
    change_conf = np.clip((decrease - threshold) / change_scale, 0.0, 1.0)
    if args.no_water_gate:
        water_conf = np.ones_like(change_conf)
    else:
        water_conf = np.clip((args.water_db - post_f) / 5.0, 0.0, 1.0)
    confidence = np.where(candidates, change_conf * water_conf, 0.0).astype(np.float32)
    if args.optical_pre and args.optical_post:
        optical_confidence = 0.45 + 0.55 * optical_gain
        confidence = np.where(fused_candidates, np.maximum(confidence, optical_confidence), confidence).astype(np.float32)

    mask_profile = profile.copy()
    mask_profile.update(dtype="uint8", count=1, nodata=255, compress="deflate")
    mask_arr = np.where(valid, flood, 255).astype(np.uint8)
    args.mask_out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(args.mask_out, "w", **mask_profile) as dst:
        dst.write(mask_arr, 1)
        dst.set_band_description(1, "flood_candidate")

    confidence_profile = profile.copy()
    confidence_profile.update(dtype="float32", count=1, nodata=-9999.0, compress="deflate")
    confidence_arr = np.where(valid, confidence, -9999.0).astype(np.float32)
    args.confidence_out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(args.confidence_out, "w", **confidence_profile) as dst:
        dst.write(confidence_arr, 1)
        dst.set_band_description(1, "flood_candidate_confidence_0_1")

    print(f"Otsu VV decrease threshold: {threshold:.2f} dB")
    print(f"Flood candidates: {int(flood.sum()):,} pixels / {int(valid.sum()):,} valid")
    print(f"Mask: {args.mask_out}\nConfidence: {args.confidence_out}")


if __name__ == "__main__":
    main()
