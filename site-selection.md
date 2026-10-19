# Hydro Homestead Site Finder (HHSF) — Requirements & Engineering Plan

**Bundle:** Software Requirements Specification v1.0 (Part I) + Engineering Plan v0.1 (Part II) **Project:** Hydro Homestead Site Finder — geospatial platform to identify and rank hydro-powered homestead sites near Chattanooga, TN **Status:** Part I is the requirements baseline; Part II is the planning response (no application code yet)

## How to read this document

This document bundles two artifacts of different kinds, kept deliberately distinct:

- **Part I — Software Requirements Specification (SRS)** states *what* the system must do and the constraints it must satisfy (functional requirements, non-functional limits, scope, success criteria, and the initial database schema). It is the **baseline of record** and is reproduced unchanged from the source.
- **Part II — Engineering Plan** is the technical response: *how* the system will be built, in what order, with which data, plus a **critique of Part I** where the requirements as written are infeasible, ambiguous, or self-conflicting.

Where the two conflict, **Part II §2 (Critique)** and **§3 (Recommended improvements)** record *proposed deviations* from the baseline — for example, the restructured hydro-scoring model and the database-schema changes. Part I remains authoritative until a deviation is formally accepted, at which point Part I should be revised and re-baselined. In short: Part I is the stable target; Part II is the living plan that interrogates and routes around the target’s gaps.
## Part I — Software Requirements Specification (SRS)

*HHSF · Version 1.0 · reproduced unchanged from the source specification.*

### 1. Purpose

The Hydro Homestead Site Finder (HHSF) is a geospatial analysis platform designed to identify and rank potential homestead locations within a specified geographic region.

The platform is optimized for identifying sites suitable for residential development, agricultural use, livestock operations, long-term water security, and micro-hydroelectric power generation.

Unlike conventional real estate search tools, HHSF evaluates physical land suitability independently of current market availability and can identify opportunities for off-market acquisition and subdivision.

### 2. System Objectives

The system shall:

- Identify candidate hydro corridors.
- Evaluate hydroelectric potential.
- Evaluate homestead suitability.
- Rank candidate sites.
- Overlay parcel ownership data.
- Identify subdivision opportunities.
- Generate acquisition reports.
- Provide an interactive mapping interface.

### 3. Scope

**Included:** GIS analysis, watershed analysis, terrain analysis, parcel analysis, ownership lookup, site scoring, report generation.

**Excluded (Version 1.0):** automated owner outreach, automated valuation models, machine learning prediction, real-time MLS integration, mobile application.

### 4. Users

**Primary user:** individual homestead seeker.

**Future users:** land investors, conservation buyers, agricultural developers, hydro developers.

### 5. Functional Requirements

#### FR-1 Data Ingestion

System shall import the following datasets:

- **Terrain** — Source: USGS DEM / LiDAR. Attributes: elevation, slope, aspect.
- **Hydrography** — Source: NHDPlus. Attributes: stream type, flowline geometry, perennial indicator.
- **Soils** — Source: USDA SSURGO. Attributes: soil class, drainage, prime farmland designation, septic suitability.
- **Flood** — Source: FEMA NFHL. Attributes: flood zone, floodplain geometry.
- **Parcels** — Source: County GIS. Attributes: parcel ID, acreage, geometry.
- **Ownership** — Source: County assessor. Attributes: owner, mailing address, tax information.

#### FR-2 Hydro Corridor Detection

System shall identify perennial streams, delineate watersheds, calculate upstream watershed area, calculate stream gradient, and estimate available head.

Output: hydro corridor candidates.

#### FR-3 Hydro Scoring

System shall calculate a Hydro Score (0–100).

| Factor | Weight |
| --- | --- |
| Head | 40% |
| Watershed Area | 30% |
| Stream Length | 15% |
| Rainfall | 15% |

#### FR-4 Buildability Analysis

System shall evaluate slope, flood risk, septic suitability, and road access.

Output: Buildability Score.

#### FR-5 Agricultural Analysis

System shall evaluate prime farmland, soil capability, drainage, and pasture suitability.

Output: Agriculture Score.

#### FR-6 Privacy Analysis

System shall evaluate nearby structures, road visibility, and development density.

Output: Privacy Score.

#### FR-7 Composite Ranking

System shall calculate an Overall Score.

| Category | Weight |
| --- | --- |
| Hydro | 40% |
| Buildability | 20% |
| Agriculture | 20% |
| Privacy | 10% |
| Logistics | 10% |

#### FR-8 Parcel Overlay

System shall associate candidate sites with parcel geometry, ownership, and acreage.

#### FR-9 Subdivision Feasibility

System shall evaluate road frontage, buildable area, access feasibility, and hydro retention.

Output: Subdivision Feasibility Score.

#### FR-10 Reporting

System shall generate PDF and HTML reports containing maps, scores, ownership information, and acquisition notes.

### 6. Non-Functional Requirements

- **Performance:** analyze a county-sized dataset within < 15 minutes.
- **Storage:** support a 100 GB+ geospatial database.
- **Availability:** single-user application; no high-availability requirements.
- **Security:** local deployment; no public internet exposure required.

### 7. Recommended Architecture

- **Frontend:** React, MapLibre
- **API:** FastAPI
- **GIS processing:** Python
- **Database:** PostgreSQL, PostGIS
- **Deployment:** Docker

### 8. Database Schema

#### Table: counties

```sql
CREATE TABLE counties (
    county_id SERIAL PRIMARY KEY,
    state_code VARCHAR(2),
    county_name VARCHAR(100),
    geometry GEOMETRY(MULTIPOLYGON,4326)
);
```

#### Table: parcels

```sql
CREATE TABLE parcels (
    parcel_id BIGSERIAL PRIMARY KEY,
    county_id INT REFERENCES counties(county_id),
    assessor_parcel_number VARCHAR(100),
    acreage NUMERIC(10,2),
    geometry GEOMETRY(MULTIPOLYGON,4326)
);
```

#### Table: parcel_owners

```sql
CREATE TABLE parcel_owners (
    owner_id BIGSERIAL PRIMARY KEY,
    owner_name TEXT,
    mailing_address TEXT,
    city TEXT,
    state TEXT,
    zip_code TEXT
);
```

#### Table: parcel_owner_link

```sql
CREATE TABLE parcel_owner_link (
    parcel_id BIGINT REFERENCES parcels(parcel_id),
    owner_id BIGINT REFERENCES parcel_owners(owner_id),
    PRIMARY KEY(parcel_id, owner_id)
);
```

