"""Runs the hydro analysis against a throwaway PostGIS (StreamStats switched off)."""

import pytest
from shapely.geometry import box
from sqlalchemy import create_engine, text

from hhsf.analysis import run
from hhsf.core.config import AOI
from hhsf.ingest import hydrography

pytestmark = pytest.mark.integration

AREA = AOI(key="fixture", name="Fixture County", county_geoid="99999", buffer_m=100)


@pytest.fixture
def engine(db_url):
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE streams, counties, analysis_runs CASCADE"))
        conn.execute(
            text("INSERT INTO data_sources (source_key) VALUES ('test') ON CONFLICT DO NOTHING")
        )
        conn.execute(
            text(
                "INSERT INTO counties (geoid, state_code, county_name, geom, source_key)"
                " VALUES ('99999', '99', 'Fixture', ST_Multi(ST_GeomFromText(:wkt, 4326)), 'test')"
            ),
            {"wkt": box(-85.5, 35.3, -85.3, 35.5).wkt},  # around the fixture streams
        )
    return eng


def test_run_saves_one_corridor_per_perennial_reach(engine, sample_features):
    records = [hydrography.parse_feature(f) for f in sample_features]
    with engine.begin() as conn:
        hydrography.load_streams(conn, [r for r in records if hydrography.is_perennial(r.fcode)])

    run_id, summary = run.run_hydro(engine, AREA, source=None, echo=lambda _: None)

    assert summary["reaches"] == 2
    with engine.connect() as conn:
        status, params = conn.execute(
            text("SELECT status, params FROM analysis_runs WHERE run_id = :r"), {"r": run_id}
        ).one()
        rows = conn.execute(
            text(
                """
                SELECT nhdplusid, gross_head_m, firm_flow_cfs, flow_method, flow_confidence,
                       flow_flags, firm_power_w, power_confidence, ST_SRID(geom) AS srid
                FROM hydro_corridors WHERE run_id = :r ORDER BY nhdplusid
                """
            ),
            {"r": run_id},
        ).all()
    assert status == "finished"
    assert params["hydro"]["system_efficiency"] == 0.5
    big, small = rows
    # Test Creek: 8.98 km2 (3.5 sq mi), above the regression minimum -> EROM fallback.
    assert big.gross_head_m == pytest.approx(106.01)
    assert big.flow_method == "nhdplus_erom"
    assert big.flow_confidence == "low"
    assert big.flow_flags == ["streamstats_disabled", "mean_flow_not_q90"]
    assert big.firm_power_w > 0 and big.power_confidence == "low"
    assert big.srid == 4326
    # 2.88 km2 (1.1 sq mi) is below it -> NULL flow and power, never a made-up number.
    assert small.firm_flow_cfs is None
    assert small.flow_confidence == "below_applicability"
    assert small.firm_power_w is None


def test_runs_are_kept_side_by_side(engine, sample_features):
    records = [hydrography.parse_feature(f) for f in sample_features[:1]]
    with engine.begin() as conn:
        hydrography.load_streams(conn, records)
    first, _ = run.run_hydro(engine, AREA, source=None, echo=lambda _: None)
    second, _ = run.run_hydro(engine, AREA, source=None, echo=lambda _: None)
    with engine.connect() as conn:
        counts = conn.execute(
            text("SELECT run_id, count(*) FROM hydro_corridors GROUP BY run_id ORDER BY 1")
        ).all()
    assert [tuple(c) for c in counts] == [(first, 1), (second, 1)]


def test_gauge_transfer_is_saved_with_its_gauge(engine, sample_features):
    from hhsf.analysis.gauges import GaugeTransfer
    from hhsf.analysis.hydro import GaugeReference
    from hhsf.core import crs

    records = [hydrography.parse_feature(f) for f in sample_features[:1]]
    with engine.begin() as conn:
        hydrography.load_streams(conn, records)
    basin = crs.to_analysis(box(-85.5, 35.3, -85.3, 35.5))
    gauge = GaugeTransfer(GaugeReference("03571000", 77.2, 402.0), basin, {"site_no": "03571000"})
    run_id, _ = run.run_hydro(engine, AREA, source=None, echo=lambda _: None, gauge=gauge)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT flow_method, flow_reference_gauge, flow_drainage_area_ratio, firm_flow_cfs"
                " FROM hydro_corridors WHERE run_id = :r"
            ),
            {"r": run_id},
        ).one()
        params = conn.execute(
            text("SELECT params FROM analysis_runs WHERE run_id = :r"), {"r": run_id}
        ).scalar_one()
    assert row.flow_method == "gauge_drainage_area_ratio"
    assert row.flow_reference_gauge == "03571000"
    assert row.firm_flow_cfs == pytest.approx(77.2 * row.flow_drainage_area_ratio)
    assert params["reference_gauge"] == {"site_no": "03571000"}


