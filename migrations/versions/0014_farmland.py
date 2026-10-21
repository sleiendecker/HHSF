"""Farmland on each corridor's turbine parcel (PART 18, FR-5).

- `soils` gains each map unit's NRCS farmland class (prime farmland and the like), its
  non-irrigated land capability class (1-8, dominant condition, and that class's share)
  and a pasture yield in animal-unit months per acre a year (see hhsf.ingest.soils).
  `hhsf ingest soils` fills them; rows loaded before this migration have them empty
  until the soils are ingested again.
- `parcel_farmland`: for each turbine parcel, its acres of prime farmland, prime
  farmland only if drained or protected from flooding, farmland of statewide
  importance, capability class 1 to `max_capability_class`, poorly drained soils, and
  the pasture its soils could carry (`hhsf analyze farmland`, config/farmland.yaml).
  Saved, not computed when read: overlaying soils on every turbine parcel when a map
  tile is drawn added half again to a zoom 9 tile. A parcel re-ingest deletes its row;
  `make analyze` computes it again.
- `corridor_turbine_farmland`: each corridor's turbine parcel's row. No row: no turbine
  parcel, or its farmland hasn't been computed.

`latest_hydro_scores` gains the turbine parcel's prime, statewide and capability class
acres and its pasture, for the map popup.

Revision ID: 0014
Revises: 0013
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# The 0013 view is built on 0012's; take that from 0013 rather than copy it a fifth time.
_spec = importlib.util.spec_from_file_location(
    "migration_0013", Path(__file__).with_name("0013_soils_buildable.py")
)
_m0013 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m0013)

VIEW_0013 = f"""
    SELECT v.*,
           b.buildable_acres AS turbine_buildable_acres,
           b.gentle_acres AS turbine_gentle_acres,
           ST_Distance(
               b.geom::geography,
               ST_EndPoint(ST_GeometryN(v.geom, ST_NumGeometries(v.geom)))::geography
           ) AS nearest_buildable_m
    FROM ({_m0013.VIEW_0012_GISLINK}) v
    LEFT JOIN parcel_buildable b ON b.gislink = v.turbine_parcel_gislink
"""


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE soils
            ADD COLUMN farmland_class   TEXT,              -- 'All areas are prime farmland', ...
            ADD COLUMN capability_class SMALLINT,          -- non-irrigated, 1 (best) to 8
            ADD COLUMN capability_pct   DOUBLE PRECISION,  -- share of the map unit in that class
            ADD COLUMN pasture_aum      DOUBLE PRECISION   -- AUM per acre a year; NULL: none
        """
    )

    op.execute(
        """
        CREATE TABLE parcel_farmland (
            gislink              TEXT PRIMARY KEY REFERENCES parcels(gislink) ON DELETE CASCADE,
            aoi_key              TEXT NOT NULL,             -- the AOI run that computed it
            parcel_acres         DOUBLE PRECISION NOT NULL, -- the whole parcel
            soil_acres           DOUBLE PRECISION NOT NULL, -- of it, with soils loaded
            prime_acres          DOUBLE PRECISION NOT NULL, -- 'All areas are prime farmland'
            prime_if_acres       DOUBLE PRECISION NOT NULL, -- prime only if drained, protected...
            statewide_acres      DOUBLE PRECISION NOT NULL, -- farmland of statewide importance
            capable_acres        DOUBLE PRECISION NOT NULL, -- capability class 1 to the max
            wet_acres            DOUBLE PRECISION NOT NULL, -- somewhat poorly drained or wetter
            pasture_aum          DOUBLE PRECISION NOT NULL, -- AUM a year, over the whole parcel
            max_capability_class SMALLINT NOT NULL,         -- settings: config/farmland.yaml
            wet_drainage         TEXT[] NOT NULL,
            computed_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE VIEW corridor_turbine_farmland AS
        SELECT tp.corridor_id, f.gislink, f.parcel_acres, f.soil_acres, f.prime_acres,
               f.prime_if_acres, f.statewide_acres, f.capable_acres, f.wet_acres,
               f.pasture_aum, f.max_capability_class, f.computed_at
        FROM corridor_turbine_parcels tp
        JOIN parcel_farmland f ON f.gislink = tp.gislink
        """
    )

    # As in 0010-0013: the old view becomes a subquery, so a map tile's bounding box
    # filters it first.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(
        f"""
        CREATE VIEW latest_hydro_scores AS
        SELECT v.*,
               f.prime_acres AS turbine_prime_acres,
               f.statewide_acres AS turbine_statewide_acres,
               f.capable_acres AS turbine_capable_acres,
               f.max_capability_class AS turbine_max_capability_class,
               f.pasture_aum AS turbine_pasture_aum
        FROM ({VIEW_0013}) v
        LEFT JOIN parcel_farmland f ON f.gislink = v.turbine_parcel_gislink
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS {VIEW_0013}")
    op.execute("DROP VIEW corridor_turbine_farmland")
    op.execute("DROP TABLE parcel_farmland")
    op.execute(
        "ALTER TABLE soils DROP COLUMN farmland_class, DROP COLUMN capability_class,"
        " DROP COLUMN capability_pct, DROP COLUMN pasture_aum"
    )