#### Table: streams

```sql
CREATE TABLE streams (
    stream_id BIGSERIAL PRIMARY KEY,
    nhd_id BIGINT,
    perennial BOOLEAN,
    stream_name TEXT,
    geometry GEOMETRY(LINESTRING,4326)
);
```

#### Table: watersheds

```sql
CREATE TABLE watersheds (
    watershed_id BIGSERIAL PRIMARY KEY,
    stream_id BIGINT REFERENCES streams(stream_id),
    area_acres NUMERIC,
    geometry GEOMETRY(POLYGON,4326)
);
```

#### Table: hydro_corridors

```sql
CREATE TABLE hydro_corridors (
    corridor_id BIGSERIAL PRIMARY KEY,
    stream_id BIGINT REFERENCES streams(stream_id),
    head_ft NUMERIC,
    watershed_area_acres NUMERIC,
    stream_length_ft NUMERIC,
    estimated_power_w NUMERIC,
    hydro_score NUMERIC
);
```

#### Table: soils

```sql
CREATE TABLE soils (
    soil_id BIGSERIAL PRIMARY KEY,
    mukey VARCHAR(50),
    prime_farmland BOOLEAN,
    drainage_class VARCHAR(50),
    capability_class VARCHAR(20),
    geometry GEOMETRY(MULTIPOLYGON,4326)
);
```

#### Table: flood_zones

```sql
CREATE TABLE flood_zones (
    flood_id BIGSERIAL PRIMARY KEY,
    zone_code VARCHAR(20),
    geometry GEOMETRY(MULTIPOLYGON,4326)
);
```

#### Table: candidate_sites

```sql
CREATE TABLE candidate_sites (
    site_id BIGSERIAL PRIMARY KEY,
    corridor_id BIGINT REFERENCES hydro_corridors(corridor_id),
    parcel_id BIGINT REFERENCES parcels(parcel_id),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    usable_acres NUMERIC,
    hydro_score NUMERIC,
    agriculture_score NUMERIC,
    buildability_score NUMERIC,
    privacy_score NUMERIC,
    logistics_score NUMERIC,
    total_score NUMERIC
);
```

#### Table: subdivision_analysis

```sql
CREATE TABLE subdivision_analysis (
    analysis_id BIGSERIAL PRIMARY KEY,
    parcel_id BIGINT REFERENCES parcels(parcel_id),
    frontage_ft NUMERIC,
    buildable_acres NUMERIC,
    hydro_retained BOOLEAN,
    subdivision_score NUMERIC
);
```

### 9. Version 1 Success Criteria

The system shall successfully:

- Ingest GIS data for a county.
- Identify hydro corridors.
- Calculate hydro suitability.
- Identify candidate homesites.
- Rank sites.
- Associate ownership records.
- Generate acquisition reports.
- Support investigation of off-market acquisition opportunities.

At completion, the user should be able to answer: *“What are the best hydro-powered homestead opportunities within driving distance of Chattanooga, regardless of whether they are currently listed for sale?”*

---

## Part II — Engineering Plan

*Prepared as the technical response to Part I. Version 0.1 · planning deliverable.*

### 0. Executive summary

The SRS is a good vision document. It defines the right problem (find physically suitable hydro-homestead sites independent of listing status, then overlay ownership) and picks a sound stack (PostGIS + Python GIS + FastAPI + MapLibre + Docker). I would not change the strategic direction.

I am recommending changes in five areas before we build, because they materially affect correctness, reproducibility, and the “explainable scoring” mandate:

1. **The hydro scoring model double-counts flow.** Three of its four factors (watershed area, stream length, rainfall) are correlated proxies for one physical variable — streamflow. Only **head** and **flow** are physically independent. I recommend restructuring the hydro score around **net head** and **firm (reliable low) flow**, which together give estimated **firm power** — the quantity the schema already wants in `estimated_power_w` but that the current score never actually uses.
2. **The schema cannot support correct GIS analysis as written.** Everything is stored in EPSG:4326 (degrees), but slope, area, length, and distance must be computed in a projected CRS. There are no spatial indexes (fatal for the 100 GB / 15-minute targets), no provenance/confidence columns (required by the “don’t fabricate values” and reporting mandates), and no analysis-run table (required for reproducible, explainable rankings).
3. **The hydrography data source has changed.** The USGS retired the National Hydrography Dataset on October 1, 2023 and halted NHDPlus HR production in favor of the new 3D Hydrography Program (3DHP), which is several years from nationwide completeness. NHDPlus HR remains available as a frozen, DOI-referenced “bridge” dataset. For v0.1 we should use the **final-release NHDPlus HR** (it ships the pre-computed drainage area, flow, and elevation attributes our pipeline needs) and design the hydrography source to be swappable to 3DHP when Tennessee coverage lands.
4. **Three functional requirements have no data source.** Road access (FR-4), logistics and driving distance (FR-7, success criterion), road frontage (FR-9), privacy/structures (FR-6), and the rainfall factor (FR-3) all depend on datasets that FR-1 never lists. They need to be added.
5. **DEM resolution and the analysis split need to be pinned down for the performance budget.** A county at 1 m LiDAR is hundreds of millions of cells and will blow the 15-minute target. Use 10 m (1/3 arc-second) 3DEP for watershed-scale hydrology; reserve 1 m LiDAR for site-specific slope later. Push vector joins into PostGIS; keep GeoPandas as glue, not as the join engine.

Everything below expands on these and lays out the build.

---

### 1. Architecture review

#### 1.1 Verdict on the proposed stack

The proposed stack is appropriate and I am keeping it. One clarification matters most: **this is a headless geospatial analysis platform with a thin read API and a map viewer bolted on — not a web app with some GIS inside it.** The center of gravity is the ingestion → analysis → scoring pipeline. The API and frontend are projections of results that the pipeline has already computed and persisted. Build it in that order.

#### 1.2 Where each tool lives (this is the decision that keeps the codebase clean)

| Concern | Tool | Why |
| --- | --- | --- |
| Raster hydrology (depression fill/breach, flow direction, flow accumulation, watershed-from-pour-point, stream-from-threshold) | **WhiteboxTools** | Purpose-built, fast, deterministic CLI/Python; far better than rolling our own raster routing |
| Large vector overlays and spatial joins (parcel ↔︎ corridor, soils ↔︎ site, flood ↔︎ site) | **PostGIS (SQL)** | Push the join to where the GiST indexes are. Do **not** pull whole layers into Python |
| Attribute logic, scoring math, geometry assembly, orchestration | **GeoPandas / Shapely / PyProj** | The glue layer; light vector work and the scoring engine |
| Raster I/O, windowed reads, DEM sampling | **Rasterio** | Window/tile to stay within memory; never load a county raster whole |
| Tabular/flow services | **HTTP clients to USGS StreamStats / Soil Data Access** | Authoritative ungauged-flow and soils attributes (see §4) |

