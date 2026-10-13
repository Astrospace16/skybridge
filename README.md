# SkyBridge — flood response intelligence

SkyBridge is a geospatial prototype that turns satellite observations into a flood screening map, infrastructure exposure findings, and candidate rescue routes. Its primary example is the July 2024 Wayanad floods in Kerala. It can be configured for another event by changing the AOI and dates or uploading compatible GeoTIFF/GeoJSON products.

> **Operational note:** These outputs are decision-support leads, not verified road closures or dispatch instructions. Confirm flood conditions, bridge safety, and access with local responders before use. The first detector does not estimate flood depth.

## Project structure

```text
skybridge/
├── acquisition.py            # Step 1: Sentinel-1 / Sentinel-2 via Earth Engine
├── flood_detection.py        # Step 2: speckle-filtered SAR change detection
├── infrastructure.py         # Step 3: OSM exposure and community screening
├── routing.py                # Step 4: risk-weighted A* road routes
├── dashboard.py              # Step 5: Streamlit + Folium dashboard
├── requirements.txt
├── data/
│   ├── raw/                   # satellite inputs
│   ├── processed/             # mask and confidence
│   └── events/<event-name>/   # optional dashboard event workspaces
├── outputs/                   # GeoJSON results
└── docs/
    ├── pitch.md               # 2-minute pitch + five-slide content
    └── technical_summary.md   # submission architecture summary
```

## Setup

Python 3.10+ is recommended. From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Container deployment

The dashboard is packaged as a standalone container. From this folder:

```powershell
docker build -t skybridge .
docker run --rm -p 8501:8501 skybridge
```

Open `http://localhost:8501`. The container starts the dashboard only; Earth Engine acquisition and OSM analysis remain separate pipeline commands. The image excludes local `data/` and `outputs/`, so upload event products in the dashboard or mount your event files when running the container.

### Earth Engine access

The acquisition step requires an Earth Engine account/project with API access. Authenticate once:

```powershell
earthengine authenticate
$env:EE_PROJECT = "your-earth-engine-project"
```

Alternatively, pass `--ee-project` to the acquisition script. Earth Engine's direct download is intended for small tiles and enforces a per-request size limit. For a large AOI, process tiles or replace direct downloads with Earth Engine batch exports.

## Run the full pipeline

Commands below run from `skybridge/`. Dates use start-inclusive/end-exclusive intervals. `--bbox` order is west, south, east, north in longitude/latitude.

### 1. Acquire imagery

The defaults are an approximately 11 km Wayanad sample tile and initial pre/post windows, sized to fit Earth Engine's direct-download limit. This is a demonstration subset, not the entire disaster footprint. Confirm scene availability and tune the time windows to the flood peak and local satellite passes.

```powershell
python acquisition.py --event wayanad-2024 --out-dir data/raw
```

For another event:

```powershell
python acquisition.py --event custom --bbox 75.98 11.48 76.08 11.58 `
  --pre-start 2024-07-01 --pre-end 2024-07-25 `
  --post-start 2024-07-30 --post-end 2024-08-20 `
  --out-dir data/raw --ee-project your-earth-engine-project
```

Downloads `s1_pre.tif`, `s1_post.tif` (VV dB), `s2_pre_rgbn.tif`, `s2_post_rgbn.tif` (B4/B3/B2/B8 reflectance), and `manifest.json`. All bands and periods are requested in a shared UTM CRS and 10 m grid. Sentinel-1 GRD is already calibrated/terrain corrected by Earth Engine. Sentinel-1 uses ascending passes by default in both periods; select `--orbit-pass DESCENDING` if that has better temporal coverage. Sentinel-2 composites mask cloud, shadow, cirrus, and snow classes.

### 2. Detect flood change

The detector applies a Lee filter in linear power, computes positive pre-to-post VV decrease, and finds an Otsu threshold. With Sentinel-2 stacks, rising NDWI (green/NIR) supports SAR change candidates; cloud-masked optical pixels fall back to SAR detection. Run with both modalities:

```powershell
python flood_detection.py --pre data/raw/s1_pre.tif --post data/raw/s1_post.tif `
  --optical-pre data/raw/s2_pre_rgbn.tif --optical-post data/raw/s2_post_rgbn.tif `
  --mask-out data/processed/flood_mask.tif `
  --confidence-out data/processed/flood_confidence.tif
```