@pytest.fixture
def plane_dem(tmp_path):
    """A 10 m-ish DEM over the fixture streams: 100 m lower per 0.01 degree south."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    cell = 1 / 10800  # 3DEP 1/3 arc-second
    west, north, n = -85.45, 35.45, 1080  # 0.1 degree square
    lat = north - cell * (np.arange(n) + 0.5)
    z = np.repeat((450 + (lat - 35.39) * 10_000)[:, None], n, axis=1).astype("float32")
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=n,
        height=n,
        count=1,
        dtype="float32",
        crs="EPSG:4269",
        transform=from_origin(west, north, cell, cell),
        nodata=-999999,
    ) as f:
        f.write(z, 1)
    return path


def test_dem_run_saves_the_steepest_stretch(engine, sample_features, plane_dem):
    records = [hydrography.parse_feature(f) for f in sample_features]
    with engine.begin() as conn:
        hydrography.load_streams(conn, [r for r in records if hydrography.is_perennial(r.fcode)])

    run_id, summary = run.run_hydro(engine, AREA, source=None, echo=lambda _: None, dem=plane_dem)

    assert summary["head_method"] == {"dem_3dep_steepest_stretch": 2}
    assert summary["stretch_shorter_than_reach"] == 2
    with engine.connect() as conn:
        params = conn.execute(
            text("SELECT params FROM analysis_runs WHERE run_id = :r"), {"r": run_id}
        ).scalar_one()
        rows = conn.execute(
            text(
                """
                SELECT c.nhdplusid, c.head_method, c.gross_head_m, c.from_measure, c.to_measure,
                       c.length_m, ST_Length(c.geom::geography) AS geom_length_m,
                       st.length_m AS reach_length_m,
                       ST_Covers(ST_Buffer(st.geom::geography, 1), c.geom::geography) AS on_reach,
                       ST_Distance(ST_StartPoint(ST_GeometryN(st.geom, 1))::geography,
                                   ST_StartPoint(ST_GeometryN(c.geom, 1))::geography) AS top_m
                FROM hydro_corridors c JOIN streams st USING (stream_id)
                WHERE c.run_id = :r ORDER BY c.nhdplusid
                """
            ),
            {"r": run_id},
        ).all()
    assert params["corridor_method"] == "dem_3dep_steepest_stretch"
    assert params["hydro"]["corridor"]["penstock_length_ft"] == 1000
    big, small = rows
    for r in rows:
        assert r.head_method == "dem_3dep_steepest_stretch"
        assert r.length_m == pytest.approx(304.8, abs=0.01)
        assert r.geom_length_m == pytest.approx(r.length_m, rel=0.01)  # the geometry is the stretch
        assert r.on_reach
        assert 0 <= r.from_measure < r.to_measure <= 100
        assert r.to_measure - r.from_measure == pytest.approx(100 * 304.8 / r.reach_length_m)
    # Test Creek runs straight down the plane (100 m over the reach), so any stretch drops
    # the plane's share, give or take one DEM cell's step (< 1 m).
    assert big.gross_head_m == pytest.approx(100 * 304.8 / big.reach_length_m, abs=1)
    assert big.top_m == pytest.approx(big.reach_length_m * (1 - big.to_measure / 100), rel=0.01)
    # The second reach's NHDPlus ends are only 1.51 m apart: the stretch can't drop more.
    assert small.gross_head_m <= 1.52


def test_run_without_dem_keeps_whole_reaches(engine, sample_features):
    records = [hydrography.parse_feature(f) for f in sample_features[:1]]
    with engine.begin() as conn:
        hydrography.load_streams(conn, records)
    run_id, _ = run.run_hydro(engine, AREA, source=None, echo=lambda _: None)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.head_method, c.from_measure, c.to_measure, c.length_m,"
                " st.length_m AS reach, ST_Equals(c.geom, st.geom) AS same FROM hydro_corridors c"
                " JOIN streams st USING (stream_id) WHERE run_id = :r"
            ),
            {"r": run_id},
        ).one()
    assert row.head_method == "nhdplus_vaa_smoothed_reach"
    assert (row.from_measure, row.to_measure) == (0, 100)
    assert row.length_m == row.reach and row.same
