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
