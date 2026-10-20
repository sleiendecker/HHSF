import json

import pytest
from shapely.geometry import LineString, box

from hhsf.analysis import basins
from hhsf.core import crs
from hhsf.core.config import AOI

AREA = AOI(key="fixture", name="Fixture County", county_geoid="99999", buffer_m=100)


def feature(huc8, geom):
    return {"type": "Feature", "properties": {"huc8": huc8}, "geometry": geom.__geo_interface__}


def test_load_basins_reads_cached_wbd(tmp_path):
    west, east = box(-85.5, 35.3, -85.4, 35.5), box(-85.4, 35.3, -85.3, 35.5)
    body = {"features": [feature("06020004", east), feature("05130107", west)]}
    (tmp_path / "wbd_hu8_fixture.geojson").write_text(json.dumps(body))
    b = basins.load_basins(AREA, box(-85.45, 35.35, -85.35, 35.45), tmp_path)
    assert list(b.polygons) == ["05130107", "06020004"]
    # A reach flowing east across the divide drains to the eastern basin.
    reach = LineString([(-85.45, 35.4), (-85.35, 35.4)])
    assert b.huc8_of(reach) == "06020004"
    assert b.huc8_of(LineString([(-85.0, 35.4), (-84.9, 35.4)])) is None
    assert b.polygons["06020004"].equals_exact(crs.to_analysis(east), 1e-6)


def test_no_basins_is_an_error(tmp_path):
    (tmp_path / "wbd_hu8_fixture.geojson").write_text(json.dumps({"features": []}))
    with pytest.raises(RuntimeError, match="no WBD basins"):
        basins.load_basins(AREA, box(-85.45, 35.35, -85.35, 35.45), tmp_path)
