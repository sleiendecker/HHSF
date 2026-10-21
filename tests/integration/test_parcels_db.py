"""Parcels, their owners and the corridor links in a throwaway PostGIS."""

import csv
import json

import pytest
from shapely.geometry import box
from sqlalchemy import text

from hhsf.analysis import export_run, scoring_run
from hhsf.ingest import parcels
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_scoring_db import PROFILE, hydro_run  # noqa: F401  (fixture)
from tests.unit.test_parcels import feature

pytestmark = pytest.mark.integration

# Two parcels side by side over the fixture streams, split at -85.4.
WEST = feature("099001    00100", box(-85.5, 35.3, -85.4, 35.5), CMAP="001", PARCEL="001.00")
EAST = feature(
    "099001    00200", box(-85.4, 35.3, -85.3, 35.5), CMAP="001", PARCEL="002.00", OWNER="DOE ANN"
)


@pytest.fixture
def loaded(engine):  # noqa: F811
    records = [parcels.parse_feature(f, AREA.county_geoid) for f in (WEST, EAST)]
    with engine.begin() as conn:
        parcels.load_parcels(conn, AREA, records)
    yield records
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE parcels CASCADE"))


def test_reload_replaces_the_countys_parcels(engine, loaded):  # noqa: F811
    with engine.begin() as conn:
        parcels.load_parcels(conn, AREA, loaded[:1])
        counts = conn.execute(
            text("SELECT (SELECT count(*) FROM parcels), (SELECT count(*) FROM parcel_owners)")
        ).one()
    assert tuple(counts) == (1, 1)


def test_every_corridor_gets_its_turbine_parcel(engine, hydro_run, loaded):  # noqa: F811
    scoring_run.score_hydro(engine, AREA, PROFILE)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT turbine_parcel_id, turbine_parcel_acres, turbine_parcel_distance_m,"
                " parcels_crossed FROM latest_hydro_scores"
            )
        ).all()
        crossed = conn.execute(text("SELECT count(*) FROM corridor_parcels")).scalar_one()
    assert len(rows) == 2
    for parcel_id, acres, distance, n in rows:
        assert parcel_id in ("001 001.00", "001 002.00")
        assert acres > 1000
        assert distance == 0
        assert n >= 1
    assert crossed >= 2


def test_turbine_in_a_gap_takes_the_nearest_parcel(engine, hydro_run, loaded):  # noqa: F811
    with engine.begin() as conn:
        # Replace the parcels with one small one about 50 m east of a corridor's outlet.
        conn.execute(text("DELETE FROM parcels"))
        corridor, x, y = conn.execute(
            text(
                "SELECT corridor_id, ST_X(pt), ST_Y(pt) FROM ("
                " SELECT corridor_id, ST_EndPoint(ST_GeometryN(geom, ST_NumGeometries(geom))) pt"
                " FROM hydro_corridors ORDER BY corridor_id LIMIT 1) o"
            )
        ).one()
        near = feature("099001    00300", box(x + 0.00055, y - 0.0005, x + 0.0015, y + 0.0005))
        parcels.load_parcels(conn, AREA, [parcels.parse_feature(near, AREA.county_geoid)])
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT corridor_id, distance_m FROM corridor_turbine_parcels")
        ).all()
    assert [c for c, _ in found] == [corridor]
    assert 40 < found[0].distance_m < 60


def test_export_has_owners_in_csv_only(engine, hydro_run, loaded, tmp_path):  # noqa: F811
    scoring_run.score_hydro(engine, AREA, PROFILE)
    info, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)

    assert info.parcels_version and "downloaded" in info.parcels_version
    with (out_dir / "all_corridors.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert {r["turbine_parcel_owner"] for r in rows} <= {"SMITH JOHN", "DOE ANN"}
    assert all(r["turbine_parcel_id"] for r in rows)
    assert all(r["stretch_parcel_ids"] for r in rows)
    geojson = (out_dir / "shortlist.geojson").read_text()
    assert "SMITH" not in geojson and "DOE" not in geojson
    for f in json.loads(geojson)["features"]:
        assert "turbine_parcel_owner" not in f["properties"]
