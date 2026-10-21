import pytest
from shapely.geometry import MultiPolygon, box, mapping

from hhsf.core.config import load_aoi
from hhsf.ingest import flood


def feature(fld_ar_id="47153C_101", zone="AE", subtype=None, sfha="T", geom=None, **props):
    values = {
        "OBJECTID": 1,
        "DFIRM_ID": "47153C",
        "FLD_AR_ID": fld_ar_id,
        "FLD_ZONE": zone,
        "ZONE_SUBTY": subtype,
        "SFHA_TF": sfha,
    }
    return {
        "type": "Feature",
        "properties": {**values, **props},
        "geometry": mapping(geom or box(-85.38, 35.43, -85.37, 35.44)),
    }


@pytest.mark.parametrize(
    ("aoi", "dfirm"),
    [
        ("bledsoe_tn", "47007C"),
        ("grundy_tn", "47061C"),
        ("marion_tn", "47115C"),
        ("rhea_tn", "47143C"),
        ("sequatchie_tn", "47153C"),
    ],
)
def test_each_county_uses_its_countywide_flood_map(aoi, dfirm):
    # Checked against the NFHL's FIRM Panels layer on 2026-10-20.
    assert flood.dfirm_id(load_aoi(aoi).county_geoid) == dfirm


@pytest.mark.parametrize("geoid", ["4715", "47l53", ""])
def test_dfirm_id_rejects_bad_codes(geoid):
    with pytest.raises(ValueError):
        flood.dfirm_id(geoid)


def test_parse_special_flood_hazard_area():
    z = flood.parse_feature(feature(), "47153")
    assert (z.fld_ar_id, z.dfirm_id, z.county_geoid, z.zone) == (
        "47153C_101",
        "47153C",
        "47153",
        "AE",
    )
    assert z.sfha and not z.floodway
    assert z.subtype is None
    assert isinstance(z.geom, MultiPolygon)


def test_parse_floodway():
    z = flood.parse_feature(feature(subtype="FLOODWAY"), "47153")
    assert z.sfha and z.floodway


def test_parse_zone_x_is_not_sfha():
    z = flood.parse_feature(
        feature(zone="X", subtype="0.2 PCT ANNUAL CHANCE  FLOOD HAZARD", sfha="F"), "47153"
    )
    assert not z.sfha and not z.floodway
    assert z.subtype == "0.2 PCT ANNUAL CHANCE FLOOD HAZARD"


def test_zone_without_id_is_an_error():
    with pytest.raises(ValueError):
        flood.parse_feature(feature(fld_ar_id=" "), "47153")
