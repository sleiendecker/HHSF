"""The FR-7 overall score (PART 19).

`hydro_scores` gains the overall score and its buildability, agriculture and logistics
sub-scores (0-100, see hhsf.analysis.scoring). Only score runs with a profile that has
an `overall` section (hydro_v4 on) fill them, and only for ranked corridors; those
runs rank each tier by the overall score. How each was reached is in `explanation`
under "overall".

`latest_hydro_scores` gains the four, for the map popup.

Revision ID: 0015
Revises: 0014
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_spec = importlib.util.spec_from_file_location(
    "migration_0014", Path(__file__).with_name("0014_farmland.py")
)
_m0014 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m0014)

VIEW_0014 = f"""
    SELECT v.*,
           f.prime_acres AS turbine_prime_acres,
           f.statewide_acres AS turbine_statewide_acres,
           f.capable_acres AS turbine_capable_acres,
           f.max_capability_class AS turbine_max_capability_class,
           f.pasture_aum AS turbine_pasture_aum
    FROM ({_m0014.VIEW_0013}) v
    LEFT JOIN parcel_farmland f ON f.gislink = v.turbine_parcel_gislink
"""


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE hydro_scores
            ADD COLUMN overall_score         DOUBLE PRECISION,  -- 0-100, FR-7
            ADD COLUMN buildability_subscore DOUBLE PRECISION,  -- 0-100; NULL: no data
            ADD COLUMN agriculture_subscore  DOUBLE PRECISION,
            ADD COLUMN logistics_subscore    DOUBLE PRECISION
        """
    )
    # A primary-key lookup per row, after the tile's bounding box has filtered the rest.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(
        f"""
        CREATE VIEW latest_hydro_scores AS
        SELECT v.*, o.overall_score, o.buildability_subscore, o.agriculture_subscore,
               o.logistics_subscore
        FROM ({VIEW_0014}) v
        LEFT JOIN hydro_scores o ON o.run_id = v.score_run_id AND o.corridor_id = v.corridor_id
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS {VIEW_0014}")
    op.execute(
        "ALTER TABLE hydro_scores DROP COLUMN overall_score, DROP COLUMN buildability_subscore,"
        " DROP COLUMN agriculture_subscore, DROP COLUMN logistics_subscore"
    )
