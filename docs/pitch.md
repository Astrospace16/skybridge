# SkyBridge — pitch materials

## Two-minute pitch script

When a flood destroys roads, bridges, and phone lines, rescue teams lose the information they need most: where the damage is, which communities are cut off, and which route is still passable.

The sky can still see. SkyBridge combines cloud-robust Sentinel-1 radar with Sentinel-2 optical imagery, then turns those observations into a response map. A change-detection model highlights likely new floodwater. We overlay that footprint with OpenStreetMap roads, bridges, settlements, schools, clinics, and hospitals. The system flags exposed infrastructure and places that may be isolated, then searches for lower-risk road routes from a rescue base.

Here is the workflow on the 2024 Wayanad flood case: choose the event window, inspect the before-and-after imagery, review the flood mask and its confidence, and see the roads, critical sites, and settlements that need attention. For a candidate route, SkyBridge shows distance and the flood-risk assumptions behind it. Responders can also upload their own imagery and event layers.

SkyBridge does not claim that a satellite pixel proves a road is destroyed or a community is unreachable. It gives response teams a fast, auditable first picture when ground reports are incomplete. Every flagged road, place, and route is a lead to verify with local responders before dispatch.

The impact is a shorter path from satellite pass to an organized field assessment: where to send a scout, which facility to check, and which communities to call first. We will measure that impact with detection accuracy, time to first map, route feasibility, and the number of exposed people identified—not with unverified lives-saved claims.

The pipeline is built from open satellite data, open mapping data, and reproducible geospatial tools. With event dates and an area of interest, it can be configured for other flood events worldwide. When infrastructure is destroyed, the only infrastructure left is the sky. SkyBridge turns satellite data into rescue intelligence. It tells you where the damage may be, who may be cut off, and how to reach them. It is not just a map. It is a lifeline.

## Five-slide deck outline

### Slide 1 — The problem: responders are blind

- Floods disrupt roads, bridges, power, and communications at the same time.
- Ground reports are delayed precisely where access is hardest.
- Visual: a flood extent beside a road network with the uncertainty made visible.
- Speaker point: teams need a prioritized picture, not another raw satellite image.

### Slide 2 — The solution: satellite data into rescue intelligence

- Sentinel-1 SAR sees through cloud; Sentinel-2 adds optical context when clear.
- Change detection produces a flood candidate mask and confidence layer.
- OSM context adds roads, bridges, communities, and critical sites.
- Risk-weighted A* produces candidate road routes.
- Visual: imagery → detection → exposure → route workflow.

### Slide 3 — The demo: Wayanad 2024

- Select pre/post windows; inspect paired imagery, flood mask, and confidence.
- See exposed road segments, potentially isolated settlements, and critical sites.
- Compare candidate routes and their risk assumptions.
- Visual: dashboard screenshot from actual downloaded Wayanad inputs. Label any illustrative or simulated layer clearly.

### Slide 4 — The impact: faster assessment, better prioritization

- Prioritize field verification by exposed population, surrounding water signal, and route distance.
- Measure against reference flood maps and responder-reported road access.
- Track time from available satellite pass to map, precision/recall, route feasibility, and exposed population coverage.
- Avoid claiming lives saved until an operational evaluation supports it.

### Slide 5 — The scalability: a reusable event workflow

- Change the area and dates; ingest any compatible GeoTIFF and GeoJSON.
- Open Sentinel and OSM sources support repeatable event setup.
- A small-area download is the prototype path; large AOIs use tiled or batch exports.
- Target: a first screening map within 24 hours of usable imagery and event definition, subject to data availability, processing, and validation.

