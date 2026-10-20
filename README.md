# Hydro Homestead Site Finder

A local map-based tool for finding land near Chattanooga, TN that could support a homestead powered by a small hydroelectric (micro-hydro) system.

## Goal

Produce a **shortlist of land worth visiting in person**: sites with a year-round stream that has enough drop and flow to generate at least 1 kW of continuous power, within about an hour's drive of Chattanooga. Later versions add parcel ownership so landowners can be contacted, **whether or not the land is listed for sale**.

This is different from a real estate search. It starts from the terrain and water, finds physically suitable land, and only then asks who owns it.

- Full requirements and engineering plan: [site-selection.md](site-selection.md)
- Open judgment calls and current answers (power target, search radius, scoring assumptions): [docs/DECISIONS.md](docs/DECISIONS.md)
- How head, flow and power are calculated, and every assumption behind them: [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)
- How corridors are scored and ranked: [docs/SCORING.md](docs/SCORING.md)

## Status

**v0.1, in progress.** Test area: Sequatchie County, TN.

| Works now | Not built yet |
| --- | --- |
| Loads the county boundary and every perennial (year-round) stream in the county into a local database | Hydro corridor detection (finding the steepest usable stretch of each stream; today each whole stream is one corridor) |
| Records each stream's elevation drop, length, slope, drainage area and estimated mean flow | Low-flow estimates for small streams on the Collins River side of the county |
| Map viewer: every stream colored by its hydro score and styled by confidence, with a legend; click a stream for its score explained in plain words | Corridors on the map (once the steepest-stretch search exists) |
| Hydro analysis: head, reliable low flow (Q90 from USGS StreamStats, or scaled from the Whitwell stream gauge where StreamStats can't help) and firm power for every stream, saved as a numbered run | Parcels, ownership, soils, flood zones, roads |
| Scoring and ranking: streams under 1 kW dropped, the rest scored 0–100 on power and head with an explanation for each score, and ranked by confidence | |
| Exports: the ranked shortlist as CSV and GeoJSON, every stream with the reason it was or wasn't listed, and a summary report for planning visits | |

## How it works

```
Public data services ──▶ hhsf (Python) ──▶ PostGIS database ──▶ Martin tile server ──▶ React map
(USGS, US Census)        download + load     (Docker)            (Docker)              (browser)
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

This downloads the Sequatchie County boundary from Census and the stream data from USGS, then loads them into the database. The first run downloads about **270 MB** of USGS stream files (one per river basin the county touches) and takes **a minute or two** on a typical connection; after that the files are reused from `data/raw/` and a re-run takes about 30 seconds. Streams are saved as each file is read, so the map starts filling in before the run finishes. If it fails partway, run it again: nothing is lost or duplicated.

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

For every stream this works out the head (elevation drop), a reliable low flow and the continuous power it could supply, and saves them to the `hydro_corridors` table as a new numbered run. Low flows come from the USGS StreamStats service. Where it can't help (small streams, or the service is down), streams on the Sequatchie River side borrow the low flow measured at the USGS gauge near Whitwell, scaled by drainage area and marked low confidence. Answers are cached in `data/cache/`, so a re-run only asks for what's missing. The first full run takes about 30 minutes while StreamStats answers; if it's down, the run finishes in seconds with the fallback estimates, clearly flagged. What each number means and where it comes from: [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).

### 6. Score and rank

```bash
make score
```

Scores the latest hydro run and prints the top 20. Streams below 1 kW are dropped; the rest get a 0–100 hydro score (70% power, 30% head), and every score is saved with an explanation of how it was reached. The list is ranked by confidence first: every stream whose low flow came from StreamStats ranks above every stream whose flow is a rougher estimate, and streams too small for any flow estimate come last, marked "head only". Each scoring is saved as a new numbered run in the `hydro_scores` table. Details: [docs/SCORING.md](docs/SCORING.md).

### 7. Export the shortlist

```bash
make export
```

Writes the latest scoring to `data/outputs/sequatchie_tn/run_<N>/` (N is the score run number):

| File | What's in it |
| --- | --- |
| `report.md` | Summary for planning visits: which runs and data it came from, the assumptions used, how many streams made the list, the top 20 of each confidence tier, and the caveats. Start here. |
| `shortlist.csv` | Every ranked stream, best first: score, net head (ft), firm power (W), firm flow (cfs and gallons per minute), drainage area (sq mi), how the flow was estimated, a lat/lon point at the stream's downstream end, and a one-line explanation. Opens in Excel or Google Sheets. |
| `shortlist.geojson` | The same streams as map lines, for QGIS, Google Earth or [geojson.io](https://geojson.io). |
| `all_corridors.csv` | Every stream, including the ones that were dropped, with the reason. Use it to answer "why isn't this stream on the list?". |

Exporting again overwrites that run's folder; earlier runs' folders are kept. `uv run hhsf export hydro --run-id N` exports an older score run.

### 8. Open the map

```bash
make web
```

Open <http://localhost:5173>. The map shows the latest `make score` run (refresh the page after scoring again):

![The map: streams colored by hydro score, with the legend and a stream's score explained](docs/images/map-scores.jpg)

| On the map | What it means |
| --- | --- |
| **Color** (light amber to dark brown) | Hydro score, 35 to 100. Darker means more power and more drop. About 35 is a stream that just reaches 1 kW. |
| **Thick solid line with a dark outline** | Medium confidence: the low flow is a USGS StreamStats estimate. These rank above everything else. |
| **Dashed line** | Low confidence: the flow is a rougher estimate, usually scaled from the Whitwell gauge, which runs high (treat it as an upper bound). |
| **Purple dotted line** | Head only: the basin is too small for any flow estimate, so it's ranked on drop alone. Check the flow on a visit. |
| **Faint grey line** | Filtered out: under 1 kW, or too little drop for a turbine. |
| **#12** labels (zoom in) | The stream's rank on the shortlist. |

Click a stream to see its score and why: how many points power and head each added, the net head (ft), firm flow (cfs and gallons per minute), firm power (W), where the flow number came from, the confidence group and any warnings. Untick **Show hydro scores** to go back to plain streams (bigger streams darker), where a click shows elevation drop, length, slope, drainage area and estimated mean flow.

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
| `make analyze` | Compute head, low flow and firm power for every stream (after `make ingest`) |
| `uv run hhsf analyze hydro --no-streamstats` | Same, without calling StreamStats (every flow is a low-confidence fallback) |
| `uv run hhsf analyze hydro --no-gauge` | Same, without the stream gauge fallback |
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
| `config/hydro.yaml` | Hydro analysis assumptions: efficiency, net-head and diversion allowances, the 1 kW target, and which StreamStats statistic to use. Values come from [docs/DECISIONS.md](docs/DECISIONS.md); change them here and re-run. |
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
│   └── scoring_profiles/    # Versioned scoring rules
├── src/hhsf/                # Python pipeline
│   ├── cli.py               # `hhsf` command-line tool
│   ├── core/                # Config, database, coordinate systems, units
│   ├── ingest/              # Downloading and loading each dataset
│   └── analysis/            # Hydro calculations, StreamStats client, scoring, runs, exports
├── migrations/versions/     # Database table definitions
├── martin/martin.yaml       # Which tables the tile server publishes
├── frontend/                # React + MapLibre map app (src/scores.ts: score colors and popup)
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
| Measured stream flow | [USGS NWIS](https://waterdata.usgs.gov/) daily flows at gauge 03571000 (Sequatchie River near Whitwell), and [WBD](https://www.usgs.gov/national-hydrography/watershed-boundary-dataset) basin boundaries | Public domain |
| Streams, elevations, drainage area, flow estimates | [USGS NHDPlus High Resolution](https://www.usgs.gov/national-hydrography/nhdplus-high-resolution) (final release; HU4 file geodatabases from the National Map, or its web service) | Public domain |
| Basemap | [OpenFreeMap](https://openfreemap.org/), © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors | ODbL |

Planned sources (soils, flood zones, parcels, roads, building footprints, rainfall) are listed in [site-selection.md §4](site-selection.md).

## Testing

```bash
make test
```

Unit tests run without Docker. They include reading a tiny synthetic NHDPlus HR file built during the test, so no large files are needed, the head, flow and power calculations against a fake StreamStats service (no network), and every scoring and ranking rule. Integration tests start a temporary PostGIS container, run the migrations, load sample streams, check that re-loading doesn't create duplicates, and run the hydro analysis, scoring and exports.

To see test coverage of the calculation code:

```bash
uv run pytest --cov=hhsf.analysis --cov-report=term-missing
```

## Known issues and blockers

### Current

- **The USGS stream web service is slow and unreliable, so it's no longer the default.** It often takes minutes per request or returns `504 Gateway Timeout`; one county took about 30 minutes. `make ingest` now downloads the NHDPlus HR file for each river basin (HU4 region) instead: about 270 MB for Sequatchie County (Cumberland `0513`: 193 MB, Middle Tennessee-Hiwassee `0602`: 74 MB), cached in `data/raw/nhdplus_hr_bulk/`. Both sources load the same 989 perennial reaches with identical IDs and attributes. The web service path is still there (`--source service`) and now saves each box as it arrives.
- **Some regions are large downloads.** Basin files range from about 75 MB to over 2 GB (e.g. Lower Ohio `0512`: 2.4 GB). Check the sizes before adding an AOI in a new basin; the ingest prints each file's size as it starts downloading.
- **Use the "VPU/Current" USGS files, not "Beta".** USGS also publishes older "Beta" files. For the Tennessee River basin (`0602`) they give many streams different IDs from the web service, which would break anything matched on IDs later. `config/data_sources.yaml` points at the current folder.
- **StreamStats is sometimes down or slow.** Its basin-characteristics service returned 502 errors all day on 2026-10-18 and was back on 2026-10-19. When it's down, streams fall back to the gauge estimate (Sequatchie River side) or the NHDPlus *mean* flow (Collins River side), both marked low confidence; run `make analyze` again later. Finished answers are cached. USGS asks for no more than 4 simultaneous requests, which the tool respects.
- **The gauge fallback is rough.** It assumes every square mile yields the same low flow as the 402 sq mi Whitwell basin, while the streams here are under 70 sq mi. Where both exist, it gives a median of about **10× StreamStats' low flow**, probably because the gauge's basin includes the spring-fed limestone valley. Treat gauge-based numbers as an upper bound. Details in [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).
- **Most streams are too small for the low-flow equations.** Tennessee's equations only apply to basins of 1.3 sq mi or more; 635 of Sequatchie's 989 perennial reaches are smaller. On the Sequatchie River side they now get the low-confidence gauge estimate; on the Collins River side they keep their head but get no flow or power estimate (`below_applicability`), as [DECISIONS.md D-09](docs/DECISIONS.md) says.
- **Power is per whole stream reach.** Until the steepest-stretch search (D-08) exists, power assumes a penstock the length of the whole reach, which overstates long reaches (the largest is over 200 kW). It also skews the ranking: the top medium-confidence streams show over 800 ft of drop, which is the whole reach, not what one penstock would capture. Treat the order within each confidence group as rough until D-08 is built. See [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).
- **Most of the ranked list has no flow estimate.** Of the 349 streams `make score` ranks, 283 are "head only": their basins are too small for any flow estimate, so they're ranked on drop alone, below every stream with a power estimate. They need a dry-season flow check on the visit. A better small-basin estimate (for example a second gauge for the Collins River side) would help most. See [docs/SCORING.md](docs/SCORING.md).
- **A long stream can show its rank label twice on the map.** Map tiles cut streams at tile edges and each piece gets a label. Both labels are the same stream; click either for its details.

### Upcoming (will block later milestones)

- **The 1-hour drive radius needs data we don't have yet.** It needs a road network and a routing tool to calculate drive times (D-02).
- **Parcel and ownership data have gaps and restrictions.** Hamilton County (Chattanooga itself) isn't in the Tennessee statewide parcel layer and needs its own source. Owner names and addresses are personal information under assessor terms of use, so they should be stored carefully and not shared publicly.
- **Some decisions are still open.** Minimum acreage, budget and privacy weighting (D-03) are needed once parcels are added.
- **Permits are outside the tool.** Putting a turbine on a Tennessee stream needs state (TDEC ARAP) and possibly federal (FERC) approval. The tool won't evaluate this, so every site needs a permitting check before it's a real option.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `make up` fails with "port is already allocated" | Another program is using 5433 or 3100. Change the left-hand port in `docker-compose.yml`; for 5433, also set `DATABASE_URL` in `.env`. |
| `make up` fails with "Cannot connect to the Docker daemon" | Start Docker Desktop or OrbStack. |
| `make ingest` fails during a download | Run it again. Partial downloads are never reused; finished files in `data/raw/nhdplus_hr_bulk/` are. |
| A downloaded file seems broken (errors opening a `.zip`) | Delete it from `data/raw/nhdplus_hr_bulk/` and run `make ingest` again. |
| `--source service` fails with "Query failed after 4 attempts" | The USGS service timed out. Run it again; finished boxes are reused. |
| `make analyze` says "StreamStats looks unavailable" | The USGS service is down or overloaded. The run still finishes with flagged fallback flows; run it again later to get Q90. |
| The map shows no streams | Check `make ingest` finished, and that <http://localhost:3100/catalog> lists `streams`. |
| Every stream is grey, or none are colored | Run `make score`, then refresh the page. The colors come from the latest score run. |
| Popups have no score breakdown or flags | Run `make up` again: it applies the newest database migration and restarts the tile server. |
| Tests fail to start a container | Docker must be running for integration tests. Run `uv run pytest tests/unit` to skip them. |

## Roadmap

From [site-selection.md §7](site-selection.md):

1. ~~Project skeleton, database, stream ingest, map~~
2. ~~Hydro analysis: head, reliable low flow from StreamStats, firm power per stream~~ Still to do: the steepest usable stretch within each stream (needs terrain data), and a gauge for the Collins River side
3. ~~Scoring and ranking against the 1 kW minimum, with an explanation for every score~~ Still to do: the original FR-3 weight table as a fallback profile, and the weighting sensitivity check
4. ~~Exports (CSV, GeoJSON), a summary report, and the scores on the map~~ (this version)
5. Then: parcels and ownership, soils, flood zones, roads and drive time, and the full Chattanooga region