The anti-pattern to avoid, explicitly: doing the parcel/soils/flood overlays in GeoPandas. That loads everything into Python memory, ignores the spatial index, and will not meet the 15-minute NFR on a county. Ingestion writes to PostGIS; analysis joins happen in SQL; GeoPandas reads back only what it needs.

#### 1.3 Component diagram

```
                          ┌─────────────────────────────────────────┐
                          │            CONFIG (versioned)            │
                          │  AOI defs · scoring profiles · CRS · src │
                          └───────────────────┬─────────────────────┘
                                              │ (read by every stage)
   ┌──────────────┐   ┌───────────────────────▼───────────────────────┐
   │ EXTERNAL DATA │──▶│                 INGEST LAYER                   │
   │ 3DEP DEM      │   │  source adapters → normalize → load to PostGIS │
   │ NHDPlus HR    │   │  idempotent · source + vintage recorded        │
   │ SSURGO (SDA)  │   └───────────────────────┬───────────────────────┘
   │ FEMA NFHL     │                           │
   │ TN parcels    │                           ▼
   │ TIGER roads   │            ┌──────────────────────────────┐
   │ bldg footprnt │            │   POSTGIS (canonical store)   │
   │ PRISM/Atlas14 │            │  4326 geometry + projected    │
   │ StreamStats   │◀──────┐    │  geometry + provenance        │
   └──────────────┘       │    └───────────────┬──────────────┘
                          │                    │
                  ┌───────┴────────┐           ▼
                  │  ANALYSIS LAYER │  ┌────────────────────────────┐
                  │  terrain ·hydro │  │      SCORING ENGINE         │
                  │  soils · flood  │─▶│  absolute, profile-driven   │
                  │  parcels ·access│  │  every factor explainable   │
                  └───────┬────────┘  └───────────────┬─────────────┘
                          │                            │
                          ▼                            ▼
                  ┌────────────────┐          ┌─────────────────────┐
                  │  ANALYSIS_RUNS  │          │   RESULTS in PostGIS │
                  │  (params,vers.) │◀────────▶│ corridors · sites ·  │
                  └────────────────┘          │ scores (run-scoped)  │
                                              └─────────┬────────────┘
                                                        │
                          ┌─────────────────────────────┼───────────────┐
                          ▼                             ▼                ▼
                   ┌─────────────┐            ┌──────────────┐   ┌──────────────┐
                   │ REPORTS     │            │ FastAPI read │   │ MapLibre web │
                   │ CSV·GeoJSON │            │ API (+tiles) │   │ viewer       │
                   │ PDF/HTML    │            └──────────────┘   └──────────────┘
                   └─────────────┘
```

#### 1.4 Data flow (v0.1, Sequatchie County)

```
AOI = Sequatchie County boundary (seed)
  │
  ├─▶ Ingest NHDPlus HR flowlines for the containing HU → filter perennial (FCode) → streams
  │     └─ carry VAA: cumulative drainage area, EROM flow est., smoothed min/max elev
  ├─▶ Ingest 10 m 3DEP DEM clipped to AOI (+ small buffer)
  │
  ├─▶ Per perennial reach:
  │     ├─ watershed area  ← NHDPlus VAA (fast path) [DEM delineation = later refinement]
  │     ├─ gross head      ← reach max−min smoothed elevation (or DEM-sampled endpoints)
  │     ├─ reach length    ← projected geometry length
  │     ├─ firm flow Qf    ← StreamStats flow-duration / low-flow at pour point (flagged)
  │     └─ est. firm power ← P = ρ·g·Qf·H·η  (η documented; flags on low-confidence inputs)
  │
  ├─▶ Hydro score = absolute function of (net head, firm power), profile-driven, explainable
  ├─▶ Rank corridors
  └─▶ Persist (run-scoped) + export CSV/GeoJSON + summary report
```

---

### 2. Critique of the current SRS

Organized by severity. Each item is **(issue → why it matters → recommendation)**.

#### 2.A Scoring model

**A1 — The hydro score double-counts (arguably triple-counts) flow.** FR-3 weights Head 40 %, Watershed Area 30 %, Stream Length 15 %, Rainfall 15 %. Physically, hydro power depends on exactly two independent things: **head** and **flow**. Watershed area, rainfall, and (loosely) stream length are all proxies for *flow* — bigger basin + more rain ⇒ more runoff ⇒ more flow. Weighting them as separate factors counts flow three times under three names, and the apparent weights don’t mean what they look like. *Recommendation:* collapse the flow proxies into a single **flow** estimate and score on **head + flow → firm power**. Keep the weight table as a *fallback/screening* profile, but make the physics-based score the primary.

**A2 — The score never uses the power it asks the schema to compute.** `hydro_corridors.estimated_power_w` requires flow Q (P = ρ·g·Q·H·η). The FR-3 score contains no flow term at all. These are two disconnected notions of “how good is the hydro.” *Recommendation:* make the hydro score a documented function of `estimated_power_w` (specifically firm power), so the number we compute and the number we rank on are the same lineage.

**A3 — Stream length as a power factor is not physically justified.** Longer stream ≠ more power. A longer reach may offer more *siting flexibility* (more places to find a usable head drop), which is real but secondary. *Recommendation:* drop length from the hydro power score; optionally reintroduce it as a small “siting flexibility” term, clearly labeled, or fold it into buildability.

**A4 — Rainfall has near-zero discriminating power within one county.** Annual precipitation across Sequatchie County is roughly uniform (~55–60 in/yr on the plateau). A 15 % weight on an essentially constant variable barely changes intra-county rankings; it only matters once we expand across the Chattanooga region where orographic differences appear. *Recommendation:* keep rainfall in the model for regional scaling, but expect ≈0 effect within v0.1’s single county, and surface that in the report so a user isn’t misled into thinking it’s discriminating.

**A5 — Min–max normalization across the candidate set is unstable and not reproducible.** If scores are scaled 0–100 relative to the current result set, adding one new candidate that sets a new maximum silently shifts every other site’s score. That breaks the explicit “explainable, reproducible, why-does-A-beat-B” requirement. *Recommendation:* score against **absolute, physically anchored thresholds** (documented piecewise functions: e.g., head in feet → sub-score; firm power in watts → sub-score, with published breakpoints). Site A’s score is then a property of Site A, not of who else is in the table.

