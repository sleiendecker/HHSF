"""Whether a homestead piece could be split off each turbine parcel (PART 22, FR-9).

- `corridor_subdivision`: for each corridor of a hydro run with a turbine parcel, the
  parcel's public road frontage, the share of the stretch on it, the house site and a
  driveway to the road, whether a piece holding them could be cut from the parcel alone,
  its acres, and the 0-100 Subdivision Feasibility Score (`hhsf analyze subdivision`, see
  hhsf.analysis.subdivision, config/subdivision.yaml). A parcel re-ingest deletes the rows
  of its corridors; `make analyze` computes them again.

Not part of the overall score: FR-7 doesn't list it. `latest_hydro_scores` gains the score
and the measures for the map popup: as 0017 says, the saved layer is dropped, the live
view replaced with the 0018 view as a subquery, and the layer saved again.

Revision ID: 0019
Revises: 0018
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def _migration(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_m0018 = _migration("0018_privacy")

VIEW_0019 = f"""
    SELECT v.*,
           sd.subdivision_score,
           sd.homestead_cut,
           sd.frontage_m AS road_frontage_m,
           sd.stretch_share AS stretch_on_parcel_share,
           sd.driveway_m AS cut_driveway_m,
           sd.piece_acres AS cut_piece_acres,
           array_to_string(sd.flags, ',') AS subdivision_flags
    FROM ({_m0018.VIEW_0018}) v
    LEFT JOIN corridor_subdivision sd ON sd.corridor_id = v.corridor_id
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE corridor_subdivision (
            corridor_id     BIGINT PRIMARY KEY
                            REFERENCES hydro_corridors(corridor_id) ON DELETE CASCADE,
            aoi_key         TEXT NOT NULL,          -- the AOI run that computed it
            -- The turbine parcel; deleting it (a re-ingest) deletes the row.
            gislink         TEXT NOT NULL REFERENCES parcels(gislink) ON DELETE CASCADE,
            frontage_m      DOUBLE PRECISION,       -- public road on or along it; NULL: no roads
            stretch_share   DOUBLE PRECISION NOT NULL,  -- of the stretch on the parcel, 0-1
            home_site_basis TEXT NOT NULL,          -- buildable | turbine_end
            home_site       GEOMETRY(POINT, 4326) NOT NULL,
            road_point      GEOMETRY(POINT, 4326),  -- where the driveway meets the road
            driveway_m      DOUBLE PRECISION,       -- straight, house site to road; NULL: none
            wire_m          DOUBLE PRECISION NOT NULL,  -- straight, turbine end to house site
            -- The turbine end, house site and frontage are on one part of the parcel.
            access_on_parcel BOOLEAN,
            piece           GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,  -- strips and house lot
            piece_acres     DOUBLE PRECISION NOT NULL,  -- rounded up to min_piece_acres
            homestead_cut   BOOLEAN,                -- NULL: no roads, or buildable not computed
            subdivision_score DOUBLE PRECISION,     -- 0-100, FR-9
            flags           TEXT[] NOT NULL,        -- hhsf.analysis.subdivision flags
            min_frontage_ft DOUBLE PRECISION NOT NULL,  -- settings used: config/subdivision.yaml
            min_stretch_share DOUBLE PRECISION NOT NULL,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX corridor_subdivision_gislink_idx ON corridor_subdivision (gislink)")
    op.execute(
        "CREATE INDEX corridor_subdivision_piece_idx ON corridor_subdivision USING GIST (piece)"
    )
    _m0018._replace_live(VIEW_0019)


def downgrade() -> None:
    _m0018._replace_live(_m0018.VIEW_0018)
    op.execute("DROP TABLE corridor_subdivision")
