"""Local Streamlit dashboard for SkyBridge event products and user uploads."""

from __future__ import annotations

import json
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import rasterio
import streamlit as st
from folium.raster_layers import ImageOverlay
from pyproj import Transformer
from streamlit_folium import st_folium


st.set_page_config(page_title="SkyBridge | Flood Rescue Intelligence", page_icon="🌊", layout="wide")


def existing_events() -> list[str]:
    root = Path("data/events")
    local = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
    return sorted(set(["wayanad-2024", *local]))


def read_raster(upload) -> tuple[np.ndarray, np.ndarray, tuple[float, float, float, float], str]:
    data = upload.getvalue()
    with rasterio.MemoryFile(data) as mem:
        with mem.open() as ds:
            arr = ds.read()
            valid = ds.read_masks(1) > 0
            b = ds.bounds
            crs = ds.crs
            to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            west, south = to_wgs.transform(b.left, b.bottom)
            east, north = to_wgs.transform(b.right, b.top)
            return arr, valid, (west, south, east, north), str(crs)


def preview(upload, kind: str) -> np.ndarray | None:
    if upload is None:
        return None
    arr, valid, _, _ = read_raster(upload)
    if kind == "rgb" and arr.shape[0] >= 3:
        rgb = np.moveaxis(arr[:3], 0, -1).astype(np.float32)
        if np.nanmax(rgb) > 2:
            rgb *= 0.0001
        return np.clip(rgb / 0.30, 0, 1)
    layer = arr[0].astype(np.float32)
    lo, hi = np.nanpercentile(layer[valid], [2, 98]) if valid.any() else (0, 1)
    return np.clip((layer - lo) / max(hi - lo, 1e-6), 0, 1)


def load_geojson(upload, local_path: Path | None):
    if upload is not None:
        return json.loads(upload.getvalue().decode("utf-8"))
    if local_path and local_path.exists():
        return json.loads(local_path.read_text(encoding="utf-8"))
    return None


st.title("🌊 SkyBridge")
st.caption("Satellite observations to flood response intelligence · Decision support, not a substitute for field verification")

events = existing_events()
event_choice = st.sidebar.selectbox("Event workspace", ["Upload / choose files"] + events)
event_dir = Path("data/events") / event_choice if event_choice != "Upload / choose files" else None
st.sidebar.markdown("### Add event data")
st.sidebar.caption("Upload GeoTIFFs and GeoJSONs from the SkyBridge pipeline, or your own compatible files.")

def local_or_upload(upload, filename: str):
    candidate = event_dir / filename if event_dir else None
    if upload is not None:
        return upload
    if candidate and candidate.exists():
        class LocalUpload:
            name = candidate.name
            def getvalue(self):
                return candidate.read_bytes()
        return LocalUpload()
    return None


with st.sidebar.expander("Satellite rasters", expanded=True):
    pre_opt_upload = st.file_uploader("Pre-event optical RGB + NIR", type=["tif", "tiff"], key="pre_opt")
    post_opt_upload = st.file_uploader("Post-event optical RGB + NIR", type=["tif", "tiff"], key="post_opt")
    pre_sar_upload = st.file_uploader("Pre-event Sentinel-1 VV", type=["tif", "tiff"], key="pre_sar")
    post_sar_upload = st.file_uploader("Post-event Sentinel-1 VV", type=["tif", "tiff"], key="post_sar")
    mask_upload = st.file_uploader("Flood mask", type=["tif", "tiff"], key="mask")
    confidence_upload = st.file_uploader("Confidence map", type=["tif", "tiff"], key="confidence")
    risk_upload = st.file_uploader("Flood risk index", type=["tif", "tiff"], key="risk")
with st.sidebar.expander("Vector outputs", expanded=True):
    damage_upload = st.file_uploader("Infrastructure / settlement GeoJSON", type=["geojson", "json"], key="damage")
    routes_upload = st.file_uploader("Rescue routes GeoJSON", type=["geojson", "json"], key="routes")

pre_opt = local_or_upload(pre_opt_upload, "s2_pre_rgbn.tif")
post_opt = local_or_upload(post_opt_upload, "s2_post_rgbn.tif")
pre_sar = local_or_upload(pre_sar_upload, "s1_pre.tif")
post_sar = local_or_upload(post_sar_upload, "s1_post.tif")
mask_file = local_or_upload(mask_upload, "flood_mask.tif")
confidence_file = local_or_upload(confidence_upload, "flood_confidence.tif")
risk_file = local_or_upload(risk_upload, "flood_risk_index.tif")
damage = load_geojson(damage_upload, event_dir / "damage.geojson" if event_dir else None)
routes = load_geojson(routes_upload, event_dir / "rescue_routes.geojson" if event_dir else None)

with st.sidebar:
    st.markdown("### Map layers")
    show_flood = st.checkbox("Flood candidate mask", True)
    show_risk = st.checkbox("Heuristic flood risk index", False)
    show_damage = st.checkbox("Infrastructure and settlements", True)
    show_routes = st.checkbox("Candidate rescue routes", True)
    show_conf = st.checkbox("Show confidence transparency", False)

left, right = st.columns(2)
with left:
    st.subheader("Before the event")
    if pre_opt:
        st.image(preview(pre_opt, "rgb"), caption="Sentinel-2 composite (B4/B3/B2 display; B8 NIR supports NDWI)", use_container_width=True)
    elif pre_sar:
        st.image(preview(pre_sar, "gray"), caption="Sentinel-1 VV backscatter", use_container_width=True)
    else:
        st.info("Upload or select pre-event optical/SAR imagery.")