**A6 — Weights are hardcoded in the requirements.** For a data-driven, reproducible system, weights and thresholds belong in **versioned config (scoring profiles)**, not baked into code. A run then references a profile by version, and any ranking is fully reconstructable. *Recommendation:* `config/scoring_profiles/*.yaml`, referenced by `analysis_runs.scoring_profile_version`.

#### 2.B Database schema

**B1 — CRS is wrong for analysis (highest-impact schema issue).** All geometry is `…,4326` (geographic degrees). You cannot compute slope, area (acres), length (feet), or distance correctly in 4326. *Recommendation:* adopt an explicit **CRS contract** — store canonical geometry in **EPSG:4326** (interoperability + MapLibre wants WGS84), and carry a **projected** geometry / compute in **EPSG:5070 (NAD83 CONUS Albers, equal-area)** for vector area/length and as the analysis CRS. 5070 is chosen over a UTM zone deliberately: it is equal-area and CONUS-wide, so the eventual multi-county “driving distance of Chattanooga” run never crosses a UTM zone boundary. Rasters (DEM) are processed in a projected grid (5070 or their native UTM 16N) for slope/hydrology. Centralize this in one module; never sprinkle EPSG codes through the code.

**B2 — `streams.geometry GEOMETRY(LINESTRING,4326)` will reject valid data.** NHD flowlines are frequently multipart. *Recommendation:* `MULTILINESTRING`.

**B3 — `watersheds.geometry GEOMETRY(POLYGON,4326)` will reject dissolved catchments.** Aggregated/island catchments are multipart. *Recommendation:* `MULTIPOLYGON`.

**B4 — No spatial indexes anywhere (fatal for the NFRs).** The 100 GB store and the 15-minute county target are impossible without GiST indexes on every geometry column, especially the layers involved in overlay joins (parcels, soils, flood). *Recommendation:* a GiST index on every geometry column, created in the same migration as the table. Add `CLUSTER`/`ANALYZE` guidance for large loads.

**B5 — `candidate_sites` stores `latitude`/`longitude` as loose doubles with no point geometry.** That’s a PostGIS anti-pattern — you can’t spatially index or join the site location. *Recommendation:* add `geom GEOMETRY(POINT,4326)` (+ projected variant) and index it; keep lat/lon only as display convenience if desired.

**B6 — No provenance or confidence — required by two explicit mandates.** The prompt says do not fabricate hydrologic values and flag what can’t be computed reliably; the reporting requirement says every report records data sources. Neither is possible without provenance in the data model. *Recommendation:* add to every computed metric a **method** and **confidence** (and per-feature **source** + **ingested_at**), and store **NULL + a quality flag** instead of a fabricated number when a value can’t be reliably derived. A `data_sources` table plus `_method`/`_confidence` columns (or a JSONB `provenance`) on `hydro_corridors` and `candidate_sites`.

**B7 — No `analysis_runs` table (reproducibility hole).** A report must be tied to the exact AOI, data versions, scoring profile, and parameters that produced it. Without a run record, “why did this ranking change” is unanswerable. *Recommendation:* `analysis_runs(run_id, aoi, code_version, scoring_profile_version, data_source_versions JSONB, params JSONB, started_at, finished_at, status)`, and add `run_id` to `hydro_corridors`, `candidate_sites`, `subdivision_analysis`. Results become run-scoped and immutable.

**B8 — `hydro_corridors` assumes 1 corridor = 1 whole stream reach, and has no geometry.** A developable corridor may be a *sub-segment* of a reach or span several reaches; storing only `stream_id` and no geometry loses the actual corridor location. *Recommendation:* give corridors their own `geom`, and model the corridor as a reach + from/to linear-referencing measures (so a corridor can be “the steep 300 m segment of reach X”), not the entire reach.

**B9 — `head_ft` is semantically undefined.** Gross head, net head, and *usable* (penstock-realizable) head are different numbers. *Recommendation:* define `head_ft` explicitly as **gross head over the corridor segment** (reach max−min smoothed elevation, or DEM-sampled endpoints), and note that net/usable head — which requires siting an intake and powerhouse and accounting for penstock losses — is a later, site-specific refinement, flagged accordingly.

#### 2.C Missing data layers (functional requirements with no source in FR-1)

| Requirement | Needs | Missing from FR-1? |
| --- | --- | --- |
| FR-4 road access; FR-9 road frontage; FR-7 logistics; **success criterion “driving distance of Chattanooga”** | Road network (Census **TIGER/Line** roads and/or OSM); routing/isochrones (OSRM/Valhalla or precomputed drive-time) | **Yes** |
| FR-6 privacy (nearby structures, development density) | Building footprints (**Microsoft/Google Building Footprints** or OSM buildings) | **Yes** |
| FR-3 rainfall factor | Precipitation normals (**PRISM 30-yr normals** or **NOAA Atlas 14**) | **Yes** |

*Recommendation:* add these to the data acquisition plan (§4). None block the v0.1 hydro proof-of-concept, but FR-4/6/7/9 cannot be implemented in v1 without them, and the headline success question (“within driving distance of Chattanooga”) needs at least a drive-time layer.

#### 2.D Hydrography approach (correction from current sources)

**D1 — NHD is retired; NHDPlus HR is frozen; 3DHP is the successor but incomplete.** USGS retired the NHD on Oct 1, 2023 and halted NHDPlus HR production to focus on 3DHP, which is years from nationwide completeness and may not yet cover the Tennessee/Chattanooga region. NHDPlus HR remains available as a static, DOI-referenced bridge dataset. *Recommendation:* use the **final-release NHDPlus HR** as the v0.1 hydrography source and isolate it behind a `HydrographySource` interface so we can swap to 3DHP when TN data is published. The “refresh strategy” for this layer is effectively *frozen snapshot → migrate to 3DHP later*, which is fine for our use.

**D2 — Much of FR-2 is already pre-computed in NHDPlus, so we shouldn’t recompute it for v0.1.** NHDPlus HR Value-Added Attributes (VAA) ship **cumulative drainage area**, **flow estimates (EROM)**, and **smoothed min/max elevation per reach**. That covers “upstream watershed area,” a flow first-approximation, and “available head” — directly. *Recommendation:* derive corridor metrics from NHDPlus VAA on the fast path for v0.1 (this alone makes the 15-minute NFR comfortable), and reserve **WhiteboxTools DEM watershed delineation** for later refinement (custom pour points, sub-NHD resolution, validation).

