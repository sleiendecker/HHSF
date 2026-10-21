"""Exports a score run from a throwaway PostGIS."""

import csv
import json

import pytest
from shapely.geometry import box
from sqlalchemy import text

from hhsf.analysis import export_run, run, scoring_run
from hhsf.core.config import AOI
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
            text("SELECT DISTINCT score_run_id, aoi_key FROM latest_hydro_scores_live")
        ).all()
    assert [tuple(r) for r in runs] == [(newest, "fixture")]


def test_saved_map_layer_is_the_live_view_after_a_refresh(engine, hydro_run):  # noqa: F811
    from hhsf.core.db import refresh_map

    def rows(conn, table):
        return conn.execute(
            text(f"SELECT corridor_id, score_run_id, rank FROM {table} ORDER BY corridor_id")
        ).all()

    run_id, _, _ = scoring_run.score_hydro(engine, AREA, PROFILE)
    with engine.begin() as conn:
        live = rows(conn, "latest_hydro_scores_live")
        assert all(r.score_run_id == run_id for r in live)
        assert rows(conn, "latest_hydro_scores") != live  # saved: stale until refreshed
        refresh_map(conn)
        assert rows(conn, "latest_hydro_scores") == live


def test_latest_scores_view_carries_popup_columns(engine, hydro_run):  # noqa: F811
    scoring_run.score_hydro(engine, AREA, PROFILE)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT status, flow_flags, flow_reference_gauge, factors, head_method,"
                " corridor_length_m, reach_length_m, reach_drop_m"
                " FROM latest_hydro_scores_live ORDER BY corridor_id"
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


NEIGHBOR = AOI(key="neighbor", name="Neighbor County", county_geoid="99998", buffer_m=20_000)


def test_latest_scores_view_keeps_one_row_per_reach_across_aois(engine, hydro_run):  # noqa: F811
    # A neighbor county whose (wide) buffer takes in both fixture reaches, scored after
    # the fixture county. The view keeps the run of the county the corridors are in.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE counties SET aoi_key = 'fixture', aoi_name = 'Fixture County'"
                " WHERE geoid = '99999'"
            )
        )
        conn.execute(
            text(
                "INSERT INTO counties"
                " (geoid, state_code, county_name, geom, source_key, aoi_key, aoi_name)"
                " VALUES ('99998', '99', 'Neighbor', ST_Multi(ST_GeomFromText(:wkt, 4326)),"
                " 'test', 'neighbor', 'Neighbor County')"
            ),
            {"wkt": box(-85.7, 35.3, -85.5, 35.5).wkt},
        )
    home, _, _ = scoring_run.score_hydro(engine, AREA, PROFILE)
    run.run_hydro(engine, NEIGHBOR, source=None, echo=lambda _: None)
    neighbor, _, _ = scoring_run.score_hydro(engine, NEIGHBOR, PROFILE)

    def rows():
        with engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT nhdplusid, score_run_id, aoi_key, aoi_name"
                    " FROM latest_hydro_scores_live"
                    " ORDER BY nhdplusid"
                )
            ).all()

    assert [r.nhdplusid for r in rows()] == [25000100000001, 25000100000002]
    assert {(r.score_run_id, r.aoi_name) for r in rows()} == {(home, "Fixture County")}

    # A county loaded before migration 0009 has no AOI: the newest run wins instead.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE counties SET aoi_key = NULL, aoi_name = NULL WHERE geoid = '99999'")
        )
    assert {(r.score_run_id, r.aoi_key) for r in rows()} == {(neighbor, "neighbor")}
