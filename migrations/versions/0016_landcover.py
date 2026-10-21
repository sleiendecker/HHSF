"""Land cover on each corridor's turbine parcel (PART 20).

- `parcel_landcover`: for each turbine parcel, its acres of open land (grassland,
  pasture/hay, crops), forest and developed land from USGS NLCD 2021, its open land on
  prime farmland soils and on cropland soils, and its mean tree canopy cover
  (`hhsf analyze landcover`, see hhsf.analysis.landcover). Saved: it's raster work. A
  parcel re-ingest deletes its row; `make analyze` computes it again.
- `corridor_turbine_landcover`: each corridor's turbine parcel's row. No row: no
  turbine parcel, or its land cover hasn't been computed.

`latest_hydro_scores` gains the turbine parcel's open, open cropland and forest acres
and canopy cover, for the map popup.

Revision ID: 0016
Revises: 0015
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_spec = importlib.util.spec_from_file_location(
    "migration_0015", Path(__file__).with_name("0015_overall_score.py")
)
_m0015 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m0015)

VIEW_0015 = f"""
    SELECT v.*, o.overall_score, o.buildability_subscore, o.agriculture_subscore,
           o.logistics_subscore
    FROM ({_m0015.VIEW_0014}) v
    LEFT JOIN hydro_scores o ON o.run_id = v.score_run_id AND o.corridor_id = v.corridor_id
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE parcel_landcover (
            gislink             TEXT PRIMARY KEY REFERENCES parcels(gislink) ON DELETE CASCADE,
            aoi_key             TEXT NOT NULL,             -- the AOI run that computed it
            parcel_acres        DOUBLE PRECISION NOT NULL, -- 30 m cells whose center is in it
            open_acres          DOUBLE PRECISION NOT NULL, -- grassland, pasture/hay, crops
            pasture_hay_acres   DOUBLE PRECISION NOT NULL,
            crop_acres          DOUBLE PRECISION NOT NULL,
            forest_acres        DOUBLE PRECISION NOT NULL,
            developed_acres     DOUBLE PRECISION NOT NULL,
            open_prime_acres    DOUBLE PRECISION NOT NULL, -- open land on prime farmland soils
            open_cropland_acres DOUBLE PRECISION NOT NULL, -- open land on capability 1-3 soils
            canopy_pct          DOUBLE PRECISION,          -- mean tree canopy cover; NULL: no data
            nlcd_year           SMALLINT NOT NULL,         -- 2021
            computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE VIEW corridor_turbine_landcover AS
        SELECT tp.corridor_id, l.*
        FROM corridor_turbine_parcels tp
        JOIN parcel_landcover l ON l.gislink = tp.gislink
        """
    )
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(
        f"""
        CREATE VIEW latest_hydro_scores AS
        SELECT v.*,
               l.open_acres AS turbine_open_acres,
               l.open_cropland_acres AS turbine_open_cropland_acres,
               l.forest_acres AS turbine_forest_acres,
               l.canopy_pct AS turbine_canopy_pct
        FROM ({VIEW_0015}) v
        LEFT JOIN parcel_landcover l ON l.gislink = v.turbine_parcel_gislink
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS {VIEW_0015}")
    op.execute("DROP VIEW corridor_turbine_landcover")
    op.execute("DROP TABLE parcel_landcover")
