"""hydro_scores: allow the low_flow status and tier.

D-01/D-09 (decided 2026-10-19): a corridor below the power target on a
low-confidence flow stays on the list, ranked last by power, instead of being
filtered out (scoring profile hydro_v2). It is ranked, so it has a rank.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

STATUSES = "'scored', 'head_only', 'below_power_target', 'below_min_head', 'not_scorable'"


def _replace(status: str, tier: str, ranked: str) -> None:
    op.execute(
        f"""
        ALTER TABLE hydro_scores
            DROP CONSTRAINT hydro_scores_status_check,
            DROP CONSTRAINT hydro_scores_tier_check,
            DROP CONSTRAINT hydro_scores_check,
            ADD CONSTRAINT hydro_scores_status_check CHECK (status IN ({status})),
            ADD CONSTRAINT hydro_scores_tier_check CHECK (tier IN ({tier})),
            ADD CONSTRAINT hydro_scores_check CHECK ((rank IS NULL) = (status NOT IN ({ranked})))
        """
    )


def upgrade() -> None:
    _replace(
        f"{STATUSES}, 'low_flow'",
        "'medium', 'low', 'head_only', 'low_flow'",
        "'scored', 'head_only', 'low_flow'",
    )


def downgrade() -> None:
    op.execute("DELETE FROM hydro_scores WHERE status = 'low_flow'")
    _replace(STATUSES, "'medium', 'low', 'head_only'", "'scored', 'head_only'")
