"""Buildings, and how private each corridor's home site is (PART 21, FR-6).

- `buildings`: Microsoft Global ML Building Footprints (`hhsf ingest buildings`), one
  row per outline, loaded a web-map tile (`quadkey`) at a time. Served as map tiles.
- `corridor_privacy`: for each corridor of a hydro run, its home site (the buildable
  ground nearest the turbine end, or the turbine end itself where there's none), the
  other buildings within 1/4, 1/2 and 1 mile of it, the nearest one, the nearest public
  road and how much woods stand between them (`hhsf analyze privacy`, see
  hhsf.analysis.privacy, config/privacy.yaml). Saved: it's raster and nearest-neighbor
  work. A parcel re-ingest deletes the rows of its corridors (their home site may have
  moved); `make analyze` computes them again.

`hydro_scores` gains the privacy sub-score (profile hydro_v6 on). `latest_hydro_scores`
gains it and the privacy measures, for the map popup: as 0017 says, the saved layer is
dropped, the live view replaced with the 0016 view as a subquery, and the layer saved
again.

Revision ID: 0018
Revises: 0017
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _migration(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_m0016 = _migration("0016_landcover")
_m0017 = _migration("0017_saved_map_layer")

# The 0016 view (latest_hydro_scores_live since 0017), unchanged.
VIEW_0016 = f"""
    SELECT v.*,
           l.open_acres AS turbine_open_acres,
           l.open_cropland_acres AS turbine_open_cropland_acres,
           l.forest_acres AS turbine_forest_acres,
           l.canopy_pct AS turbine_canopy_pct
    FROM ({_m0016.VIEW_0015}) v
    LEFT JOIN parcel_landcover l ON l.gislink = v.turbine_parcel_gislink
"""

VIEW_0018 = f"""
    SELECT v.*,
           o.privacy_subscore,
           pr.home_site_basis AS privacy_home_site,
           pr.buildings_quarter_mile,
           pr.buildings_half_mile,
           pr.buildings_per_sqmi,
           pr.nearest_building_m,
           pr.parcel_buildings,
           pr.public_road_m AS home_public_road_m,
           pr.road_woods_m
    FROM ({VIEW_0016}) v
    LEFT JOIN hydro_scores o ON o.run_id = v.score_run_id AND o.corridor_id = v.corridor_id
    LEFT JOIN corridor_privacy pr ON pr.corridor_id = v.corridor_id
"""


def _replace_live(view: str) -> None:
    op.execute("DROP MATERIALIZED VIEW latest_hydro_scores")
    op.execute("DROP VIEW latest_hydro_scores_live")
    op.execute(f"CREATE VIEW latest_hydro_scores_live AS {view}")
    for sql in _m0017.CREATE_SAVED:
        op.execute(sql)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE buildings (
            building_id BIGSERIAL PRIMARY KEY,
            quadkey     TEXT NOT NULL,              -- the zoom 9 web-map tile it came in
            area_m2     DOUBLE PRECISION NOT NULL,  -- footprint area (equal-area projection)
            height_m    DOUBLE PRECISION,           -- Microsoft's estimate; NULL: none
            geom        GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
            source_key  TEXT NOT NULL REFERENCES data_sources(source_key),
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX buildings_geom_idx ON buildings USING GIST (geom)")
    op.execute("CREATE INDEX buildings_quadkey_idx ON buildings (quadkey)")
    op.execute(
        """
        CREATE TABLE corridor_privacy (
            corridor_id     BIGINT PRIMARY KEY
                            REFERENCES hydro_corridors(corridor_id) ON DELETE CASCADE,
            aoi_key         TEXT NOT NULL,          -- the AOI run that computed it
            -- The turbine parcel used; deleting the parcel (a re-ingest) deletes the row.
            -- NULL: no turbine parcel, so every building counts as a neighbor.
            gislink         TEXT REFERENCES parcels(gislink) ON DELETE CASCADE,
            home_site_basis TEXT NOT NULL,          -- buildable | turbine_end
            home_site       GEOMETRY(POINT, 4326) NOT NULL,
            -- Other buildings of min_footprint_sqft or more (not on the turbine parcel)
            -- within 1/4, 1/2 and 1 mile of the home site, and per square mile within 1 mile.
            buildings_quarter_mile INTEGER NOT NULL,
            buildings_half_mile    INTEGER NOT NULL,
            buildings_mile         INTEGER NOT NULL,
            buildings_per_sqmi     DOUBLE PRECISION NOT NULL,
            nearest_building_m     DOUBLE PRECISION,  -- NULL: none within search_miles
            parcel_buildings       INTEGER,           -- on the turbine parcel itself
            turbine_buildings_quarter_mile INTEGER NOT NULL,  -- the same, from the turbine end
            public_road_m   DOUBLE PRECISION,       -- home site to the nearest public road
            sightline_m     DOUBLE PRECISION,       -- how much of the line to it was examined
            road_woods_m    DOUBLE PRECISION,       -- of that, under tree canopy; NULL: no data
            min_footprint_sqft DOUBLE PRECISION NOT NULL,  -- settings used: config/privacy.yaml
            woods_canopy_pct   DOUBLE PRECISION NOT NULL,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX corridor_privacy_gislink_idx ON corridor_privacy (gislink)")
    op.execute("ALTER TABLE hydro_scores ADD COLUMN privacy_subscore DOUBLE PRECISION")
    _replace_live(VIEW_0018)


def downgrade() -> None:
    _replace_live(VIEW_0016)
    op.execute("ALTER TABLE hydro_scores DROP COLUMN privacy_subscore")
    op.execute("DROP TABLE corridor_privacy")
    op.execute("DROP TABLE buildings")
