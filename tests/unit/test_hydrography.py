import pytest

from hhsf.ingest import hydrography


@pytest.mark.parametrize(
    ("fcode", "expected"),
    [(46006, True), (46003, False), (46007, False), (55800, False), (None, False)],
)
def test_perennial_filter(fcode, expected):
    assert hydrography.is_perennial(fcode) is expected


def test_parse_converts_units_and_geometry(sample_features):
    record = hydrography.parse_feature(sample_features[0])
    assert record.nhdplusid == 25000100000001
    assert record.geom.geom_type == "MultiLineString"
    assert record.max_elev_m == pytest.approx(552.71)
    assert record.min_elev_m == pytest.approx(446.70)
    assert record.length_m > 1000  # measured in meters, not degrees


def test_parse_treats_sentinels_as_missing(sample_features):
    record = hydrography.parse_feature(sample_features[1])
    assert record.slope is None
    assert record.stream_name is None
