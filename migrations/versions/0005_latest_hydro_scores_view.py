"""latest_hydro_scores: the newest score run's corridors, for the map.

A view, so it always follows the latest finished `hhsf score hydro` run without
copying anything. Martin publishes it (martin/martin.yaml) so the map can color
corridors by tier. Earlier runs stay in hydro_scores; query them directly.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per corridor of the latest score run of each AOI, filtered ones included
    # (tier and rank are NULL for those), so the map can show them greyed out.
    op.execute(
        """
        CREATE VIEW latest_hydro_scores AS
        SELECT c.corridor_id, c.geom, r.aoi_key, s.run_id AS score_run_id,
               c.run_id AS hydro_run_id, c.nhdplusid, st.stream_name, s.status, s.tier,
               s.rank, s.tier_rank, s.hydro_score, s.head_subscore, s.power_subscore,
               s.net_head_ft, c.firm_power_w, c.firm_flow_cfs, c.flow_method,
               c.flow_confidence, s.explanation->>'summary' AS summary
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
    )


def downgrade() -> None:
    op.execute("DROP VIEW latest_hydro_scores")