**D3 — “Identify perennial streams” is an attribute filter, not a DEM computation.** NHD encodes perennial vs intermittent vs ephemeral in the flowline **FCode** (perennial 46006 vs intermittent 46003, etc.). FR-2 frames perennial detection as something derived; it’s a filter on the source attribute. *Recommendation:* implement it as a documented FCode filter, and treat the perennial flag as **source-provided** in `streams.perennial`.

#### 2.E Performance / DEM resolution

**E1 — 1 m LiDAR over a whole county breaks the 15-minute budget.** Sequatchie County (~266 mi²) at 1 m is on the order of 700 M cells; full-county fill/flow-direction/flow-accumulation at that resolution is not a 15-minute job. *Recommendation:* use **10 m (1/3 arc-second) 3DEP** for watershed-scale hydrology (it matches NHD’s lineage); reserve 1 m LiDAR for site-specific slope/buildability on the short list later. Use Rasterio **windowed** reads/writes — never materialize a county raster in memory.

#### 2.F Flow estimation (the real micro-hydro gap)

**F1 — Firm power depends on reliable low flow, which nothing in the SRS computes.** A micro-hydro plant produces continuously only what the stream *reliably* delivers — conventionally the flow exceeded ~90–95 % of the time (Q90/Q95 from the flow-duration curve), **not** mean annual flow, which overstates firm capacity. NHDPlus EROM gives a mean-annual estimate (low confidence on tiny headwaters). *Recommendation:* use **USGS StreamStats** (REST services + Tennessee regional regression equations) at candidate pour points to obtain flow-duration / low-flow statistics as a higher-fidelity flow input.

**F2 — But flag the uncertainty honestly.** Tennessee’s regression equations carry large prediction errors (roughly 30–50 % standard error) and have a **minimum drainage-area applicability limit**; many attractive micro-hydro headwater sites fall at or below that limit. *Recommendation:* attach **method + confidence** to every flow value, **flag sub-threshold basins**, and never substitute a fabricated number — exactly the behavior the prompt asks for. Where flow can’t be estimated within applicability, store NULL + flag and let the corridor rank on head with a reduced-confidence marker.

---

### 3. Recommended improvements (consolidated)

The actionable shortlist, in priority order:

1. **Adopt a CRS contract** (store 4326, analyze 5070; one module owns it). *(B1)*
2. **Add `analysis_runs` + run-scoping + provenance/confidence columns**, and the `data_sources` table. *(B6, B7)*
3. **Restructure hydro scoring** around net head + firm power, with **absolute** profile-driven sub-scores; keep the FR-3 weight table as a labeled fallback. *(A1–A6)*
4. **Fix geometry types and add GiST indexes** on every geometry column. *(B2–B5)*
5. **Pin NHDPlus HR (frozen) behind a swappable hydrography interface**; derive v0.1 corridor metrics from VAA; defer DEM delineation. *(D1–D3)*
6. **Pin 10 m DEM for hydrology**; windowed raster I/O; vector joins in PostGIS. *(E1, §1.2)*
7. **Integrate StreamStats for flow with confidence flagging**; treat headwater flow as uncertain by default. *(F1–F2)*
8. **Add the missing data layers** (roads, building footprints, precipitation) to the acquisition plan and schema for v1. *(2.C)*
9. **Give corridors their own geometry and linear-referencing**, and define head semantics precisely. *(B8, B9)*

#### 3.1 Revised hydro scoring (sketch, not code)

```
firm_flow_Qf   = StreamStats flow-duration low-flow (e.g., Q90), m³/s   [confidence-flagged]
gross_head_H   = reach max−min smoothed elevation, m                     [from NHDPlus VAA]
net_head       = gross_head_H × head_efficiency_factor (config)          [usable-head proxy]
firm_power_W   = ρ · g · firm_flow_Qf · net_head · η_system              [ρ=1000, g=9.81]

head_subscore  = piecewise_absolute(net_head)        # documented breakpoints, 0–100
power_subscore = piecewise_absolute(firm_power_W)     # documented breakpoints, 0–100
hydro_score    = w_head·head_subscore + w_power·power_subscore   # weights in scoring profile

# every term, breakpoint, and the η/efficiency assumptions are written to the run record,
# so the report can state exactly why corridor A outranks corridor B.
```

This keeps the score transparent and absolute (A5), uses the physics the schema already implies (A2), and removes the correlated proxies (A1, A3, A4).

---

### 4. Data acquisition strategy

Federal layers are US Government works and **public domain**; attribution is courtesy, not a legal requirement. County/state parcel and assessor data carry the real licensing caveats and PII considerations — read each portal’s terms before any redistribution.

| Dataset | Source | Access method | License | Refresh strategy | Notes / caveats |
| --- | --- | --- | --- | --- | --- |
| **DEM (terrain)** | USGS **3DEP**, 1/3 arc-second (~10 m) | The National Map Downloader / TNM Access API; clip to AOI + buffer | Public domain | Stable national layer; pull once per AOI, re-pull on major release | Use 10 m for hydrology; reserve 1 m LiDAR (where QL2 exists) for site-specific slope later |
| **Hydrography** | **NHDPlus HR — final release** (frozen) | National Map / NHD Data Access download by HU; load flowlines + VAA tables | Public domain | **Frozen snapshot** (no longer maintained); migrate to **3DHP** when TN coverage publishes | NHD retired 2023; NHDPlus HR halted for 3DHP. VAA gives drainage area, EROM flow, min/max elevation. Isolate behind `HydrographySource` |
| **Streamflow (ungauged)** | USGS **StreamStats** v4 + TN regional regression / NSS | REST services (delineation, basin characteristics, flow stats) | Public domain | On-demand per pour point; cache results per run | Flow-duration / low-flow for firm power. **30–50 % standard error; min-drainage-area limit** → confidence-flag, flag sub-threshold basins |
| **Soils** | USDA-NRCS **SSURGO** | **Soil Data Access (SDA)** REST/POST + WFS/WMS for AOI; or gSSURGO file geodatabase | Public domain | Annual refresh (~October FY release) | Pull map units + interpretation ratings for the AOI. **Septic suitability is a shipped interp** (“Septic Tank Absorption Fields”), not derived from drainage class. Join via MUKEY |
| **Flood** | FEMA **NFHL** | FEMA Map Service Center bulk download (county/state GDB) and/or FEMA hazard ArcGIS REST/WMS services | Public domain | Periodic; re-pull per AOI on update | Confirm current endpoint at build time; coverage gaps exist in rural areas |
| **Parcels (geometry)** | **TN statewide parcel boundaries** (STS GIS / TNMap) | Statewide feature service / download; filter to county FIPS | Public service; portal terms apply | Updated monthly | **Sequatchie is included.** **Hamilton County (Chattanooga itself) is NOT** in the statewide set — needs a separate county source for the regional run. Statewide layer is linework + labels; attributes link out |
| **Ownership / assessor** | **TN Comptroller of the Treasury** property assessment data (TN Property Viewer) | Comptroller property assessment portal / county trustee | Informational, “no warranty,” not legal evidence of ownership | Periodic | **PII** — segregate, restrict, and treat as the explicitly-later overlay. Read terms before any storage/redistribution |
| **Roads** *(add — missing from SRS)* | Census **TIGER/Line** roads (and/or OSM) | Bulk download by county; OSM via extract | Public domain (TIGER) / ODbL (OSM) | Annual (TIGER) | Needed for FR-4/7/9 and drive-time. OSM’s ODbL has share-alike terms if redistributed |
| **Building footprints** *(add — missing from SRS)* | **Microsoft / Google Building Footprints** or OSM buildings | Bulk download by region | Open data licenses (review per source) | Periodic | Needed for FR-6 privacy / development density |
| **Precipitation** *(add — missing from SRS)* | **PRISM** 30-yr normals or **NOAA Atlas 14** | Download grids; sample at site | Public domain / PRISM terms | Static normals | Needed for FR-3 rainfall factor (≈constant intra-county) |

