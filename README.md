# Hydro Homestead Site Finder

A local map-based tool for finding land near Chattanooga, TN that could support a homestead powered by a small hydroelectric (micro-hydro) system.

## Goal

Produce a **shortlist of land worth visiting in person**: sites with a year-round stream that has enough drop and flow to generate at least 1 kW of continuous power, within about an hour's drive of Chattanooga. Later versions add parcel ownership so landowners can be contacted, **whether or not the land is listed for sale**.

This is different from a real estate search. It starts from the terrain and water, finds physically suitable land, and only then asks who owns it.

- Full requirements and engineering plan: [site-selection.md](site-selection.md)
- Open judgment calls and current answers (power target, search radius, scoring assumptions): [docs/DECISIONS.md](docs/DECISIONS.md)

## Status

**v0.1, in progress.** Test area: Sequatchie County, TN.

| Works now | Not built yet |
| --- | --- |
| Loads the county boundary and every perennial (year-round) stream in the county into a local database | Hydro corridor detection (finding the steepest usable stretch of each stream) |
| Records each stream's elevation drop, length, slope, drainage area and estimated mean flow | Reliable low-flow estimates and firm power (watts) |
| Map viewer: click any stream to see its numbers | Scoring, ranking and the shortlist |
| Tests for unit conversions, coordinate math, the perennial filter and database loading | Parcels, ownership, soils, flood zones, roads, reports |

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

This downloads the Sequatchie County boundary and streams from USGS and Census and loads them into the database. **It can take a while: anywhere from a few minutes to about an hour**, depending on how responsive the USGS service is (see [Known issues and blockers](#known-issues-and-blockers)). If it fails partway, run it again. Finished pieces are cached in `data/raw/` and reused.

### 5. Open the map

```bash
make web
```

Open <http://localhost:5173>. Click any stream to see its elevation drop, length, slope, drainage area and estimated flow.

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
| `config/aoi/*.yaml` | Areas of interest. One file per county: Census ID and a buffer distance. Add a file to analyze another county; no code changes needed. |
| `config/data_sources.yaml` | Where each dataset is downloaded from, plus license and version notes |
| `.env` (optional) | Override the database connection (`DATABASE_URL`). Copy from `.env.example`. Only needed if you change the database port. |

## Project structure

```
hydro-homestead/
├── README.md
├── site-selection.md        # Requirements and engineering plan
├── docs/DECISIONS.md        # Judgment calls and their current answers
├── Makefile                 # Common commands
├── docker-compose.yml       # PostGIS + Martin
├── pyproject.toml           # Python dependencies and tool settings
├── alembic.ini
├── config/
│   ├── aoi/                 # Areas of interest
│   └── data_sources.yaml    # Dataset endpoints and licenses
├── src/hhsf/                # Python pipeline
│   ├── cli.py               # `hhsf` command-line tool
│   ├── core/                # Config, database, coordinate systems, units
│   └── ingest/              # Downloading and loading each dataset
├── migrations/versions/     # Database table definitions
├── martin/martin.yaml       # Which tables the tile server publishes
├── frontend/                # React + MapLibre map app
├── tests/
│   ├── unit/                # Fast, no Docker needed
│   ├── integration/         # Starts a throwaway PostGIS in Docker
│   └── fixtures/            # Small synthetic test data
└── data/                    # Downloaded data cache (not committed)
```

## Data sources

| Data | Source | License |
| --- | --- | --- |
| County boundaries | [US Census TIGERweb](https://tigerweb.geo.census.gov/) | Public domain |
| Streams, elevations, drainage area, flow estimates | [USGS NHDPlus High Resolution](https://www.usgs.gov/national-hydrography/nhdplus-high-resolution) (final release, via the National Map web service) | Public domain |
| Basemap | [OpenFreeMap](https://openfreemap.org/), © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors | ODbL |

Planned sources (soils, flood zones, parcels, roads, building footprints, rainfall) are listed in [site-selection.md §4](site-selection.md).

## Testing

```bash
make test
```

Unit tests run without Docker. Integration tests start a temporary PostGIS container, run the migrations, load sample streams, and check that re-loading doesn't create duplicates.

## Known issues and blockers

### Current

- **The USGS stream service is slow and unreliable.** It often takes minutes per request or returns `504 Gateway Timeout`, especially for large areas. The ingest works around this by splitting the county into small boxes, retrying failures and caching each finished box, so re-running `make ingest` resumes where it stopped. A full county can still take up to an hour on a bad day. **Fallback if it gets worse:** switch to the bulk NHDPlus HR file download (a few GB per region), which avoids the service entirely.
- **Flow estimates are mean annual flow only.** The streams carry USGS's estimated *average* flow, which overstates what a turbine can count on year-round. Sizing for the 1 kW minimum needs a *reliable low-flow* estimate (the flow met 90% of the time), which means integrating USGS StreamStats. That's next on the roadmap.

### Upcoming (will block later milestones)

- **Small headwater streams have uncertain flow.** Tennessee's low-flow equations have large error margins and don't apply below a minimum drainage area, and many promising micro-hydro sites are that small. They'll be kept and flagged as low confidence rather than dropped ([DECISIONS.md D-09](docs/DECISIONS.md)).
- **The 1-hour drive radius needs data we don't have yet.** It needs a road network and a routing tool to calculate drive times (D-02).
- **Parcel and ownership data have gaps and restrictions.** Hamilton County (Chattanooga itself) isn't in the Tennessee statewide parcel layer and needs its own source. Owner names and addresses are personal information under assessor terms of use, so they should be stored carefully and not shared publicly.
- **Some decisions are still open.** Minimum acreage, budget and privacy weighting (D-03) are needed once parcels are added.
- **Permits are outside the tool.** Putting a turbine on a Tennessee stream needs state (TDEC ARAP) and possibly federal (FERC) approval. The tool won't evaluate this, so every site needs a permitting check before it's a real option.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `make up` fails with "port is already allocated" | Another program is using 5433 or 3100. Change the left-hand port in `docker-compose.yml`; for 5433, also set `DATABASE_URL` in `.env`. |
| `make up` fails with "Cannot connect to the Docker daemon" | Start Docker Desktop or OrbStack. |
| `make ingest` fails with "Query failed after 4 attempts" | The USGS service timed out. Run `make ingest` again; finished pieces are reused. |
| The map shows no streams | Check `make ingest` finished, and that <http://localhost:3100/catalog> lists `streams`. |
| Tests fail to start a container | Docker must be running for integration tests. Run `uv run pytest tests/unit` to skip them. |

## Roadmap

From [site-selection.md §7](site-selection.md):

1. ~~Project skeleton, database, stream ingest, map~~ (this version)
2. Hydro analysis: steepest usable stretch per stream, reliable low flow from StreamStats, firm power
3. Scoring and ranking against the 1 kW minimum, with an explanation for every score
4. Exports (CSV, GeoJSON) and a summary report
5. Then: parcels and ownership, soils, flood zones, roads and drive time, and the full Chattanooga region
