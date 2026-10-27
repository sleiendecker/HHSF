"""Soils, buildable ground and its corridor link in a throwaway PostGIS."""

import csv

import numpy as np
import pytest
import rasterio
import shapely
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, box
from sqlalchemy import text

from hhsf.analysis import buildable, export_run, scoring_run
from hhsf.core import units
from hhsf.ingest import flood, parcels, soils
from tests.integration.test_flood_db import outlet
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_scoring_db import PROFILE, hydro_run  # noqa: F401  (fixture)
from tests.unit.test_flood import feature as flood_feature
from tests.unit.test_parcels import feature as parcel_feature

pytestmark = pytest.mark.integration

SETTINGS = buildable.Settings(
    max_slope_pct=15,
    septic_rating="septic_tn",
    septic_excluded=["Very limited"],
    min_patch_acres=0.5,
)
HALF = 0.004  # the parcel reaches this many degrees each way from the turbine end
CELL_DEG = 1 / 10800  # 1/3 arc-second, as 3DEP


def soil_unit(mukey, rating):
    return soils.MapUnit(
        mukey=mukey,
        areasymbol="TN999",
        musym=mukey,
        muname=f"Fixture soil {mukey}",
        hydric_pct=0,
        bedrock_min_cm=None,
        water_table_min_cm=None,
        flooding="None",
        drainage="Well drained",
        ratings={"septic_tn": (rating, 85.0), "septic_national": ("Very limited", 85.0)},
    )


def write_dem(path, x, y):
    """Flat (100 m) west of the turbine end, a 50% slope rising east of it (NAD83)."""
    x0, y1 = x - 2 * HALF, y + 2 * HALF
    n = round(4 * HALF / CELL_DEG)
    lon = x0 + (np.arange(n) + 0.5) * CELL_DEG
    m_east = np.maximum(lon - x, 0) * 111_320 * np.cos(np.radians(y))
    elev = np.tile(100 + 0.5 * m_east, (n, 1)).astype("float32")
    with rasterio.open(
        path, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32",
        crs="EPSG:4269", transform=from_origin(x0, y1, CELL_DEG, CELL_DEG), nodata=-999999,
    ) as dem:  # fmt: skip
        dem.write(elev, 1)


@pytest.fixture
def site(engine, hydro_run, tmp_path):  # noqa: F811
    """A parcel around the first corridor's turbine end: flat west, steep east, a floodplain
    strip on its west edge, septic-suitable soils north of the turbine end only."""
    corridor, x, y = outlet(engine)
    lot = box(x - HALF, y - HALF, x + HALF, y + HALF)
    with engine.begin() as conn:
        parcels.load_parcels(
            conn, AREA, [parcels.parse_feature(parcel_feature("099001    00300", lot), "99999")]
        )
        strip = box(x - HALF, y - HALF, x - HALF / 2, y + HALF)
        flood.load_flood_zones(
            conn, AREA, [flood.parse_feature(flood_feature("99999C_9", "AE", geom=strip), "99999")]
        )
        # Densified: a long straight edge in degrees bends when projected to meters.
        north = shapely.segmentize(box(x - 0.01, y, x + 0.01, y + 0.01), 0.0005)
        south = shapely.segmentize(box(x - 0.01, y - 0.01, x + 0.01, y), 0.0005)
        soils.load_soils(
            conn,
            AREA,
            [soil_unit("1", "Slightly limited"), soil_unit("2", "Very limited")],
            [
                soils.SoilPolygon(1, "1", "TN999", MultiPolygon([north])),
                soils.SoilPolygon(2, "2", "TN999", MultiPolygon([south])),
            ],
            {"TN999": "TN999 version 1 (1/1/2026)"},
        )
    dem = tmp_path / "dem.tif"
    write_dem(dem, x, y)
    yield corridor, x, y, dem
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE parcels, flood_zones, soils CASCADE"))


def run(engine, dem):  # noqa: F811
    with engine.begin() as conn:
        targets = buildable.turbine_parcels(conn, buildable.latest_hydro_run(conn, AREA.key))
        return buildable.run_buildable(conn, AREA, targets, dem, SETTINGS, echo=lambda _: None)


