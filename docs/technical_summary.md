# SkyBridge — technical summary

## Architecture and data

SkyBridge is a Python geospatial workflow for rapid flood-response screening. The acquisition stage queries Google Earth Engine for Sentinel‑1 GRD VV backscatter and Sentinel‑2 surface-reflectance RGB composites in pre-event and post-event windows. Sentinel‑1 GRD in Earth Engine is delivered as calibrated backscatter in decibels; the script filters to IW/VV and computes a temporal median. Sentinel‑2 uses the harmonized surface-reflectance collection, masks scene-classification cloud, shadow, cirrus, and snow classes, and outputs B4/B3/B2. OpenStreetMap features are fetched with OSMnx for roads, bridges, buildings, populated places, and selected critical services.

## Models and processing

The first flood detector is an unsupervised change-detection baseline, not a trained neural network. It applies a Lee filter to SAR intensity in linear power, computes positive pre-to-post VV decrease, and derives a scene-specific Otsu threshold. When cloud-masked Sentinel-2 pixels are available, a gain in green/NIR NDWI supports SAR change candidates; cloudy pixels retain the SAR-only path. Outputs include a byte flood candidate mask and a 0–1 heuristic confidence score based on change margin, water-like backscatter, and optical water-index support. Parameters must be calibrated for acquisition geometry, terrain, land cover, and event conditions.

The infrastructure stage samples the flood mask along OSM road geometries and at critical sites. It flags exposed segments and sites from overlap fractions. “Potentially cut off” uses a transparent screening proxy: water samples on a ring around a mapped settlement. It is not a graph-theoretic proof of isolation. The routing stage downloads a drivable OSM graph, samples each edge against the mask, blocks edges above a flood-exposure threshold, and applies A* to minimize length plus a confidence-weighted flood-risk penalty. It exports a heuristic raster index (candidate mask × confidence), not a calibrated road-failure probability or flood-depth surface. Route priority combines population, surrounding flood signal, and distance from the rescue base. Routes remain candidates until confirmed by current field information.

## Outputs and novelty

The Streamlit dashboard presents paired imagery, flood mask, confidence, infrastructure findings, settlements, and candidate routes on a Folium map. GeoTIFF and GeoJSON inputs can be uploaded for another event. The contribution is the operational chain from multimodal satellite observations to exposure prioritization and route candidates, rather than a claim that any single flood-mapping method is novel. The prototype fuses SAR and optical context while keeping the detection result and uncertainty visible to users.

## Real-world impact and limitations

Disaster response agencies such as India's NDRF and NDMA could use a validated deployment as an early situational-awareness layer: task field teams to verify flagged bridges, identify facilities that need contact, and compare access options. Integration would require agency-approved data handling, local road and population datasets, robust large-area exports, validation against reference flood maps, and human approval before dispatch. OSM completeness varies; SAR can confuse smooth surfaces, terrain shadow, and built-up changes with water; Sentinel-2 may be cloud-obscured; and the prototype does not estimate flood depth. Population defaults are placeholders when OSM has no population tag. No field-validated Wayanad result or lives-saved estimate is claimed here.