**Sources consulted (verify endpoints at build time):** USGS National Hydrography / 3D Hydrography Program; USGS 3DEP / The National Map; USGS StreamStats v4 web services and the Tennessee regression publications; USDA-NRCS SSURGO / Soil Data Access / gSSURGO; FEMA NFHL / Flood Map Service Center; Tennessee STS GIS (TNMap / geodata.tn.gov) statewide parcel service and TN Comptroller property assessment data.

---

### 5. Recommended repository structure

The prompt’s example mixes concerns at the top level (e.g., `/reports` and `/analysis` as siblings of `/backend`). I recommend a clearer separation between **the Python service/package**, **infrastructure**, **the frontend**, and **operational config/data**, with domain-driven modules inside the analysis layer.

```
hhsf/
├── README.md
├── pyproject.toml                 # single source of Python deps + tooling (ruff, mypy, pytest)
├── docker-compose.yml             # postgis + api + (later) tile server, web
├── .env.example
│
├── config/                        # versioned, code-reviewed configuration (NOT secrets)
│   ├── aoi/
│   │   └── sequatchie_tn.yaml      # AOI definition (FIPS, boundary source, buffer)
│   ├── scoring_profiles/
│   │   ├── hydro_physics_v1.yaml   # head/power breakpoints, weights, η assumptions
│   │   └── fr3_weighted_fallback_v1.yaml
│   ├── data_sources.yaml           # endpoints, versions, refresh policy per dataset
│   └── crs.yaml                    # the CRS contract (storage 4326 / analysis 5070)
│
├── src/hhsf/                       # the installable Python package
│   ├── core/
│   │   ├── crs.py                  # THE one place EPSG codes live; transforms
│   │   ├── config.py               # typed config loader (Pydantic)
│   │   ├── db.py                   # engine/session, PostGIS helpers
│   │   ├── provenance.py           # method/confidence/source primitives
│   │   └── units.py                # explicit unit handling (m↔ft, m²↔acres)
│   │
│   ├── ingest/                     # SEPARATE from analysis (clean-architecture boundary)
│   │   ├── base.py                 # SourceAdapter interface; idempotent load contract
│   │   ├── hydrography/            # HydrographySource: NHDPlusHR now, 3DHP later
│   │   ├── dem/                    # 3DEP fetch + clip (windowed)
│   │   ├── soils/                  # SSURGO via SDA
│   │   ├── flood/                  # FEMA NFHL
│   │   ├── parcels/                # TN statewide service
│   │   ├── roads/                  # TIGER/OSM (v1)
│   │   └── precip/                 # PRISM/Atlas14 (v1)
│   │
│   ├── analysis/                   # deterministic, config-driven; reads PostGIS, writes results
│   │   ├── terrain/                # slope/aspect (projected grid), DEM sampling
│   │   ├── hydro/                  # perennial filter, watershed (VAA fast path / Whitebox later),
│   │   │                           #   head, flow (StreamStats), firm power
│   │   ├── flood/                  # flood-zone overlays (v1)
│   │   ├── soils/                  # capability/septic interps (v1)
│   │   ├── parcels/                # parcel overlay + subdivision feasibility (v1)
│   │   └── access/                 # road frontage / drive-time (v1)
│   │
│   ├── scoring/                    # the explainable scoring engine
│   │   ├── functions.py            # absolute piecewise sub-score functions
│   │   ├── profiles.py             # load + version scoring profiles
│   │   └── compose.py              # composite ranking; writes per-factor explanations
│   │
│   ├── runs/                       # analysis-run orchestration + provenance capture
│   │   └── pipeline.py
│   │
│   ├── reporting/                  # summary report + CSV + GeoJSON (+ PDF/HTML later)
│   │   └── exporters.py
│   │
│   ├── api/                        # FastAPI read API (added after the pipeline works)
│   │   └── routers/
│   │
│   └── cli.py                      # `hhsf ingest …`, `hhsf run …`, `hhsf export …`
│
├── migrations/                     # Alembic (schema/DDL versioning) — see §6
│   ├── env.py
│   └── versions/
│
├── data/                           # gitignored working data; never commit large rasters
│   ├── raw/                        # downloaded source data (by source + vintage)
│   ├── interim/
│   └── outputs/                    # generated reports/exports
│
├── frontend/                       # React + TypeScript + MapLibre (last)
│   └── src/
│
├── tests/                          # pytest; mirrors src/ layout
│   ├── unit/                       # CRS, units, scoring functions, perennial filter, head/power
│   ├── integration/                # ingest→PostGIS, spatial joins, full pipeline on a fixture AOI
│   └── fixtures/                   # tiny clipped datasets (a few reaches) for fast, real tests
│
└── docs/
    ├── ENGINEERING_PLAN.md         # this document
    ├── ARCHITECTURE.md
    ├── DATA_SOURCES.md
    ├── SCORING.md                  # the explainability reference: every factor + breakpoint
    └── ASSUMPTIONS.md              # running log of hydrologic assumptions + confidence policy
```