Output mask values are 1=candidate, 0=not detected, 255=nodata. Confidence is a heuristic 0–1 score. Adjust `--water-db`, `--min-change-db`, `--ndwi-gain`, and `--ndwi-post` for scene conditions; `--no-water-gate` exposes change-only candidates. All rasters must share a CRS, transform, and dimensions. Without optical inputs, the detector uses SAR only.

### 3. Assess infrastructure and settlements

```powershell
python infrastructure.py --mask data/processed/flood_mask.tif `
  --confidence data/processed/flood_confidence.tif `
  --out outputs/damage.geojson
```

This queries OpenStreetMap for roads, bridges, buildings, populated places, and selected critical services. Road/bridge risk is sampled along each line. Critical sites are flagged from mask overlap. A settlement is labelled `potentially_cut_off` when a ring around its mapped location has a configured fraction of flood candidate samples. That is a transparent geographic proxy, not a full connectivity or field-access determination. If OSM has no population tag, the current prototype uses a 100-person placeholder; treat priority scores as provisional.

OSM queries require network access and may be rate-limited. Keep the AOI focused and follow OpenStreetMap attribution requirements when distributing results.

### 4. Generate candidate rescue routes

Replace the example base coordinates with a known rescue base in longitude/latitude:

```powershell
python routing.py --mask data/processed/flood_mask.tif `
  --confidence data/processed/flood_confidence.tif `
  --damage outputs/damage.geojson --base 76.03 11.53 `
  --out outputs/rescue_routes.geojson --risk-out outputs/flood_risk_index.tif
```

The router obtains a drivable OSM graph. Edges with at least 25% flood candidate samples are blocked by default; remaining edges get distance-plus-risk costs. A* creates a candidate route to each settlement flagged as potentially cut off. It writes a `flood_risk_index.tif` using flood candidates times the heuristic confidence score, and each route includes a mean sampled risk index. This index is not a calibrated probability or a flood-depth estimate. Change `--flood-cutoff` or `--risk-weight` for scenario review. A route can be absent if the remaining road graph is disconnected. A low-risk model route is not a confirmation that a road is open or safe.

### 5. Open the dashboard

```powershell
streamlit run dashboard.py
```

The dashboard accepts Sentinel-2/Sentinel-1 GeoTIFFs, the flood mask, confidence GeoTIFF, damage GeoJSON, and route GeoJSON. Upload those products in the sidebar, or place them together under `data/events/<event-name>/` with these names:

```text
s1_pre.tif, s1_post.tif, s2_pre_rgbn.tif, s2_post_rgbn.tif,
flood_mask.tif, flood_confidence.tif, flood_risk_index.tif,
damage.geojson, rescue_routes.geojson
```

The event selector includes `wayanad-2024` and any local event folders. Uploads can be mixed with local files. The map shows flood candidates, exposed infrastructure, settlements, and routes; the register displays the exposure and provisional priority information.

## What the current prototype can and cannot claim

- It combines SAR and optical imagery in one workflow, but the initial flood classifier is SAR-only so it can operate through cloud cover.
- Flood confidence is a heuristic score based on threshold margin and water-like backscatter, not a calibrated probability.
- The cut-off-community flag is a ring-water heuristic; route connectivity is separately evaluated against the OSM road graph.
- The route risk layer uses flood-mask overlap and confidence. The first increment does not provide flood depth or live road status.
- No field-validated Wayanad accuracy, deployment result, or lives-saved metric is claimed. Compare masks with reference data and validate routes with local authorities.

## Submission material

- [Pitch script and 5-slide outline](docs/pitch.md)
- [Technical summary](docs/technical_summary.md)

