"""latest_hydro_scores: add what the map popup explains.

Adds the flow flags, the reference gauge and each factor's part in the score
(the `factors` list from hydro_scores.explanation, as JSON text) to the view,
so a click on the map can explain a score without a separate API. Vector tiles
can't carry arrays, so the flags are one comma-separated string.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# The 0005 view's columns, unchanged. CREATE OR REPLACE VIEW may only add columns at the end.
BASE_COLUMNS = """
    c.corridor_id, c.geom, r.aoi_key, s.run_id AS score_run_id,
    c.run_id AS hydro_run_id, c.nhdplusid, st.stream_name, s.status, s.tier,
    s.rank, s.tier_rank, s.hydro_score, s.head_subscore, s.power_subscore,
    s.net_head_ft, c.firm_power_w, c.firm_flow_cfs, c.flow_method,
    c.flow_confidence, s.explanation->>'summary' AS summary
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
        SELECT {BASE_COLUMNS},
               array_to_string(c.flow_flags, ',') AS flow_flags,
               c.flow_reference_gauge,
               (s.explanation->'factors')::text AS factors
        {FROM_LATEST_RUN}
        """
    )


def downgrade() -> None:
    # A view can't drop columns in place, so rebuild the 0005 one.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS SELECT {BASE_COLUMNS} {FROM_LATEST_RUN}")
