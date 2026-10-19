import pytest
from shapely.geometry import LineString, Point

from hhsf.core import crs


def test_length_is_meters_not_degrees():
    # 0.01 degrees of latitude is ~1,110 m anywhere on Earth.
    line = LineString([(-85.4, 35.40), (-85.4, 35.41)])
    assert crs.length_m(line) == pytest.approx(1110, rel=0.01)


def test_one_degree_of_longitude_shrinks_with_latitude():
    # At ~35.4°N, 0.01° of longitude is ~cos(35.4°) × 1,113 m ≈ 907 m.
    line = LineString([(-85.40, 35.4), (-85.39, 35.4)])
    assert crs.length_m(line) == pytest.approx(907, rel=0.01)


def test_round_trip_through_analysis_crs():
    p = Point(-85.4, 35.4)
    back = crs.to_storage(crs.to_analysis(p))
    assert back.x == pytest.approx(p.x, abs=1e-9)
    assert back.y == pytest.approx(p.y, abs=1e-9)
