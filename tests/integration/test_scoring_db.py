"""Scores a hydro run against a throwaway PostGIS."""

import pytest
from sqlalchemy import text

from hhsf.analysis import run, scoring, scoring_run
from hhsf.ingest import hydrography
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

PROFILE = scoring.load_profile("hydro_v1")


@pytest.fixture
def hydro_run(engine, sample_features):  # noqa: F811
    records = [hydrography.parse_feature(f) for f in sample_features]
    with engine.begin() as conn:
        hydrography.load_streams(conn, [r for r in records if hydrography.is_perennial(r.fcode)])
    run_id, _ = run.run_hydro(engine, AREA, source=None, echo=lambda _: None)
    return run_id


def test_scores_latest_hydro_run_as_new_run(engine, hydro_run):  # noqa: F811
    score_run, scored_run, summary = scoring_run.score_hydro(engine, AREA, PROFILE)

    assert scored_run == hydro_run
    assert summary["corridors"] == 2
    with engine.connect() as conn:
        meta = conn.execute(
            text(
                "SELECT analysis, status, scoring_profile_version, source_run_id, params"
                " FROM analysis_runs WHERE run_id = :r"
            ),
            {"r": score_run},
        ).one()
        rows = conn.execute(
            text(
                """
                SELECT c.flow_confidence, s.status, s.tier, s.rank, s.hydro_score, s.explanation
                FROM hydro_scores s JOIN hydro_corridors c USING (corridor_id)
                WHERE s.run_id = :r ORDER BY c.nhdplusid
                """
            ),
            {"r": score_run},
        ).all()
    assert (meta.analysis, meta.status) == ("hydro_score", "finished")
    assert meta.scoring_profile_version == "hydro_v1"
    assert meta.source_run_id == hydro_run
    assert meta.params["scoring_profile"]["weights"] == {"power": 0.7, "head": 0.3}
    big, small = rows
    # Big reach: EROM mean flow (low confidence). Small reach: no flow (D-09) and under
    # 10 ft of net head, so it drops instead of being ranked on head.
    assert big.status in (scoring.SCORED, scoring.BELOW_POWER_TARGET)
    if big.status == scoring.SCORED:
        assert big.tier == "low" and big.rank == 1
    assert small.flow_confidence == "below_applicability"
    assert (small.status, small.rank, small.hydro_score) == ("below_min_head", None, None)
    assert "basin too small" in small.explanation["summary"]

    with engine.connect() as conn:
        assert scoring_run.top(conn, score_run, 5)[0].rank == 1


def test_rescoring_keeps_earlier_scores(engine, hydro_run):  # noqa: F811
    first, _, _ = scoring_run.score_hydro(engine, AREA, PROFILE)
    second, _, _ = scoring_run.score_hydro(engine, AREA, PROFILE, hydro_run_id=hydro_run)
    with engine.connect() as conn:
        counts = conn.execute(
            text("SELECT run_id, count(*) FROM hydro_scores GROUP BY run_id ORDER BY 1")
        ).all()
    assert [tuple(c) for c in counts] == [(first, 2), (second, 2)]


def test_refuses_runs_that_are_not_finished_hydro_runs(engine, hydro_run):  # noqa: F811
    score_run, _, _ = scoring_run.score_hydro(engine, AREA, PROFILE)
    with pytest.raises(scoring_run.NoHydroRun):
        scoring_run.score_hydro(engine, AREA, PROFILE, hydro_run_id=score_run)


def test_no_hydro_run_says_what_to_do(engine):  # noqa: F811
    with pytest.raises(scoring_run.NoHydroRun, match="make analyze"):
        scoring_run.score_hydro(engine, AREA, PROFILE)
