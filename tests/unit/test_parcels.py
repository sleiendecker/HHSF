import pytest
from shapely.geometry import MultiPolygon, Polygon, box, mapping

from hhsf.core.config import load_aoi
from hhsf.ingest import arcgis, parcels


def feature(gislink="077030    00133", geom=None, **props):
    values = {
        "OBJECTID": 1,
        "COUNTY_ID": 77,
        "COUNTY_NAME": "Sequatchie",
        "PARCEL_TYPE": 1,
        "GISLINK": gislink,
        "CMAP": "030",
        "GP": " ",
        "PARCEL": "001.33",
        "DEEDAC": 81.39,
        "LINK_TPAD": "https://assessment.cot.tn.gov/TPAD/Parcel/GIS?gislink=077030",
        "OWNER": "SMITH  JOHN",
        "OWNER2": " ",
        "ADDRESS": "100  ROGERS RD",
    }
    return {
        "type": "Feature",
        "properties": {**values, **props},
        "geometry": mapping(geom or box(-85.38, 35.43, -85.37, 35.44)),
    }


@pytest.mark.parametrize(
    ("aoi", "number"),
    [
        ("bledsoe_tn", 4),
        ("grundy_tn", 31),
        ("marion_tn", 58),
        ("rhea_tn", 72),
        ("sequatchie_tn", 77),
    ],
)
def test_county_number_matches_the_layer(aoi, number):
    # Checked against the layer's COUNTY_ID and COUNTY_NAME on 2026-10-20.
    assert parcels.county_number(load_aoi(aoi).county_geoid) == number


@pytest.mark.parametrize("geoid", ["13047", "47154", "4715", "47191"])
def test_county_number_rejects_non_tennessee_codes(geoid):
    with pytest.raises(ValueError):
        parcels.county_number(geoid)


def test_parcel_label_skips_a_blank_group():
    assert parcels.parcel_label("030", " ", "001.33") == "030 001.33"
    assert parcels.parcel_label("048M", "D", "001.10") == "048M D 001.10"


def test_parse_feature_splits_owners_from_the_parcel():
    parcel, owner = parcels.parse_feature(feature(), "47153")

    assert parcel.gislink == "077030 00133"
    assert parcel.parcel_id == "030 001.33"
    assert parcel.county_geoid == "47153"
    assert parcel.deed_acres == 81.39
    # 0.01 x 0.01 degrees at 35.4 N is about 0.9 km x 1.1 km, ~250 acres.
    assert 230 < parcel.acres < 270
    assert not any("owner" in k or "address" in k for k in parcel.__dict__)
    assert (owner.gislink, owner.owner, owner.owner2, owner.property_address) == (
        "077030 00133",
        "SMITH JOHN",
        None,
        "100 ROGERS RD",
    )


def test_zero_deed_acres_means_not_recorded():
    parcel, _ = parcels.parse_feature(feature(DEEDAC=0), "47153")
    assert parcel.deed_acres is None


def test_self_intersecting_outline_is_repaired():
    bowtie = Polygon([(-85.4, 35.4), (-85.39, 35.41), (-85.39, 35.4), (-85.4, 35.41)])
    parcel, _ = parcels.parse_feature(feature(geom=bowtie), "47153")
    assert parcel.geom.is_valid
    assert isinstance(parcel.geom, MultiPolygon)
    assert len(parcel.geom.geoms) == 2


def test_parcel_drawn_as_two_features_is_merged():
    west = parcels.parse_feature(feature(geom=box(-85.40, 35.4, -85.39, 35.41)), "47153")
    east = parcels.parse_feature(feature(geom=box(-85.39, 35.4, -85.38, 35.41)), "47153")
    other = parcels.parse_feature(feature("077030    00134"), "47153")

    merged = parcels.merge_duplicates([west, east, other])

    assert [p.gislink for p, _ in merged] == ["077030 00133", "077030 00134"]
    assert merged[0][0].acres == pytest.approx(west[0].acres + east[0].acres, rel=0.01)


def test_paged_query_follows_offsets_and_caches_each_page(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, params):
        calls.append(params["resultOffset"])
        n = {0: 2, 2: 2, 4: 1}[params["resultOffset"]]
        return {"features": [{"id": params["resultOffset"] + i} for i in range(n)]}

    monkeypatch.setattr(arcgis, "_get", fake_get)
    pages = list(arcgis.query_geojson_paged("http://x", {"where": "1=1"}, tmp_path, 2))

    assert [len(p) for p in pages] == [2, 2, 1]
    assert calls == [0, 2, 4]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "page_0000.geojson",
        "page_0001.geojson",
        "page_0002.geojson",
    ]
    # A second run reads the cache.
    list(arcgis.query_geojson_paged("http://x", {"where": "1=1"}, tmp_path, 2))
    assert calls == [0, 2, 4]
