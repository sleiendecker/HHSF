"""latest_hydro_scores: several counties on one map.

Each AOI is one county, so `counties` now says which AOI loaded it (`aoi_key`,
`aoi_name`, filled by `hhsf ingest boundary`). The view uses that to:

- name the county a corridor is ranked in (`aoi_name`): every AOI has its own #1;
- keep one row per reach. A reach within an AOI's buffer of a county line is
  analyzed for both counties; the map shows the run of the county that holds the
  middle of the corridor, else the newest run.

Counties loaded before this migration have no AOI until the boundary ingest runs
again; their corridors then show no county name and border reaches fall back to
the newest run.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# The 0007 view's columns, unchanged. CREATE OR REPLACE VIEW may only add columns at the end.
COLUMNS_0007 = """
    c.corridor_id, c.geom, r.aoi_key, s.run_id AS score_run_id,
    c.run_id AS hydro_run_id, c.nhdplusid, st.stream_name, s.status, s.tier,
    s.rank, s.tier_rank, s.hydro_score, s.head_subscore, s.power_subscore,
    s.net_head_ft, c.firm_power_w, c.firm_flow_cfs, c.flow_method,
    c.flow_confidence, s.explanation->>'summary' AS summary,
    array_to_string(c.flow_flags, ',') AS flow_flags,
    c.flow_reference_gauge,
    (s.explanation->'factors')::text AS factors,
    c.head_method,
    c.length_m AS corridor_length_m,
    st.length_m AS reach_length_m,
    st.max_elev_m - st.min_elev_m AS reach_drop_m
"""

JOINS = """
    FROM hydro_scores s
    JOIN analysis_runs r ON r.run_id = s.run_id
    JOIN hydro_corridors c ON c.corridor_id = s.corridor_id
    JOIN streams st ON st.stream_id = c.stream_id
"""

LATEST_RUNS = """
    WHERE s.run_id IN (
        SELECT max(run_id) FROM analysis_runs
        WHERE analysis = 'hydro_score' AND status = 'finished'
        GROUP BY aoi_key
    )
"""
FROM_LATEST_RUN = JOINS + LATEST_RUNS


def upgrade() -> None:
    op.execute("ALTER TABLE counties ADD COLUMN aoi_key TEXT UNIQUE, ADD COLUMN aoi_name TEXT")
    op.execute(
        f"""
        CREATE OR REPLACE VIEW latest_hydro_scores AS
        SELECT DISTINCT ON (c.nhdplusid) {COLUMNS_0007},
               co.aoi_name
        {JOINS}
        LEFT JOIN counties co ON co.aoi_key = r.aoi_key
        {LATEST_RUNS}
        ORDER BY c.nhdplusid,
                 ST_Intersects(co.geom, ST_PointOnSurface(c.geom)) DESC NULLS LAST,
                 s.run_id DESC
        """
    )


def downgrade() -> None:
    # A view can't drop columns in place, so rebuild the 0007 one.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS SELECT {COLUMNS_0007} {FROM_LATEST_RUN}")
    op.execute("ALTER TABLE counties DROP COLUMN aoi_key, DROP COLUMN aoi_name")
