import json

import pytest
from shapely.geometry import box

from hhsf.ingest import soils

RULES = {"septic_tn": "TN rule", "septic_national": "National rule"}


def test_dominant_condition_adds_up_shares_of_the_same_rating():
    # 45% + 20% Slightly limited beats 35% Very limited.
    rating, pct = soils.dominant_condition(
        [("Slightly limited", 45), ("Very limited", 35), ("Slightly limited", 20)]
    )
    assert (rating, pct) == ("Slightly limited", 65)


def test_dominant_condition_breaks_ties_toward_the_more_limiting():
    assert soils.dominant_condition([("Somewhat limited", 40), ("Very limited", 40)]) == (
        "Very limited",
        40,
    )
    assert soils.dominant_condition([("Not rated", 50), ("Slightly limited", 50)]) == (
        "Slightly limited",
        50,
    )


def test_dominant_condition_of_nothing_is_not_rated():
    assert soils.dominant_condition([]) == (soils.NOT_RATED, 0.0)


def test_parse_map_units_rates_each_rule_separately():
    unit_rows = [
        {
            "mukey": 529626,
            "areasymbol": "TN153",
            "musym": "LnC",
            "muname": "Lily  loam, 5 to 12 percent slopes",
            "hydclprs": "0",
            "brockdepmin": "76",
            "wtdepannmin": None,
            "flodfreqdcd": "None",
            "drclassdcd": "Well drained",
        },
        {"mukey": "1", "areasymbol": "TN153", "musym": "W", "muname": "Water"}
        | dict.fromkeys(["hydclprs", "brockdepmin", "wtdepannmin", "flodfreqdcd", "drclassdcd"]),
    ]
    rating_rows = [
        {"mukey": "529626", "cokey": "1", "comppct_r": "85", "mrulename": "TN rule",
         "interphrc": "Slightly limited"},
        {"mukey": "529626", "cokey": "2", "comppct_r": "15", "mrulename": "TN rule",
         "interphrc": "Very limited"},
        {"mukey": "529626", "cokey": "1", "comppct_r": "85", "mrulename": "National rule",
         "interphrc": "Very limited"},
    ]  # fmt: skip
    units = soils.parse_map_units(unit_rows, soils.parse_ratings(rating_rows), RULES)
    lily, water = units
    assert lily.mukey == "529626" and lily.muname == "Lily loam, 5 to 12 percent slopes"
    assert lily.ratings == {
        "septic_tn": ("Slightly limited", 85),
        "septic_national": ("Very limited", 85),
    }
    assert (lily.bedrock_min_cm, lily.water_table_min_cm, lily.hydric_pct) == (76, None, 0)
    assert water.ratings["septic_tn"] == (soils.NOT_RATED, 0.0)


def test_survey_area_query_names_the_county_the_ssurgo_way():
    assert "'TN153'" in soils.survey_areas_query("47153")
    with pytest.raises(KeyError):
        soils.survey_areas_query("06001")  # California isn't listed


def test_queries_quote_their_strings():
    assert "'O''Brien'" in soils.map_units_query("O'Brien")
    q = soils.ratings_query("TN153", RULES.values())
    assert "'TN rule', 'National rule'" in q and "ruledepth = 0" in q


def test_parse_polygon_repairs_and_keeps_polygons():
    p = soils.parse_polygon({"mupolygonkey": "7", "mukey": "42", "wkt": box(0, 0, 1, 1).wkt}, "TN1")
    assert (p.mupolygonkey, p.mukey, p.areasymbol) == (7, "42", "TN1")
    assert p.geom.geom_type == "MultiPolygon" and p.geom.area == 1


def test_fetch_pages_polygons_by_key_and_caches_every_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(soils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        soils, "data_source_settings", lambda _: {"page_size": 2, "septic_rules": RULES}
    )
    polygons = [
        {"mupolygonkey": k, "mukey": "1", "wkt": box(k, 0, k + 1, 1).wkt} for k in (3, 5, 9)
    ]
    calls = []

    def post(query):
        calls.append(query)
        if "FROM laoverlap" in query:
            return [{"areasymbol": "TN999", "areaname": "Fixture", "saversion": "20",
                     "saverest": "8/28/2026 1:44:32 PM"}]  # fmt: skip
        if "FROM mupolygon" in query:
            after = int(query.split("mupolygonkey >")[1].split()[0])
            return [p for p in polygons if p["mupolygonkey"] > after][:2]
        if "cointerp" in query:
            return []
        return [{"mukey": "1", "areasymbol": "TN999", "musym": "X", "muname": "Fixture"}
                | dict.fromkeys(["hydclprs", "brockdepmin", "wtdepannmin", "flodfreqdcd",
                                 "drclassdcd"])]  # fmt: skip

    monkeypatch.setattr(soils, "_post", post)
    aoi = soils.AOI(key="fixture", name="Fixture", county_geoid="47999")
    units, got, versions = soils.fetch_soils(aoi, echo=lambda _: None)
    assert [p.mupolygonkey for p in got] == [3, 5, 9]
    assert versions == {"TN999": "TN999 version 20 (8/28/2026)"}
    assert len(units) == 1
    pages = sorted((tmp_path / "raw" / "usda_ssurgo" / "fixture" / "TN999").glob("polygons_*"))
    assert [p.name for p in pages] == ["polygons_0000.json", "polygons_0001.json"]
    assert json.loads(pages[1].read_text())[0]["mupolygonkey"] == 9

    n = len(calls)
    soils.fetch_soils(aoi, echo=lambda _: None)
    assert len(calls) == n  # all from the cache
