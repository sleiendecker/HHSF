"""Hydro scores, run-scoped.

Each `hhsf score hydro` writes a new analysis_runs row (analysis = 'hydro_score')
that records the scoring profile version and the hydro run it scored, then one
hydro_scores row per corridor of that run, filtered ones included so the list can
say why a corridor isn't on it. Re-scoring never changes an earlier score run
(site-selection.md A5, A6; docs/SCORING.md).

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The run a derived run was computed from (a score run -> its hydro run).
    op.execute(
        "ALTER TABLE analysis_runs ADD COLUMN source_run_id BIGINT"
        " REFERENCES analysis_runs(run_id) ON DELETE CASCADE"
    )
    op.execute(
        """
        CREATE TABLE hydro_scores (
            run_id          BIGINT NOT NULL
                REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
            corridor_id     BIGINT NOT NULL
                REFERENCES hydro_corridors(corridor_id) ON DELETE CASCADE,
            status          TEXT NOT NULL CHECK (status IN (
                'scored', 'head_only', 'below_power_target', 'below_min_head', 'not_scorable'
            )),
            tier            TEXT CHECK (tier IN ('medium', 'low', 'head_only')),
            net_head_ft     DOUBLE PRECISION,
            head_subscore   DOUBLE PRECISION,           -- 0-100
            power_subscore  DOUBLE PRECISION,           -- 0-100, NULL for head-only
            hydro_score     DOUBLE PRECISION,           -- 0-100, only when status = 'scored'
            rank            INTEGER,                    -- 1 = best; NULL when filtered out
            tier_rank       INTEGER,                    -- 1 = best within its tier
            explanation     JSONB NOT NULL,             -- per-factor inputs, breakpoints, why
            PRIMARY KEY (run_id, corridor_id),
            CHECK ((rank IS NULL) = (status NOT IN ('scored', 'head_only')))
        )
        """
    )
    op.execute("CREATE INDEX hydro_scores_rank_idx ON hydro_scores (run_id, rank)")


def downgrade() -> None:
    op.execute("DROP TABLE hydro_scores")
    op.execute("ALTER TABLE analysis_runs DROP COLUMN source_run_id")