**Why this differs from the prompt’s example:** ingestion and analysis are sibling packages with an enforced one-way dependency (analysis reads what ingestion persisted; ingestion never imports analysis); all GIS operations are modular by domain; there is no county-specific code (Sequatchie lives in `config/aoi/`, not in a module); and config — AOI, scoring, sources, CRS — is first-class, versioned, and reviewable, which is what makes runs reproducible and scores explainable.

---

### 6. Database & migration strategy

#### 6.1 Tooling

**Use Alembic** for schema/DDL versioning — it’s the standard, integrates with SQLAlchemy, and gives reversible migrations. But be deliberate about its boundaries: Alembic is excellent for table/column/index DDL and weak for (a) enabling extensions and (b) bulk spatial data loads. So:

- **Schema/DDL** → Alembic migrations (tables, geometry columns, GiST indexes, constraints, the `analysis_runs`/`data_sources` tables, seed reference rows like the county record).
- **Extension enablement** → an early, explicit migration: `CREATE EXTENSION IF NOT EXISTS postgis;` (+ `postgis_raster`, `postgis_topology` if/when needed).
- **Bulk spatial loads** (DEM, NHDPlus, SSURGO, parcels) → a **separate, idempotent ingestion layer** (ogr2ogr / GeoPandas `to_postgis` / `raster2pgsql`), **not** Alembic. Data is not schema; keep the two version streams apart.

Use a geometry-aware comparison hook (e.g., GeoAlchemy2 with Alembic’s `compare_type`) so autogenerate doesn’t mangle `GEOMETRY(...)` columns — review every autogenerated migration by hand regardless.

#### 6.2 Migration workflow

1. Migration `0001` — enable PostGIS extension(s).
2. Migration `0002` — reference tables (`counties`) + seed Sequatchie boundary.
3. Migration `0003+` — ingestion target tables (`streams` as `MULTILINESTRING`, `watersheds` as `MULTIPOLYGON`, `soils`, `flood_zones`, `parcels`, `parcel_owners`, `parcel_owner_link`) **each with its GiST index in the same migration**.
4. Migration for results: `analysis_runs`, `data_sources`, and the run-scoped `hydro_corridors` / `candidate_sites` / `subdivision_analysis` with provenance/confidence columns, corridor geometry, and a `POINT` geometry on sites.
5. Every migration has a working `downgrade`; CI runs `upgrade head` → `downgrade base` → `upgrade head` against a throwaway PostGIS container.

#### 6.3 The CRS contract in the schema

Canonical geometry in **4326**; a projected geometry in **5070** maintained for analysis (either a generated column or a materialized analysis view), with the GiST index on the column actually used for area/length/distance work. The contract is documented in `config/crs.yaml` and enforced in `core/crs.py` — the database stores both representations so analysis never has to guess.

#### 6.4 Schema additions at a glance (illustrative DDL)

```sql
-- reproducibility spine
CREATE TABLE analysis_runs (
    run_id                   BIGSERIAL PRIMARY KEY,
    aoi_key                  TEXT NOT NULL,            -- e.g. 'sequatchie_tn'
    code_version             TEXT NOT NULL,            -- git describe
    scoring_profile_version  TEXT NOT NULL,
    data_source_versions     JSONB NOT NULL,           -- {source: version/vintage}
    params                   JSONB NOT NULL,
    started_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at              TIMESTAMPTZ,
    status                   TEXT NOT NULL DEFAULT 'running'
);

-- provenance dictionary
CREATE TABLE data_sources (
    source_key   TEXT PRIMARY KEY,                     -- 'nhdplus_hr','3dep_10m',...
    description  TEXT,
    license      TEXT,
    version      TEXT,
    retrieved_at TIMESTAMPTZ
);

-- corridors get geometry, run-scoping, and confidence
ALTER TABLE hydro_corridors
    ADD COLUMN run_id            BIGINT REFERENCES analysis_runs(run_id),
    ADD COLUMN geom              GEOMETRY(MULTILINESTRING,4326),
    ADD COLUMN net_head_ft       NUMERIC,
    ADD COLUMN firm_flow_cfs     NUMERIC,
    ADD COLUMN flow_method       TEXT,                 -- 'streamstats_q90','nhdplus_erom',NULL
    ADD COLUMN flow_confidence   TEXT,                 -- 'high','low','below_applicability'
    ADD COLUMN head_method       TEXT;
CREATE INDEX ON hydro_corridors USING GIST (geom);

-- candidate sites get a real point geometry
ALTER TABLE candidate_sites
    ADD COLUMN run_id BIGINT REFERENCES analysis_runs(run_id),
    ADD COLUMN geom   GEOMETRY(POINT,4326);
CREATE INDEX ON candidate_sites USING GIST (geom);
```

(Shown to make the recommendations concrete; the real DDL lands as reviewed Alembic migrations, not hand-run SQL.)

---

### 7. Version 0.1 implementation plan

**Goal (unchanged from the prompt):** *Identify and rank hydro corridors in Sequatchie County, Tennessee.* Nothing else ships until this works and is trustworthy.

**Definition of done for v0.1:** a single command produces a ranked list of hydro corridors for Sequatchie County, where each corridor has head, watershed area, a confidence-flagged firm-flow and firm-power estimate, and an **explainable** hydro score; results are persisted to PostGIS under an `analysis_runs` record and exported to CSV + GeoJSON with a summary report stating inputs, assumptions, sources, and confidence; calculation modules have ≥80 % test coverage; the run completes well under 15 minutes.

#### 7.1 Phases & milestones

| Phase | Milestone | Output |
| --- | --- | --- |
| **P0 — Skeleton & infra** | Repo, `pyproject`, Docker Compose with PostGIS, Alembic baseline (extension + `counties` + seed Sequatchie), `core/crs.py`, `core/config.py`, CLI stub | `docker compose up` yields a PostGIS DB with the schema spine; CRS + config covered by tests |
| **P1 — Ingestion** | NHDPlus HR flowlines for Sequatchie’s HU loaded; **perennial FCode filter**; 10 m 3DEP clipped to AOI; provenance recorded | `hhsf ingest` populates `streams` (perennial) and stages the DEM; idempotent re-runs |
| **P2 — Hydro analysis** | Per perennial reach: watershed area (VAA fast path), gross/net head, reach length (projected), firm flow via StreamStats (confidence-flagged), firm power | `hydro_corridors` rows with metrics + method/confidence; assumptions logged to `docs/ASSUMPTIONS.md` |
| **P3 — Scoring & ranking** | Absolute, profile-driven head + power sub-scores; composite hydro score; ranking; per-factor explanation captured | Ranked corridors; each score reconstructable from the run record |
| **P4 — Persistence & exports** | Run-scoped persistence under `analysis_runs`; CSV + GeoJSON exporters; summary report (inputs/assumptions/scores/sources) | Reproducible artifacts in `data/outputs/` |
| **P5 — Hardening** | ≥80 % coverage on calc modules; integration test on a tiny fixture AOI; perf check < 15 min; docs (`SCORING.md`, `DATA_SOURCES.md`) | Green CI; trustworthy v0.1 |

