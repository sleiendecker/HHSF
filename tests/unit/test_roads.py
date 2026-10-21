import numpy as np
import pyogrio.raw
import pytest
import shapely
from shapely.geometry import LineString, MultiLineString

from hhsf.ingest import roads


def write_roads(path, rows):
    """A tiny TIGER/Line-style roads shapefile: (LINEARID, FULLNAME, RTTYP, MTFCC, line)."""
    pyogrio.raw.write(
        path,
        np.array([shapely.to_wkb(r[4]) for r in rows], dtype=object),
        [np.array([r[i] for r in rows], dtype=object) for i in range(4)],
        fields=["LINEARID", "FULLNAME", "RTTYP", "MTFCC"],
        geometry_type="LineString",
        crs="EPSG:4269",
        driver="ESRI Shapefile",
    )


def test_file_url_names_the_county_and_year():
    url = roads.file_url("47153")
    assert url.endswith("/TIGER2025/ROADS/tl_2025_47153_roads.zip")
    assert roads.cache_path("47153").name == "tl_2025_47153_roads.zip"


@pytest.mark.parametrize("geoid", ["4715", "47l53", ""])
def test_file_url_rejects_bad_codes(geoid):
    with pytest.raises(ValueError):
        roads.file_url(geoid)


@pytest.mark.parametrize(
    ("mtfcc", "key", "public", "limited"),
    [
        ("S1100", "primary", True, True),
        ("S1200", "secondary", True, False),
        ("S1400", "local", True, False),
        ("S1500", "4wd_trail", False, False),
        ("S1630", "ramp", True, True),
        ("S1740", "private", False, False),
    ],
)
def test_road_classes(mtfcc, key, public, limited):
    c = roads.road_class(mtfcc)
    assert (c.key, c.public, c.limited_access) == (key, public, limited)
    assert roads.ROAD_LABELS[key] == c.label


@pytest.mark.parametrize("mtfcc", ["S1710", "S1720", "S1750", "S1820", "S1830", "H3010"])
def test_paths_and_internal_lines_are_not_roads(mtfcc):
    assert roads.road_class(mtfcc) is None


def test_read_roads_keeps_drivable_roads_only(tmp_path):
    path = tmp_path / "tl_2025_99999_roads.shp"
    line = LineString([(-85.4, 35.4), (-85.39, 35.41)])
    write_roads(
        path,
        [
            ("1101", "Old York Hwy N", "M", "S1400", line),
            ("1102", None, None, "S1500", line),
            ("1103", None, None, "S1710", line),  # a walkway
            ("1104", None, None, "S1750", line),  # internal Census use
        ],
    )
    records = roads.read_roads(path, "99999")
    assert [(r.linearid, r.name, r.route_type, r.mtfcc) for r in records] == [
        ("1101", "Old York Hwy N", "M", "S1400"),
        ("1102", None, None, "S1500"),
    ]
    assert all(isinstance(r.geom, MultiLineString) for r in records)
    assert records[0].county_geoid == "99999"
    # NAD83 to WGS84 moves points by well under a meter here.
    assert records[0].geom.geoms[0].coords[0] == pytest.approx((-85.4, 35.4), abs=1e-5)