with right:
    st.subheader("After the event")
    if post_opt:
        st.image(preview(post_opt, "rgb"), caption="Sentinel-2 composite (B4/B3/B2 display; B8 NIR supports NDWI)", use_container_width=True)
    elif post_sar:
        st.image(preview(post_sar, "gray"), caption="Sentinel-1 VV backscatter", use_container_width=True)
    else:
        st.info("Upload or select post-event optical/SAR imagery.")

if not mask_file and not damage and not routes:
    st.info("Add a flood mask or pipeline GeoJSON outputs to populate the operational map. Run acquisition.py and flood_detection.py first.")

map_center = [11.5, 76.05]
map_bounds = None
mask_data = None
if mask_file:
    mask_data, _, map_bounds, mask_crs = read_raster(mask_file)
    map_center = [(map_bounds[1] + map_bounds[3]) / 2, (map_bounds[0] + map_bounds[2]) / 2]
    st.caption(f"Mask grid CRS: {mask_crs} · Choose layers in the sidebar")

m = folium.Map(location=map_center, zoom_start=11, tiles="CartoDB positron", control_scale=True)
if mask_data is not None and show_flood and map_bounds:
    mask = mask_data[0]
    rgba = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
    flooded = mask == 1
    rgba[flooded] = [15, 116, 189, 155]
    if confidence_file and show_conf:
        conf_data, _, _, _ = read_raster(confidence_file)
        if conf_data[0].shape == mask.shape:
            alpha = np.clip(conf_data[0], 0, 1) * 190
            rgba[flooded, 3] = alpha[flooded].astype(np.uint8)
        else:
            st.sidebar.warning("Confidence map grid differs from mask; transparency disabled.")
    bounds = [[map_bounds[1], map_bounds[0]], [map_bounds[3], map_bounds[2]]]
    ImageOverlay(rgba, bounds=bounds, opacity=0.8, name="Flood candidates", interactive=True, cross_origin=False, zindex=2).add_to(m)
if risk_file and show_risk:
    risk_data, _, risk_bounds, _ = read_raster(risk_file)
    risk = np.clip(risk_data[0], 0, 1)
    risk_rgba = np.zeros((*risk.shape, 4), dtype=np.uint8)
    risk_rgba[..., 0] = 255
    risk_rgba[..., 1] = (220 * (1 - risk)).astype(np.uint8)
    risk_rgba[..., 2] = 25
    risk_rgba[..., 3] = (risk * 210).astype(np.uint8)
    bounds = [[risk_bounds[1], risk_bounds[0]], [risk_bounds[3], risk_bounds[2]]]
    ImageOverlay(risk_rgba, bounds=bounds, name="Heuristic flood risk index", interactive=True, cross_origin=False, zindex=3).add_to(m)
if damage and show_damage:
    prop_keys = {k for feature in damage.get("features", []) for k in feature.get("properties", {})}
    tooltip_fields = [k for k in ("name", "feature_type", "status", "population", "flood_fraction") if k in prop_keys]
    tooltip = folium.GeoJsonTooltip(fields=tooltip_fields) if tooltip_fields else None
    folium.GeoJson(damage, name="Infrastructure and settlements", style_function=lambda f: {
        "color": "#df2935" if f.get("properties", {}).get("status") in ("flood_exposed", "at_risk", "potentially_cut_off") else "#5c677d",
        "weight": 3 if f.get("properties", {}).get("feature_type") in ("road", "bridge") else 1,
        "fillOpacity": 0.5,
    }, tooltip=tooltip).add_to(m)
if routes and show_routes:
    folium.GeoJson(routes, name="Candidate rescue routes", style_function=lambda _: {"color": "#f08c00", "weight": 5}).add_to(m)
folium.LayerControl().add_to(m)
st.subheader("Flood response map")
st_folium(m, height=590, use_container_width=True, returned_objects=[])

if damage:
    rows = [f.get("properties", {}) for f in damage.get("features", [])]
    if rows:
        st.subheader("Priority and exposure register")
        df = pd.DataFrame(rows)
        cols = [c for c in ("name", "feature_type", "status", "population", "priority_score", "flood_fraction", "surrounding_flood_fraction", "mean_confidence") if c in df.columns]
        if "population" in df.columns:
            severity_values = df["surrounding_flood_fraction"] if "surrounding_flood_fraction" in df.columns else (
                df["flood_fraction"] if "flood_fraction" in df.columns else pd.Series(0.0, index=df.index)
            )
            df["priority_score"] = (
                45 * np.clip(np.log1p(pd.to_numeric(df.get("population", 0), errors="coerce").fillna(0)) / np.log1p(5000), 0, 1)
                + 55 * pd.to_numeric(severity_values, errors="coerce").fillna(0).clip(0, 1)
            )
            cols.append("priority_score")
        st.dataframe(df[cols].sort_values(cols[-1], ascending=False) if "priority_score" in cols else df[cols], use_container_width=True, hide_index=True)
if routes:
    features = routes.get("features", [])
    if features:
        st.subheader("Candidate route register")
        st.dataframe(pd.DataFrame([f.get("properties", {}) for f in features]), use_container_width=True, hide_index=True)

st.caption("SkyBridge outputs are screening and planning aids. Validate current flood conditions, road status, bridge safety, and route access with local authorities before dispatch.")