#### 7.2 Dependencies (critical path)

```
P0 (CRS + DB spine) ──▶ P1 (ingest) ──▶ P2 (hydro metrics) ──▶ P3 (scoring) ──▶ P4 (persist/export)
        │                                   │                                         ▲
        └────────────────────────────────── └──────────── P5 (tests/hardening) ───────┘ (runs alongside P2–P4)
```

P0’s CRS contract gates everything (every downstream area/length/head value depends on it). StreamStats integration (P2) is the main external dependency and the main uncertainty source — start it early and behind an interface so the pipeline can fall back to NHDPlus EROM (flagged low-confidence) if the service is unavailable.

#### 7.3 Risk register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| **Headwater flow is genuinely uncertain** (regressions below applicability) | High | High | Confidence flags + sub-threshold flagging built in from P2; never fabricate; rank low-confidence corridors with an explicit marker. Treat as a *feature* (honest output), not a bug |
| NHDPlus HR is frozen / 3DHP arrives mid-project | Medium | Medium | `HydrographySource` interface; swap is localized. NHDPlus HR is DOI-pinned, so v0.1 stays reproducible regardless |
| External services (StreamStats, SDA, FEMA) change endpoints or rate-limit | Medium | Medium | Adapters + response caching per run; endpoints in `config/data_sources.yaml`; degrade gracefully to flagged fallbacks |
| Performance miss on full county | Low (VAA fast path) | Medium | 10 m DEM, windowed I/O, PostGIS joins, VAA instead of DEM delineation for v0.1 |
| County parcel/ownership terms or PII handling | Medium | Medium | Ownership is the explicitly-later overlay; segregate PII; read portal terms; Hamilton County needs its own source for the regional run |
| Scoring opacity creeps back in | Low | High | Absolute functions + per-factor explanations + `SCORING.md` enforced in review; ban relative min-max normalization |
| 1 m LiDAR scope creep | Medium | Low | Explicitly out of v0.1 hydrology; documented as a short-list-only, later refinement |

---

### 8. First coding milestone

The very first vertical slice — small, but it proves the spine end-to-end so every later phase has solid ground.

**Milestone: “Config → ingest → PostGIS, with the perennial-stream slice for Sequatchie.”**

Concretely, the first PR delivers:

1. **Project skeleton** — `pyproject.toml` (ruff, mypy, pytest), package layout under `src/hhsf/`, `cli.py` stub.
2. **Docker Compose** with a PostGIS service and a `.env.example`.
3. **Alembic baseline migrations** — `CREATE EXTENSION postgis`; `counties` table; seed the Sequatchie County boundary; `streams` table as `MULTILINESTRING(4326)` **with its GiST index**.
4. **`core/crs.py`** — the CRS contract (4326 storage / 5070 analysis) with transform helpers, plus `core/config.py` (typed loader) and `config/aoi/sequatchie_tn.yaml`.
5. **One ingestion command** — `hhsf ingest hydrography --aoi sequatchie_tn` fetches NHDPlus HR flowlines for the containing HU, applies the **perennial FCode filter**, loads them into `streams`, and records the source in `data_sources`. Idempotent.
6. **Tests** — unit tests for CRS transforms and unit conversions; a unit test for the perennial FCode filter against a tiny fixture; an integration test that loads a handful of fixture reaches into a throwaway PostGIS container and asserts the perennial count and SRID.

**Acceptance checks:**

- `docker compose up` → healthy PostGIS with the migrated schema.
- `alembic upgrade head` → `downgrade base` → `upgrade head` runs clean in CI.
- `hhsf ingest hydrography --aoi sequatchie_tn` populates `streams` with only perennial flowlines, all SRID 4326, source recorded.
- Running ingest twice does not duplicate rows.
- Tests green; coverage reporting wired up.

No analysis, no scoring, no API, no frontend yet — deliberately. This PR exists to prove the config-driven, provenance-aware, CRS-correct ingestion spine on real data, so P2’s hydrology has a trustworthy foundation.

---

### 9. Decisions I’m defaulting on (redirect me if any are wrong)

Per your “use engineering judgment, don’t wait for approval on minor calls” instruction, here are the few material assumptions I’m proceeding on — each with my chosen default:

1. **Purpose of the power estimate.** I’m building toward *real micro-hydro feasibility* (firm power you could actually develop), not just relative screening — hence the StreamStats flow integration and firm-flow emphasis. If v0.1 only needs coarse relative ranking, we can defer StreamStats and rank on head + watershed-area-as-flow-proxy (faster, less external dependency). **Default: feasibility-oriented, with confidence flags.**
2. **Single-user, local, no auth** (per your NFRs). I’m keeping the API and deployment simple accordingly — no multi-tenant or auth scaffolding in v1. **Default: single-user local.**
3. **Regulatory reality is out of software scope but flagged.** Developing micro-hydro on a perennial TN stream implicates water rights, TDEC permitting (e.g., ARAP), and possibly FERC exemption. The platform won’t model this in v0.1, but I’ll surface it in reports as an actionability caveat and reserve it as a future filtering input. **Default: flag, don’t model, in v0.1.**
4. **Regional AOI definition deferred.** “Driving distance of Chattanooga” = a multi-county set (Hamilton, Sequatchie, Marion, Bledsoe, Rhea, possibly Dade/Walker GA and Jackson/DeKalb AL). I’m scoping v0.1 to Sequatchie only and treating the county as a pure config parameter so the regional run is just more AOIs later — but I’ll need the actual county list (and the drive-time threshold) from you before the regional milestone. **Default: Sequatchie-only for v0.1.**

---

*Next deliverable on your go-ahead: I can either (a) refine any section above, or (b) begin the first coding milestone (§8) — the skeleton, Compose, Alembic baseline, CRS module, and the perennial-stream ingestion slice for Sequatchie.*