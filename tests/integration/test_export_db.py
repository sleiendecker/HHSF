"""Exports a score run from a throwaway PostGIS."""

import csv
import json

import pytest
from sqlalchemy import text

from hhsf.analysis import export_run, scoring_run
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_scoring_db import PROFILE, hydro_run  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration


def test_exports_latest_score_run(engine, hydro_run, tmp_path):  # noqa: F811
    scoring_run.score_hydro(engine, AREA, PROFILE)
    score_run, _, _ = scoring_run.score_hydro(engine, AREA, PROFILE)

    info, out_dir, files = export_run.export_hydro(engine, AREA, out_root=tmp_path)

    assert (info.score_run_id, info.hydro_run_id) == (score_run, hydro_run)
    assert info.scoring_profile_version == "hydro_v1"
    assert info.hydro_params["hydro"]["system_efficiency"] == 0.5
    assert info.score_params["scoring_profile"]["power_target_w"] == 1000
    assert out_dir == tmp_path / "fixture" / f"run_{score_run}"
    assert {p.name for p in files} == {
        "shortlist.csv",
        "shortlist.geojson",
        "all_corridors.csv",
        "report.md",
    }

    with (out_dir / "all_corridors.csv").open() as f:
        everything = list(csv.DictReader(f))
    assert len(everything) == 2
    small = next(r for r in everything if r["status"] == "below_min_head")
    assert small["rank"] == ""
    assert "basin too small" in small["explanation"]
    assert small["drainage_area_sqmi"] != ""
    # The outlet is the downstream end of the reach, in lat/lon.
    assert 35.3 < float(small["outlet_lat"]) < 35.5
    assert -85.5 < float(small["outlet_lon"]) < -85.3

    with (out_dir / "shortlist.csv").open() as f:
        ranked = list(csv.DictReader(f))
    features = json.loads((out_dir / "shortlist.geojson").read_text())["features"]
    assert len(features) == len(ranked) <= 1
    for row, feature in zip(ranked, features, strict=True):
        assert row["rank"] == "1"
        assert feature["geometry"]["type"] == "MultiLineString"
        assert feature["properties"]["nhdplusid"] == int(row["nhdplusid"])

    report = (out_dir / "report.md").read_text()
    assert f"| Score run | {score_run} " in report
    assert f"| Hydro run it scored | {hydro_run} |" in report
    assert "| **Total** | **2** |" in report


def test_exports_a_chosen_run(engine, hydro_run, tmp_path):  # noqa: F811
    first, _, _ = scoring_run.score_hydro(engine, AREA, PROFILE)
    scoring_run.score_hydro(engine, AREA, PROFILE)
    info, out_dir, _ = export_run.export_hydro(engine, AREA, first, out_root=tmp_path)
    assert info.score_run_id == first
    assert out_dir.name == f"run_{first}"


def test_refuses_runs_that_are_not_score_runs(engine, hydro_run, tmp_path):  # noqa: F811
    with pytest.raises(export_run.NoScoreRun, match="make score"):
        export_run.export_hydro(engine, AREA, out_root=tmp_path)
    with pytest.raises(export_run.NoScoreRun, match="not a finished score run"):
        export_run.export_hydro(engine, AREA, hydro_run, out_root=tmp_path)


def test_latest_scores_view_follows_newest_run(engine, hydro_run):  # noqa: F811
    scoring_run.score_hydro(engine, AREA, PROFILE)
    newest, _, _ = scoring_run.score_hydro(engine, AREA, PROFILE)
    with engine.connect() as conn:
        runs = conn.execute(
            text("SELECT DISTINCT score_run_id, aoi_key FROM latest_hydro_scores")
        ).all()
    assert [tuple(r) for r in runs] == [(newest, "fixture")]


def test_latest_scores_view_carries_popup_columns(engine, hydro_run):  # noqa: F811
    scoring_run.score_hydro(engine, AREA, PROFILE)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT status, flow_flags, flow_reference_gauge, factors, head_method,"
                " corridor_length_m, reach_length_m, reach_drop_m"
                " FROM latest_hydro_scores ORDER BY corridor_id"
            )
        ).all()
    # Flags are one comma-separated string, since vector tiles can't carry arrays.
    assert all(isinstance(r.flow_flags, str) for r in rows)
    keys = {"factor", "value", "subscore", "weight", "contribution"}
    for r in rows:
        factors = json.loads(r.factors)
        assert all(keys <= f.keys() for f in factors)
        assert bool(factors) == (r.status in ("scored", "head_only"))
        # Where the corridor sits on its reach (migration 0007); whole reaches in this run.
        assert r.head_method == "nhdplus_vaa_smoothed_reach"
        assert r.corridor_length_m == r.reach_length_m > 0
        assert r.reach_drop_m >= 0
