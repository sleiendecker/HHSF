# Hydro Homestead Site Finder

A local map-based tool for finding land near Chattanooga, TN that could support a homestead powered by a small hydroelectric (micro-hydro) system.

## Goal

Produce a **shortlist of land worth visiting in person**: sites with a year-round stream that has enough drop and flow to generate at least 1 kW of continuous power, within about an hour's drive of Chattanooga. Each site lists its property parcel and owner so landowners can be contacted, **whether or not the land is listed for sale**.

This is different from a real estate search. It starts from the terrain and water, finds physically suitable land, and only then asks who owns it.

- Full requirements and engineering plan: [site-selection.md](site-selection.md)
- Open judgment calls and current answers (power target, search radius, scoring assumptions): [docs/DECISIONS.md](docs/DECISIONS.md)
- How head, flow and power are calculated, and every assumption behind them: [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)
- How corridors are scored and ranked: [docs/SCORING.md](docs/SCORING.md)

## Status

**v0.1, in progress.** Analyzed so far: Sequatchie County, TN (the default) and its neighbors Marion, Bledsoe, Grundy and Rhea, part of the 1-hour drive region around Chattanooga (D-02). Six streams across the five counties reach 1 kW; see [docs/SCORING.md](docs/SCORING.md#five-counties-part-13-profile-hydro_v3-2026-10-20).

| Works now | Not built yet |
| --- | --- |
| Loads the county boundary, every perennial (year-round) stream in the county and a 10 m elevation model into a local database and `data/raw/` | A way to spot spring-fed small streams (the low-flow estimates for small streams are typical-basin numbers) |
| Records each stream's elevation drop, length, slope, drainage area and estimated mean flow | Soils, roads |
| Hydro corridors: the steepest 1,000 ft stretch of each stream (where one penstock would get the most drop), found on the elevation model | |
| Map viewer: each stream's best stretch colored by its hydro score and styled by confidence, with a legend; click one for its score explained in plain words. Light, satellite or topo map underneath | |
| Hydro analysis: head over that stretch, reliable low flow (Q90 from USGS StreamStats, or, for streams too small for it, the typical StreamStats low flow per square mile of their river basin; the Whitwell stream gauge where StreamStats is down) and firm power, saved as a numbered run | |
| Scoring and ranking: streams of 1 kW or more scored 0–100 on power and head with an explanation for each score, ranked by confidence; streams under 1 kW on a rough flow estimate listed last as "low flow" | |
| Exports: the ranked shortlist as CSV and GeoJSON, every stream with the reason it was or wasn't listed, and a summary report for planning visits | Parcels for Hamilton County (Chattanooga) and other counties outside the state's parcel layer |
| Parcels and owners: each stretch's turbine parcel (ID, acreage, owner) and every parcel it crosses, in the exports; property lines on the map. Owner names stay on this computer, never on the map | A minimum acreage filter (D-03 is open; small parcels are only flagged) |
| Flood zones: FEMA's flood zone at each stretch's turbine end and how much of the stretch is in the 100-year floodplain, in the exports and the map popup; flood zones on the map | Checking for buildable ground outside the floodplain on each parcel (flood zones are flagged only, by decision D-12) |

## How it works

```
Public data services ──▶ hhsf (Python) ──▶ PostGIS database ──▶ Martin tile server ──▶ React map
(USGS, Census, TN)       download + load     (Docker)            (Docker)              (browser)
```

1. The **`hhsf` command-line tool** downloads public datasets for an area, converts units and coordinates, and loads them into the database.
2. **PostGIS** stores everything. Later analysis and scoring steps run here.
3. **Martin** serves database tables to the map as vector tiles.
4. The **React app** draws the map in your browser.

## Tech stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| Data pipeline | Python 3.12, [uv](https://docs.astral.sh/uv/) | Download, convert and load data; later analysis and scoring |
| | [Typer](https://typer.tiangolo.com/) | The `hhsf` command-line tool |
| | [Shapely](https://shapely.readthedocs.io/), [pyproj](https://pyproj4.github.io/pyproj/) | Geometry and coordinate conversion |
| | [httpx](https://www.python-httpx.org/) | Calls to the public data services |
| | [SQLAlchemy](https://www.sqlalchemy.org/), [psycopg](https://www.psycopg.org/) | Database access |
| | [Alembic](https://alembic.sqlalchemy.org/) | Database schema migrations |
| | [Pydantic](https://docs.pydantic.dev/) | Typed settings and config files |
| Database | PostgreSQL 17 + [PostGIS](https://postgis.net/) 3.5 (Docker) | Spatial data storage and queries |
| Tile server | [Martin](https://maplibre.org/martin/) 1.16 (Docker) | Serves database tables as map tiles |
| Frontend | [React](https://react.dev/) 18, TypeScript, [Vite](https://vite.dev/) | The web app |
| | [MapLibre GL JS](https://maplibre.org/) | Interactive map |
| | [OpenFreeMap](https://openfreemap.org/) | Free basemap tiles (no API key) |
| | [USGS The National Map](https://www.usgs.gov/programs/national-geospatial-program/national-map) | Satellite and topo basemap tiles (no API key) |
| Testing and tooling | pytest, [Testcontainers](https://testcontainers.com/), Ruff | Tests (including throwaway databases) and linting |

## System requirements

| Requirement | Version | Notes |
| --- | --- | --- |
| OS | macOS, Linux, or Windows with WSL 2 | Tested on macOS (Apple Silicon) |
| Docker | Docker Desktop, OrbStack, or Docker Engine with Compose v2 | Must be running before `make up` |
| uv | 0.4+ | Installs Python 3.12 automatically; no system Python needed |
| Node.js | 20+ | For the map app |
| make | any | Preinstalled on macOS and Linux |
| Disk | ~2 GB free | Docker images plus one county of data. The full regional version will need far more (see [site-selection.md](site-selection.md)). |
| RAM | 8 GB+ | |
| Network | Internet access | To download data and basemap tiles |
| Free ports | 5433, 3100, 5173 | Database, tile server, map app |

## Getting started

### 1. Install prerequisites

- **Docker:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (any OS) or [OrbStack](https://orbstack.dev/) (Mac). Open it once so it's running.
- **uv:** follow the [install guide](https://docs.astral.sh/uv/getting-started/installation/), or on Mac run `brew install uv`.
- **Node.js 20+:** from [nodejs.org](https://nodejs.org/), or on Mac run `brew install node`.

### 2. Clone and install dependencies

```bash
git clone <repo-url>
cd hydro-homestead
make setup
```

### 3. Start the database and tile server

```bash
make up
```

This starts PostGIS and Martin in Docker and creates the database tables. Data is kept between restarts.

### 4. Load data

```bash
make ingest
```

This downloads the Sequatchie County boundary from Census, the stream data and the elevation model from USGS, the property parcels from the Tennessee Comptroller and the flood zones from FEMA, then loads them. The first run downloads about **270 MB** of USGS stream files (one per river basin the county touches) plus about **84 MB** of elevation data, and takes **a couple of minutes** on a typical connection; after that the files are reused from `data/raw/` and a re-run takes about 30 seconds.

The elevation model is USGS 3DEP at 1/3 arc-second (about 10 m). USGS publishes it in 1×1 degree tiles (Sequatchie's is 472 MB), but only the part under the county's streams is read, about 84 MB, saved as a 48 MB file in `data/raw/usgs_3dep_13/`. To fetch just that step: `uv run hhsf ingest terrain`.

Parcels take about 10 seconds per county and are cached in `data/raw/tn_parcels/`. The state updates them monthly; `uv run hhsf ingest parcels --refresh` downloads them again. Owner names come with the parcels and are kept in their own table, which the map never reads (see [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md#parcels-and-owners)). Flood zones come from FEMA, one county at a time, cached in `data/raw/fema_nfhl/` (about 50 MB for the five counties). FEMA's server is slow and drops connections under load, so the first download takes from a few seconds to about 5 minutes per county (Rhea, the largest); the tool retries, and a re-run reuses the cache and takes a few seconds. Streams are saved as each file is read, so the map starts filling in before the run finishes. If it fails partway, run it again: nothing is lost or duplicated.

**Other counties.** Marion, Bledsoe, Grundy and Rhea are set up in `config/aoi/`. Run each step with `AOI=<key>`, one county at a time:

```bash
make ingest AOI=marion_tn
make analyze AOI=marion_tn
make score AOI=marion_tn
```

Keys: `marion_tn`, `bledsoe_tn`, `grundy_tn`, `rhea_tn`. They need two more river-basin files than Sequatchie (Upper Tennessee `0601`: 486 MB, Middle Tennessee-Elk `0603`: 152 MB). A new county's first `make analyze` takes 30 minutes to a couple of hours while StreamStats answers, longer if it keeps dropping out. The map shows every county you've scored, each ranked on its own.

<details>
<summary>Using the USGS web service instead of the file download</summary>

The older path queries the USGS web service box by box instead of downloading files. It's much slower (30 minutes to an hour for one county) and often times out, but downloads far less:

```bash
uv run hhsf ingest hydrography --source service
```

Each box is saved as it arrives and cached in `data/raw/`, so re-running resumes where it stopped. To make it the default, set `hydrography_source: service` in `config/data_sources.yaml`. Both paths load the same streams with the same IDs.

</details>

### 5. Run the hydro analysis

```bash
make analyze
```

For every stream this finds its **steepest stretch of up to 1,000 ft** on the elevation model (where one penstock would get the most drop), then works out the head over that stretch, a reliable low flow and the continuous power it could supply, and saves them to the `hydro_corridors` table as a new numbered run. A stream shorter than 1,000 ft is used whole. Low flows come from the USGS StreamStats service; if it outlines the wrong watershed for a stream, the tool asks again further up that stream. Streams too small for StreamStats' equations get the median StreamStats low flow per square mile of the bigger streams in their river basin (HUC8), marked low confidence. If the service is down, streams on the Sequatchie River side borrow the low flow measured at the USGS gauge near Whitwell, scaled by drainage area, also low confidence. Answers are cached in `data/cache/`, so a re-run only asks for what's missing. The first full run takes about 30 minutes while StreamStats answers. If it's down, the run still finishes with fallback estimates, clearly flagged, and says how many streams were affected ("StreamStats looks unavailable: N reaches…"); run it again later. What each number means and where it comes from: [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).

### 6. Score and rank

```bash
make score
```

Scores the latest hydro run and prints the top 20. Streams of 1 kW or more get a 0–100 hydro score (70% power, 30% head), and every score is saved with an explanation of how it was reached. The list is ranked by confidence first: every stream whose low flow came from StreamStats ranks above every stream whose flow is a rougher estimate, then streams with no flow estimate ("head only"), then streams under 1 kW on a rough flow estimate ("low flow", ordered by power). Streams under 1 kW on a StreamStats flow are dropped, and so is any stream with under 10 ft of net head (too little drop for a small turbine). Each scoring is saved as a new numbered run in the `hydro_scores` table. Details: [docs/SCORING.md](docs/SCORING.md).

### 7. Export the shortlist

```bash
make export
```

Writes the latest scoring to `data/outputs/sequatchie_tn/run_<N>/` (N is the score run number):

| File | What's in it |
| --- | --- |
| `report.md` | Summary for planning visits: which runs and data it came from, the assumptions used, how many streams made the list, the top 20 of each confidence tier with each one's turbine parcel, owner and flood zone, how many stretches are in a flood zone, and the caveats. Start here. |
| `shortlist.csv` | Every ranked stream, best first: score, net head (ft), firm power (W), firm flow (cfs and gallons per minute), drainage area (sq mi), the stretch's length (ft) and the whole stream's (mi), how the flow was estimated, a lat/lon point at the bottom of the stretch (where a turbine would go), and a one-line explanation. Then the property: the turbine parcel's ID, county, acreage (measured and on the deed), owner(s), street address and a link to its record on the Comptroller's site, plus every parcel the stretch crosses and their owners, and flags such as `parcel_under_min_acres`. Then flooding: FEMA's zone at the turbine end (`turbine_flood_zone`, e.g. A, AE or X, and its subtype), whether that's in the 100-year floodplain (`turbine_in_sfha`), how many feet of the stretch are (`stretch_sfha_ft`), and `flood_flags` such as `turbine_in_floodway` or `flood_zone_not_mapped`. Opens in Excel or Google Sheets. |
| `shortlist.geojson` | The same stretches as map lines, for QGIS, Google Earth or [geojson.io](https://geojson.io). Parcel IDs and acreage, but no owner names. |
| `all_corridors.csv` | Every stream, including the ones that were dropped, with the reason. Use it to answer "why isn't this stream on the list?". |

**The CSVs and the report name property owners. That's personal information: keep them to yourselves, and don't post them or paste them into issues.** Parcels are matched when you export, so run `make export` again after re-ingesting parcels.

Exporting again overwrites that run's folder; earlier runs' folders are kept. `uv run hhsf export hydro --run-id N` exports an older score run.

### 8. Open the map

```bash
make web
```

Open <http://localhost:5173>. The map shows the latest `make score` run (refresh the page after scoring again):

![The map: each stream's steepest stretch colored by hydro score, with the legend and the top site's score explained](docs/images/map-scores.jpg)

| On the map | What it means |
| --- | --- |
| **Where the line is** | Only each stream's best stretch (up to 1,000 ft) is drawn, not the whole stream. Zoom in to see where it sits. |
| **Color** (light amber to dark brown) | Hydro score, 35 to 100. Darker means more power and more drop. About 35 is a stream that just reaches 1 kW. |
| **Thick solid line with a dark outline** | Medium confidence: the low flow is a USGS StreamStats estimate. These rank above everything else. |
| **Dashed line** | Low confidence: 1 kW or more, but on a rougher flow estimate. |
| **Purple dotted line** | Head only: the basin is too small for any flow estimate, so it's ranked on drop alone. Check the flow on a visit. |
| **Thin blue line** | Low flow: under 1 kW on a rough flow estimate (mostly small streams). Listed last; worth a look only if a visit finds a spring. |
| **Faint grey line** | Filtered out: under 1 kW on a StreamStats flow, or too little drop for a turbine. |
| **#12** labels (zoom in) | The stream's rank on its county's shortlist. |

To change the map underneath, click the layers button (two stacked sheets) under the zoom buttons and pick a thumbnail. The map remembers your choice next time:

| Map | Use it for |
| --- | --- |
| **Light** (default) | Reading the scores: plain grey streets and place names, so the colors stand out. |
| **Satellite** | Scouting a visit: access roads, clearings, houses and tree cover around a stretch. Aerial photos from USDA's farm survey (NAIP), about 1 m per pixel, taken in summer. Street and place names stay on top. |
| **Topo** | Terrain: USGS topo map with contour lines, so you can see how steep the ground is around a stretch. |

Satellite and topo come from the USGS National Map and stop sharpening past about 1:9,000 (zoom 16); closer than that the map just enlarges them. On both, streams and scores get a white outline so they show up on the darker background.

![The same stretch on the satellite map, with the map menu open: the top site sits in forest above a road and farm fields](docs/images/map-satellite.jpg)

Click a stretch to see its score and why: how many points power and head each added, which part of the stream it is (how long the stretch is, and how long the whole stream is and how much it drops), the net head (ft), firm flow (cfs and gallons per minute), firm power (W), where the flow number came from, the confidence group and any warnings. The popup also shows the **turbine parcel** (the parcel under the bottom of the stretch: its ID and acreage) and how many parcels the stretch crosses, then the **FEMA flood zone** at the turbine end and how much of the stretch is in the 100-year floodplain. Tick **Show property lines** to draw parcel outlines (pink; pale yellow on satellite) from zoom 12. Owner names are only in the exports. Tick **Show FEMA flood zones** to shade floodways (dark blue), the rest of the 100-year floodplain (blue) and the 0.2% annual chance ("500-year") area (pale blue) from zoom 10; the rest is FEMA zone X, minimal hazard, and isn't shaded.

Untick **Show hydro scores** to go back to plain streams (bigger streams darker), where a click shows elevation drop, length, slope, drainage area and estimated mean flow.

### Stopping

```bash
make down
```

## Usage

Run `make` to list all commands.

| Command | What it does |
| --- | --- |
| `make setup` | Install Python and Node dependencies |
| `make up` | Start the database and tile server, apply migrations |
| `make ingest` | Download and load data for the default area |
| `make ingest AOI=<key>` | Same, for another area defined in `config/aoi/` |
| `uv run hhsf ingest hydrography --source service` | Reload streams from the USGS web service instead of the file download |
| `uv run hhsf ingest terrain` | Just the elevation model step (after the streams are loaded) |
| `uv run hhsf ingest parcels --aoi <key>` | Just the parcels step; add `--refresh` to download the monthly update |
| `uv run hhsf ingest flood --aoi <key>` | Just the FEMA flood zones step; add `--refresh` to download again after a flood map revision |
| `make analyze` | Find each stream's steepest stretch, then compute head, low flow and firm power for it (after `make ingest`) |
| `uv run hhsf analyze hydro --penstock-ft 2000` | Same, allowing a longer penstock (D-08 sensitivity check) |
| `uv run hhsf analyze hydro --no-terrain` | Same, but each whole stream is one corridor (the old method; overstates long streams) |
| `uv run hhsf analyze hydro --no-streamstats` | Same, without calling StreamStats (every flow is a low-confidence fallback) |
| `uv run hhsf analyze hydro --no-gauge` | Same, without the stream gauge fallback |
| `uv run hhsf analyze hydro --no-regional-yield` | Same, but small streams get the gauge estimate in its basin and no flow elsewhere (ranked head only), the D-09 starting rule |
| `uv run hhsf score hydro --profile hydro_v2` | Score with the previous rules: a stream of 1 kW or more is scored however little drop it has |
| `uv run hhsf score hydro --profile hydro_v1` | Score with the oldest rules: also drops streams under 1 kW on a rough flow estimate |
| `make score` | Score and rank the latest hydro run (after `make analyze`) |
| `uv run hhsf score hydro --profile <version> --run-id <N>` | Score a specific hydro run with another scoring profile from `config/scoring_profiles/` |
| `make export` | Write the latest scores to `data/outputs/` as CSV, GeoJSON and a summary report (after `make score`) |
| `uv run hhsf export hydro --run-id <N>` | Export a specific score run |
| `make web` | Run the map at <http://localhost:5173> |
| `make test` | Run all tests (Docker must be running) |
| `make lint` | Check code style |
| `make down` | Stop services (data is kept) |

The pipeline tool can also be run directly:

```bash
uv run hhsf --help
```

## Configuration

| File | What it controls |
| --- | --- |
| `config/aoi/*.yaml` | Areas of interest. One file per county: Census ID, a buffer distance, (optionally) the river basins (HU4 codes) it spans, and (optionally) a reference stream gauge for low flows. Add a file to analyze another county; no code changes needed. If you leave out `huc4`, the basins are looked up automatically. |
| `config/data_sources.yaml` | Where each dataset is downloaded from, plus license and version notes. `hydrography_source` picks the stream source: `bulk` (file download, default) or `service` (web service). The `streamstats` section lists its endpoints and how hard to call it. |
| `config/hydro.yaml` | Hydro analysis assumptions: the longest penstock a corridor may assume (1,000 ft), efficiency, net-head and diversion allowances, the 1 kW target, which StreamStats statistic to use, and where to ask StreamStats again when it outlines the wrong watershed (`pour_point_retry_shares`, `min_pour_point_area_share`). Values come from [docs/DECISIONS.md](docs/DECISIONS.md); change them here and re-run. |
| `config/parcels.yaml` | The minimum-acreage placeholder (D-03) that flags small parcels in the exports. |
| `config/scoring_profiles/*.yaml` | Scoring rules: the 1 kW cutoff, the head and power breakpoints, the 70/30 weights and the confidence tiers. To change one, copy the file to a new version and score again (see [docs/SCORING.md](docs/SCORING.md)). |
| `.env` (optional) | Override the database connection (`DATABASE_URL`). Copy from `.env.example`. Only needed if you change the database port. |

## Project structure

```
hydro-homestead/
├── README.md
├── site-selection.md        # Requirements and engineering plan
├── docs/DECISIONS.md        # Judgment calls and their current answers
├── docs/ASSUMPTIONS.md      # How hydro numbers are calculated
├── docs/SCORING.md          # How corridors are scored and ranked
├── docs/images/             # Screenshots for this README
├── Makefile                 # Common commands
├── docker-compose.yml       # PostGIS + Martin
├── pyproject.toml           # Python dependencies and tool settings
├── alembic.ini
├── config/
│   ├── aoi/                 # Areas of interest
│   ├── data_sources.yaml    # Dataset endpoints and licenses
│   ├── hydro.yaml           # Hydro analysis assumptions
│   ├── parcels.yaml         # Minimum-acreage placeholder (D-03)
│   └── scoring_profiles/    # Versioned scoring rules
├── src/hhsf/                # Python pipeline
│   ├── cli.py               # `hhsf` command-line tool
│   ├── core/                # Config, database, coordinate systems, units
│   ├── ingest/              # Downloading and loading each dataset
│   └── analysis/            # Hydro calculations, StreamStats client, scoring, runs, exports
├── migrations/versions/     # Database table definitions
├── martin/martin.yaml       # Which tables the tile server publishes
├── frontend/                # React + MapLibre map app (src/scores.ts: score colors and popup; src/basemaps.ts and BasemapControl.ts: light, satellite and topo maps)
├── tests/
│   ├── unit/                # Fast, no Docker needed
│   ├── integration/         # Starts a throwaway PostGIS in Docker
│   └── fixtures/            # Small synthetic test data
└── data/                    # Downloaded data cache and exports (not committed)
    └── outputs/             # `make export` results, one folder per score run
```

## Data sources

| Data | Source | License |
| --- | --- | --- |
| County boundaries | [US Census TIGERweb](https://tigerweb.geo.census.gov/) | Public domain |
| Low-flow estimates (Q90) | [USGS StreamStats](https://streamstats.usgs.gov/) with the Tennessee regression equations (USGS SIR 2009-5159) | Public domain |
| Measured stream flow | [USGS NWIS](https://waterdata.usgs.gov/) daily flows at gauge 03571000 (Sequatchie River near Whitwell), and [WBD](https://www.usgs.gov/national-hydrography/watershed-boundary-dataset) basin boundaries (for the gauge's basin and the regional yield) | Public domain |
| Elevation model (10 m) | [USGS 3DEP](https://www.usgs.gov/3d-elevation-program) 1/3 arc-second DEM, cloud-optimized GeoTIFF tiles from the National Map (only the part under the AOI's streams is downloaded: about 84 MB for Sequatchie) | Public domain |
| Streams, elevations, drainage area, flow estimates | [USGS NHDPlus High Resolution](https://www.usgs.gov/national-hydrography/nhdplus-high-resolution) (final release; HU4 file geodatabases from the National Map, or its web service) | Public domain |
| Property parcels and owners | [Tennessee Comptroller of the Treasury](https://comptroller.tn.gov/office-functions/pa/gisredistricting/redistricting-and-land-use-maps/parcel-data.html) statewide parcel layer ("Tennessee Property Boundaries Public Use", on TNMap), 86 counties, updated monthly | Public service, not for commercial use, no warranty. Owner names are personal information: local exports only |
| Flood zones | [FEMA National Flood Hazard Layer](https://www.fema.gov/flood-maps/national-flood-hazard-layer) (effective flood maps, ArcGIS service at hazards.fema.gov). All five counties have countywide digital maps (2008–2010, some panels revised 2025) | US Government work, generally public domain (FEMA states no license). A planning screen, not a flood determination |
| Basemap (Light) | [OpenFreeMap](https://openfreemap.org/), © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors | ODbL |
| Basemap (Satellite, Topo) | [USGS The National Map](https://www.usgs.gov/programs/national-geospatial-program/national-map) tile services USGSImageryOnly (USDA NAIP aerial photos) and USGSTopo | Public domain |

Planned sources (soils, roads, building footprints, rainfall) are listed in [site-selection.md §4](site-selection.md).

## Testing

```bash
make test
```

Unit tests run without Docker. They include reading a tiny synthetic NHDPlus HR file built during the test, so no large files are needed, the steepest-stretch search on made-up elevation profiles and a tiny made-up elevation model, the head, flow and power calculations against a fake StreamStats service (no network), and every scoring and ranking rule. Integration tests start a temporary PostGIS container, run the migrations, load sample streams, check that re-loading doesn't create duplicates, and run the hydro analysis (with and without an elevation model), scoring and exports, including linking corridors to parcels and checking that owner names reach the CSVs but not the GeoJSON, and linking corridors to flood zones (the floodway wins where zones meet; turbine ends off the map are flagged). A unit test fails if any map layer in `martin/martin.yaml` gets an owner or address column.

To see test coverage of the calculation code:

```bash
uv run pytest --cov=hhsf.analysis --cov-report=term-missing
```

## Known issues and blockers

### Current

- **The USGS stream web service is slow and unreliable, so it's no longer the default.** It often takes minutes per request or returns `504 Gateway Timeout`; one county took about 30 minutes. `make ingest` now downloads the NHDPlus HR file for each river basin (HU4 region) instead: about 270 MB for Sequatchie County (Cumberland `0513`: 193 MB, Middle Tennessee-Hiwassee `0602`: 74 MB), cached in `data/raw/nhdplus_hr_bulk/`. Both sources load the same 989 perennial reaches with identical IDs and attributes. The web service path is still there (`--source service`) and now saves each box as it arrives.
- **Some regions are large downloads.** Basin files range from about 75 MB to over 2 GB (e.g. Lower Ohio `0512`: 2.4 GB). Check the sizes before adding an AOI in a new basin; the ingest prints each file's size as it starts downloading.
- **Use the "VPU/Current" USGS files, not "Beta".** USGS also publishes older "Beta" files. For the Tennessee River basin (`0602`) they give many streams different IDs from the web service, which would break anything matched on IDs later. `config/data_sources.yaml` points at the current folder.
- **StreamStats is sometimes down or slow.** Its basin-characteristics service returned 502 errors all day on 2026-10-18 and was back on 2026-10-19. On 2026-10-20 it dropped out over a dozen times between 11:00 and 20:00, so the four new counties took about 11 hours instead of 2. When it's down, larger streams fall back to the gauge estimate (Sequatchie River basin), the regional yield or the NHDPlus *mean* flow, all marked low confidence, and the run says how many streams that hit; run `make analyze` again later. A run that hit an outage is not a usable result. Finished answers are cached. USGS asks for no more than 4 simultaneous requests, which the tool respects.
- **The gauge fallback is rough.** It assumes every square mile yields the same low flow as the 402 sq mi Whitwell basin, while the streams here are under 70 sq mi. Where both exist, it gives a median of about **10× StreamStats' low flow**, probably because the gauge's basin includes the spring-fed limestone valley. Treat gauge-based numbers as an upper bound. Since D-09 was decided it's only used for 4 larger streams StreamStats can't outline. Details in [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).
- **Most streams are too small for the low-flow equations.** Tennessee's equations only apply to basins of 1.3 sq mi or more; 635 of Sequatchie's 989 perennial reaches are smaller. They all get a **regional yield**, the typical StreamStats low flow per square mile of the bigger streams in the same river basin (HUC8), marked low confidence and flagged `below_regression_min_drainage_area`. It's an extrapolation and can't see springs; on the Collins River side StreamStats' own basin data suggests it may be up to 5× low, and on the Sequatchie Valley floor spring-fed streams can carry 5–10× more. See [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md#regional-low-flow-yield) and [DECISIONS.md D-09](docs/DECISIONS.md).
- **Only six streams in five counties clear 1 kW over a 1,000 ft penstock**, all on StreamStats flows: Rogers Branch (Sequatchie), an unnamed stream and Bethel Creek (Marion), and Negro Den Creek, the Elk River headwater and Big Creek (Grundy), at 1.0–1.6 kW. Bledsoe and Rhea have none. In Sequatchie only Rogers Branch does (about 1.3 kW). Head is measured over each stream's steepest 1,000 ft stretch (D-08) instead of the whole stream, which brought the largest ranked net head down from 1,101 ft to 414 ft. Five plateau-edge streams made 1.1–1.5 kW on the gauge estimate; on the regional yield they make 105–146 W and are now ranks 2–6, at the top of the low-flow list. The penstock length matters a lot: before the regional yield, 1 / 6 / 19 streams reached 1 kW at 500 / 1,000 / 2,000 ft (0 / 1 / 3 on StreamStats flows). Worth deciding what pipe length is realistic. See [docs/DECISIONS.md D-08](docs/DECISIONS.md) and [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md#how-the-steepest-stretch-is-found).
- **StreamStats sometimes outlines the wrong watershed.** It moves each point onto its own stream grid, which can land on a bigger stream at a confluence or on a hillside next to the creek. When the area it finds is more than 25% off NHDPlus's, the tool asks again 25%, 50% and 75% of the way up the stream and scales the answer back down to the stream's end. In Sequatchie that fixed 18 of the 22 wrong answers: the larger Walden Ridge creeks (Soddy, North Suck, Cain, Conner and others) that had been on the NHDPlus *mean annual* flow, 130–190× their dry-season flow. Thirteen of them had been on the shortlist at 1–21 kW; on dry-season flow none reaches 200 W. The other 4 still use the gauge estimate. See [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md#when-streamstats-snaps-to-the-wrong-stream).
- **The stretch search uses 10 m elevations along the mapped stream line.** It can miss a small waterfall, read a few meters high where the line runs along a bank, and measures pipe length along the stream (a real pipe can cut bends). Each stretch is capped at the drop NHDPlus gives the whole stream. Confirm the drop on the visit.
- **Most of the ranked list is "low flow".** The 272 "head only" streams that used to fill most of the list now have a power estimate from the regional yield, and no small stream reaches 1 kW on it (best 146 W, median 9 W). Rather than drop them, the list keeps 575 of them last, ordered by power (D-01, D-09): their flow is a typical-basin number, so a spring-fed one with lots of head could still work. They need a dry-season flow check on the visit. A second reference gauge was tried instead and rejected: Collins River near McMinnville runs 10× high like Whitwell, and the Walden Ridge gauges disagree with each other. See [docs/SCORING.md](docs/SCORING.md).
- **The regional yield differs by county.** Each county's run builds the small-stream yield from its own StreamStats streams, and some differ 3–4× in the same river basin: Bledsoe's part of the Sequatchie River basin gets 0.068 cfs per sq mi against Sequatchie's 0.019, and Marion's Battle Creek side 0.083 against Grundy's 0.022. So a stream within 500 m of a county line, analyzed for both counties, can make 3–4× more power in one county's run than the other's. The map shows each stream once, with the numbers of the county it sits in; the exports list it in both. Pooling the yield across counties would fix it. See [docs/SCORING.md](docs/SCORING.md#five-counties-part-13-profile-hydro_v3-2026-10-20).
- **Ranks are per county.** Each county has its own #1, so the map shows several. The popup says which county a rank is in.
- **Parcel lines are for reference, not surveys.** The state's layer is "not intended for commercial use" and comes with no warranty; owner names can lag a sale. Check the deed through the parcel's TPAD link before contacting anyone. Details: [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md#parcels-and-owners).
- **140 ranked stretches have no parcel.** They end outside the five counties (streams within 500 m of a county line are analyzed for both counties), where no parcels are loaded. Streams are often property lines, so a stretch usually crosses the parcels on both banks; the turbine parcel is just the one under its bottom end.
- **The three biggest 1 kW streams sit on very large parcels** (1,355 to 10,481 acres), likely timber, conservation or public land. Check the owner type before planning a visit.
- **Flood zones are flagged, not used in the score (decided, D-12).** 369 of the five counties' 5,233 ranked stretches touch FEMA's 100-year floodplain: 331 have their turbine end in it, 13 of those in a floodway. None of the six 1 kW streams has its turbine end in it: all six end in zone X (minimal hazard), and only Bethel Creek's stretch crosses the floodplain, for about 240 ft. See [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md#flood-zones).
- **"Minimal hazard" on a small creek usually means unstudied.** FEMA generally maps streams draining about a square mile or more; most of the ranked stretches are on smaller creeks, inside zone X. Grundy County's flood map shows only a handful of studied streams. 161 ranked stretches show "not mapped": 158 end outside the five counties, and 3 sit within 8 m of a county line where FEMA's and Census's county outlines differ.
- **A long stream can show its rank label twice on the map.** Map tiles cut streams at tile edges and each piece gets a label. Both labels are the same stream; click either for its details.

### Upcoming (will block later milestones)

- **The 1-hour drive radius needs data we don't have yet.** It needs a road network and a routing tool to calculate drive times (D-02).
- **Hamilton County (Chattanooga itself) isn't in the state parcel layer.** It, and eight other counties, keep their own parcels; the regional run will need Hamilton's source.
- **Some decisions are still open.** Minimum acreage, budget and privacy weighting (D-03). Parcels are loaded, but small ones are only flagged until it's decided.
- **Permits are outside the tool.** Putting a turbine on a Tennessee stream needs state (TDEC ARAP) and possibly federal (FERC) approval. The tool won't evaluate this, so every site needs a permitting check before it's a real option.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `make up` fails with "port is already allocated" | Run `docker compose ls`. If it lists a project other than `hydro-homestead`, that's a second copy of this stack started from another folder (older checkouts named it after the folder): stop it with `docker compose -p <name> down`. Otherwise another program is using 5433 or 3100: change the left-hand port in `docker-compose.yml`, and for 5433 also set `DATABASE_URL` in `.env`. |
| `make up` fails with "Can't locate revision" | The shared database was migrated by a newer branch than the one checked out (all checkouts and worktrees share one database). Run `make up` from that newer branch, or wait until it's merged and pull `main`. Don't delete the database to get past it; that loses every run. |
| `make up` fails with "Cannot connect to the Docker daemon" | Start Docker Desktop or OrbStack. |
| `make ingest` fails during a download | Run it again. Partial downloads are never reused; finished files in `data/raw/nhdplus_hr_bulk/` are. |
| A downloaded file seems broken (errors opening a `.zip`) | Delete it from `data/raw/nhdplus_hr_bulk/` and run `make ingest` again. |
| `--source service` fails with "Query failed after 4 attempts" | The USGS service timed out. Run it again; finished boxes are reused. |
| `make analyze` says "No elevation model" | Run `uv run hhsf ingest terrain` (or `make ingest`), or pass `--no-terrain` to use whole streams. |
| `make analyze` says "StreamStats looks unavailable" | The USGS service is down or overloaded. The run still finishes with flagged fallback flows; run it again later to get Q90. |
| `make export` leaves the parcel columns empty | Parcels aren't loaded: run `uv run hhsf ingest parcels --aoi <key>` (or `make ingest`), then export again. |
| `make ingest` stops in the flood step with "Query failed after 4 attempts" | FEMA's server dropped the connection (it does under load). Run `uv run hhsf ingest flood --aoi <key>` again; finished pages in `data/raw/fema_nfhl/` are reused. |
| `make export` leaves the flood columns empty | Flood zones aren't loaded: run `uv run hhsf ingest flood --aoi <key>`, then export again. |
| **Show FEMA flood zones** shades nothing | Zoom in past zoom 10, and check <http://localhost:3100/catalog> lists `flood_zones`; if not, run `make up`. Small creeks often have no flood zone at all: FEMA didn't study them. |
| **Show property lines** draws nothing | Zoom in past zoom 12 (about 1:150,000), and check <http://localhost:3100/catalog> lists `parcels`; if not, run `make up`. |
| The map shows no streams | Check `make ingest` finished, and that <http://localhost:3100/catalog> lists `streams`. |
| Popups say "ranked #N overall" instead of naming the county | The county was loaded before the map knew which AOI it belongs to. Run `uv run hhsf ingest boundary --aoi <key>` for each county, then refresh. |
| Every stream is grey, or none are colored | Run `make score`, then refresh the page. The colors come from the latest score run. |
| Popups have no score breakdown or flags, or don't say which stretch of the stream it is | Run `make up` again: it applies the newest database migration and restarts the tile server. |
| Tests fail to start a container | Docker must be running for integration tests. Run `uv run pytest tests/unit` to skip them. |

## Roadmap

From [site-selection.md §7](site-selection.md):

1. ~~Project skeleton, database, stream ingest, map~~
2. ~~Hydro analysis: head, reliable low flow, firm power, over the steepest usable stretch of each stream~~ Low flow comes from StreamStats for the bigger streams and from a regional yield per river basin for the small ones (D-09). Still to do: a way to spot spring-fed small streams, which the regional yield can't see
3. ~~Scoring and ranking against the 1 kW minimum, with an explanation for every score~~ Still to do: the original FR-3 weight table as a fallback profile, and the weighting sensitivity check
4. ~~Exports (CSV, GeoJSON), a summary report, and the scores on the map~~
5. ~~Parcels and ownership~~ Still to do: a minimum acreage filter once D-03 is decided, and Hamilton County's own parcel source
6. ~~Flood zones~~ Flagged only (D-12). Still to do: check each parcel for buildable ground outside the floodplain, with soils and slope
7. Then: soils, roads and drive time, and the full Chattanooga region