def test_parcels_are_grouped_by_their_own_state(engine, site):  # noqa: F811
    with engine.connect() as conn:
        targets = buildable.turbine_parcels(conn, buildable.latest_hydro_run(conn, AREA.key))
        groups = buildable.by_state(conn, targets)
    assert list(groups) == ["99"] and groups["99"] == targets


def test_soils_keep_their_ratings_and_source(engine, site):  # noqa: F811
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT mukey, septic_tn, septic_tn_pct, county_geoid, survey_version"
                " FROM soils ORDER BY 1"
            )
        ).all()
        version = conn.execute(
            text("SELECT version FROM data_sources WHERE source_key = 'usda_ssurgo'")
        ).scalar()
    assert [tuple(r) for r in rows] == [
        ("1", "Slightly limited", 85.0, "99999", "TN999 version 1 (1/1/2026)"),
        ("2", "Very limited", 85.0, "99999", "TN999 version 1 (1/1/2026)"),
    ]
    assert version.endswith(": TN999 version 1 (1/1/2026)")


def test_buildable_ground_is_flat_dry_and_septic_suitable(engine, site):  # noqa: F811
    corridor, x, y, dem = site
    [result] = run(engine, dem)
    # Buildable: between the floodplain strip and the turbine end (west of it is flat),
    # north of it (soils), i.e. HALF/2 by HALF degrees.
    width_m = HALF / 2 * 111_320 * np.cos(np.radians(y))
    expected = units.sqkm_to_acres(width_m * HALF * 111_000 / 1e6)
    assert result.parcel_acres == pytest.approx(
        units.sqkm_to_acres(4 * width_m * 2 * HALF * 111_000 / 1e6), rel=0.03
    )
    assert result.gentle_acres == pytest.approx(result.parcel_acres / 2, rel=0.05)
    assert result.gentle_dry_acres == pytest.approx(result.gentle_acres / 2, rel=0.05)
    assert result.buildable_acres == pytest.approx(expected, rel=0.1)
    assert result.patches == 1

    with engine.connect() as conn:
        link = conn.execute(
            text("SELECT * FROM corridor_turbine_buildable WHERE corridor_id = :c"),
            {"c": corridor},
        ).one()
        saved = conn.execute(
            text("SELECT septic_rating, septic_excluded, ST_IsValid(geom) FROM parcel_buildable")
        ).one()
    assert link.buildable_acres == pytest.approx(result.buildable_acres)
    assert link.nearest_buildable_m < 30  # the turbine end is at the patch's corner
    assert tuple(saved) == ("septic_tn", ["Very limited"], True)


def test_rerun_replaces_and_a_parcel_reload_deletes(engine, site):  # noqa: F811
    _, _, _, dem = site
    run(engine, dem)
    run(engine, dem)
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM parcel_buildable")).scalar() == 1
        conn.execute(text("DELETE FROM parcels"))
        assert conn.execute(text("SELECT count(*) FROM parcel_buildable")).scalar() == 0


def test_map_view_and_export_have_buildable_ground(engine, site, tmp_path):  # noqa: F811
    corridor, _, _, dem = site
    scoring_run.score_hydro(engine, AREA, PROFILE)
    info, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)
    assert info.buildable is None  # not computed yet
    run(engine, dem)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT corridor_id, turbine_buildable_acres, turbine_gentle_acres,"
                " nearest_buildable_m FROM latest_hydro_scores_live ORDER BY corridor_id"
            )
        ).all()
    assert len(rows) == 2
    on_site = {r.corridor_id: r for r in rows}[corridor]
    assert on_site.turbine_buildable_acres > 0 and on_site.turbine_gentle_acres > 0
    assert on_site.nearest_buildable_m < 30

    info, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)
    assert info.buildable.max_slope_pct == 15
    assert info.soils_version.startswith("TN999 version 1 (1/1/2026), downloaded ")
    with (out_dir / "all_corridors.csv").open() as f:
        exported = {int(r["nhdplusid"]): r for r in csv.DictReader(f)}
    with_ground = [r for r in exported.values() if r["turbine_parcel_buildable_acres"]]
    assert len(with_ground) == 1 and with_ground[0]["buildable_flags"] == ""
    assert float(with_ground[0]["nearest_buildable_ft"]) < 100
    report = (out_dir / "report.md").read_text()
    assert "Buildable ground on the turbine parcel (ranked" in report
    assert "a screen, not a site evaluation" in report
