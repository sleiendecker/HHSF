import gzip
import json

import pytest
import shapely
from shapely.geometry import box

from hhsf.core import crs
from hhsf.ingest import buildings


def test_quadkey_matches_bing_tiles():
    # Bing Maps tile system: zoom 3 tile x=3, y=5 is "213".
    assert buildings.quadkey(-45, -45, 3) == "213"
    # A building in Microsoft's file for tile 032002112 (Bledsoe County, TN).
    assert buildings.quadkey(-85.0824, 35.8558, 9) == "032002112"


def test_tile_bounds_contain_their_points():
    xmin, ymin, xmax, ymax = buildings.tile_bounds("032002112")
    assert xmin < -85.0824 < xmax and ymin < 35.8558 < ymax
    assert xmax - xmin == pytest.approx(360 / 2**9)


def test_quadkeys_cover_a_box_across_tiles():
    keys = buildings.quadkeys((-85.92, 34.98, -84.71, 35.83), 9)
    assert keys == ["032002103", "032002112", "032002113", "032002121", "032002130", "032002131"]


def test_parse_links_keeps_the_region_and_release():
    url = "https://x/global-buildings/2026-02-03/global-buildings.geojsonl/RegionName={r}/quadkey={q}/part.csv.gz"
    text = "Location,QuadKey,Url,Size,UploadDate\n" + "\n".join(
        f"{r},{q},{url.format(r=r, q=q)},1MB,2026-02-23"
        for r, q in [("UnitedStates", "032002112"), ("Canada", "032002112"), ("UnitedStates", "1")]
    )
    found = buildings.parse_links(text, "UnitedStates")
    assert sorted(found) == ["032002112", "1"]
    assert found["032002112"][0].release == "2026-02-03"


def test_read_tile_keeps_polygons_inside_the_area_with_their_size(tmp_path):
    near = box(-85.40, 35.37, -85.3999, 35.3701)  # about 9 x 11 m, near Dunlap
    far = box(-80, 35, -79.9999, 35.0001)
    lines = [
        {"type": "Feature", "properties": {"height": 4.2}, "geometry": near.__geo_interface__},
        {"type": "Feature", "properties": {"height": -1}, "geometry": far.__geo_interface__},
    ]
    path = tmp_path / "t.csv.gz"
    with gzip.open(path, "wt") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")
    every = buildings.read_tile(path)
    assert [b.height_m for b in every] == [4.2, None]
    assert every[0].area_m2 == pytest.approx(crs.area_m2(near))
    keep = crs.to_analysis(shapely.box(-85.5, 35.3, -85.3, 35.4))
    [kept] = buildings.read_tile(path, keep)
    assert kept.geom.geom_type == "MultiPolygon" and kept.geom.equals(shapely.MultiPolygon([near]))
