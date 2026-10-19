"""Analysis runs and hydro corridors.

Results are run-scoped: each `hhsf analyze` writes a new analysis_runs row and its
own result rows, so earlier runs stay reproducible. Every computed metric carries
a method and a confidence, and a value that can't be estimated reliably is stored
as NULL plus a flag (site-selection.md B6, B7, B8, B9).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE analysis_runs (
            run_id                  BIGSERIAL PRIMARY KEY,
            analysis                TEXT NOT NULL,            -- e.g. 'hydro'
            aoi_key                 TEXT NOT NULL,            -- e.g. 'sequatchie_tn'
            code_version            TEXT NOT NULL,            -- git describe
            scoring_profile_version TEXT,                     -- NULL until scoring exists
            data_source_versions    JSONB NOT NULL,           -- {source_key: version}
            params                  JSONB NOT NULL,           -- every assumption used
            summary                 JSONB,                    -- counts, written at the end
            started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at             TIMESTAMPTZ,
            status                  TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'finished', 'failed'))
        )
        """
    )

    # One corridor per perennial reach for now. from/to measures (percent along the
    # reach, NHDPlus convention: 0 = downstream end) let a later version store just
    # the steepest stretch of a reach without a schema change.
    op.execute(
        """
        CREATE TABLE hydro_corridors (
            corridor_id               BIGSERIAL PRIMARY KEY,
            run_id                    BIGINT NOT NULL
                REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
            stream_id                 BIGINT NOT NULL
                REFERENCES streams(stream_id) ON DELETE CASCADE,
            nhdplusid                 BIGINT NOT NULL,
            from_measure              DOUBLE PRECISION NOT NULL DEFAULT 0,
            to_measure                DOUBLE PRECISION NOT NULL DEFAULT 100,
            geom                      GEOMETRY(MULTILINESTRING, 4326) NOT NULL,
            length_m                  DOUBLE PRECISION NOT NULL,  -- computed in EPSG:5070

            gross_head_m              DOUBLE PRECISION,           -- max - min smoothed elevation
            net_head_m                DOUBLE PRECISION,           -- gross x head factor (D-07)
            head_method               TEXT NOT NULL,
            head_flag                 TEXT,                       -- why head is NULL, if it is

            drainage_area_sqkm        DOUBLE PRECISION,           -- NHDPlus totdasqkm
            drainage_area_method      TEXT NOT NULL,

            mean_flow_cfs             DOUBLE PRECISION,           -- NHDPlus EROM, for reference
            firm_flow_cfs             DOUBLE PRECISION,           -- NULL when not estimable
            flow_statistic            TEXT,                       -- 'q90' or 'mean_annual'
            flow_method               TEXT,        -- 'streamstats_q90' or 'nhdplus_erom'
            flow_confidence           TEXT NOT NULL
                CHECK (flow_confidence IN ('medium', 'low', 'below_applicability', 'unavailable')),
            flow_flags                TEXT[] NOT NULL DEFAULT '{}',
            flow_std_error_pct        DOUBLE PRECISION,           -- regression ASEp
            regression_region         TEXT,                       -- StreamStats region code
            streamstats_drainage_area_sqkm DOUBLE PRECISION,
            flow_reference_gauge      TEXT,                       -- USGS site, gauge transfers
            flow_drainage_area_ratio  DOUBLE PRECISION,           -- reach / gauge area

            usable_flow_cfs           DOUBLE PRECISION,           -- firm flow x diversion (D-05)
            firm_power_w              DOUBLE PRECISION,           -- NULL when flow or head is NULL
            power_confidence          TEXT NOT NULL
                CHECK (power_confidence IN ('medium', 'low', 'unavailable')),

            created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (run_id, nhdplusid)
        )
        """
    )
    op.execute("CREATE INDEX hydro_corridors_geom_idx ON hydro_corridors USING GIST (geom)")
    op.execute("CREATE INDEX hydro_corridors_run_idx ON hydro_corridors (run_id)")


def downgrade() -> None:
    op.execute("DROP TABLE hydro_corridors")
    op.execute("DROP TABLE analysis_runs")
