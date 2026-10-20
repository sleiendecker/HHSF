"""latest_hydro_scores: add where the corridor sits on its reach.

A corridor is now the steepest stretch of a reach (D-08), so the popup says how
long the stretch is, how long the whole reach is and how much the whole reach
drops, and which head method was used (whole-reach runs still exist). The
geometry column already holds the stretch: nothing changes for the map's lines.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# The 0006 view's columns, unchanged. CREATE OR REPLACE VIEW may only add columns at the end.
COLUMNS_0006 = """
    c.corridor_id, c.geom, r.aoi_key, s.run_id AS score_run_id,
    c.run_id AS hydro_run_id, c.nhdplusid, st.stream_name, s.status, s.tier,
    s.rank, s.tier_rank, s.hydro_score, s.head_subscore, s.power_subscore,
    s.net_head_ft, c.firm_power_w, c.firm_flow_cfs, c.flow_method,
    c.flow_confidence, s.explanation->>'summary' AS summary,
    array_to_string(c.flow_flags, ',') AS flow_flags,
    c.flow_reference_gauge,
    (s.explanation->'factors')::text AS factors
"""

FROM_LATEST_RUN = """
    FROM hydro_scores s
    JOIN analysis_runs r ON r.run_id = s.run_id
    JOIN hydro_corridors c ON c.corridor_id = s.corridor_id
    JOIN streams st ON st.stream_id = c.stream_id
    WHERE s.run_id IN (
        SELECT max(run_id) FROM analysis_runs
        WHERE analysis = 'hydro_score' AND status = 'finished'
        GROUP BY aoi_key
    )
"""


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE VIEW latest_hydro_scores AS
        SELECT {COLUMNS_0006},
               c.head_method,
               c.length_m AS corridor_length_m,
               st.length_m AS reach_length_m,
               st.max_elev_m - st.min_elev_m AS reach_drop_m
        {FROM_LATEST_RUN}
        """
    )


def downgrade() -> None:
    # A view can't drop columns in place, so rebuild the 0006 one.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS SELECT {COLUMNS_0006} {FROM_LATEST_RUN}")
