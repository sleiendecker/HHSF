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


def test_main_component_pasture_takes_the_best_yield_of_the_largest_component():
    rows = [
        # Allen 92% (two pasture crops: the better one), Townley 8%.
        {"mukey": "1", "cokey": "a", "comppct_r": "92", "cropname": "Pasture",
         "nonirryield_r": "6"},
        {"mukey": "1", "cokey": "a", "comppct_r": "92", "cropname": "Tall fescue",
         "nonirryield_r": "6.5"},
        {"mukey": "1", "cokey": "b", "comppct_r": "8", "cropname": "Pasture",
         "nonirryield_r": "9"},
        # Rock outcrop is the main component: no pasture.
        {"mukey": "2", "cokey": "c", "comppct_r": "60", "cropname": None, "nonirryield_r": None},
        {"mukey": "2", "cokey": "d", "comppct_r": "40", "cropname": "Pasture",
         "nonirryield_r": "3"},
        # A tie goes to the lower yield.
        {"mukey": "3", "cokey": "e", "comppct_r": "50", "cropname": "Pasture",
         "nonirryield_r": "7"},
        {"mukey": "3", "cokey": "f", "comppct_r": "50", "cropname": "Pasture",
         "nonirryield_r": "4"},
    ]  # fmt: skip
    assert soils.main_component_pasture(rows) == {"1": 6.5, "2": None, "3": 4.0}


def test_add_agriculture_fills_farmland_capability_and_pasture():
    unit_rows = [
        {"mukey": m, "areasymbol": "TN153", "musym": m, "muname": m}
        | dict.fromkeys(["hydclprs", "brockdepmin", "wtdepannmin", "flodfreqdcd", "drclassdcd"])
        for m in ("1", "2")
    ]
    units = soils.parse_map_units(unit_rows, [], RULES)
    ag_rows = [
        {"mukey": "1", "farmlndcl": "All areas are prime farmland", "niccdcd": "2",
         "niccdcdpct": "90"},
    ]  # fmt: skip
    pasture_rows = [
        {"mukey": "1", "cokey": "a", "comppct_r": "90", "cropname": "Pasture",
         "nonirryield_r": "7"},
    ]  # fmt: skip
    prime, unknown = soils.add_agriculture(units, ag_rows, pasture_rows)
    assert prime.farmland_class == "All areas are prime farmland"
    assert (prime.capability_class, prime.capability_pct, prime.pasture_aum) == (2, 90, 7)
    assert (unknown.farmland_class, unknown.capability_class, unknown.pasture_aum) == (
        None,
        None,
        None,
    )


def test_agriculture_queries_ask_for_the_right_columns():
    assert "mu.farmlndcl, ma.niccdcd, ma.niccdcdpct" in soils.agriculture_query("TN153")
    q = soils.pasture_query("TN153")
    assert "yldunits = 'AUM'" in q and "LEFT JOIN cocropyld" in q


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
        if "cointerp" in query or "cocropyld" in query:
            return []
        if "farmlndcl" in query:
            return [{"mukey": "1", "farmlndcl": "Not prime farmland", "niccdcd": "6",
                     "niccdcdpct": "100"}]  # fmt: skip
        return [{"mukey": "1", "areasymbol": "TN999", "musym": "X", "muname": "Fixture"}
                | dict.fromkeys(["hydclprs", "brockdepmin", "wtdepannmin", "flodfreqdcd",
                                 "drclassdcd"])]  # fmt: skip

    monkeypatch.setattr(soils, "_post", post)
    aoi = soils.AOI(key="fixture", name="Fixture", county_geoid="47999")
    units, got, versions = soils.fetch_soils(aoi, echo=lambda _: None)
    assert [p.mupolygonkey for p in got] == [3, 5, 9]
    assert versions == {"TN999": "TN999 version 20 (8/28/2026)"}
    assert len(units) == 1 and units[0].capability_class == 6
    pages = sorted((tmp_path / "raw" / "usda_ssurgo" / "fixture" / "TN999").glob("polygons_*"))
    assert [p.name for p in pages] == ["polygons_0000.json", "polygons_0001.json"]
    assert json.loads(pages[1].read_text())[0]["mupolygonkey"] == 9

    n = len(calls)
    soils.fetch_soils(aoi, echo=lambda _: None)
    assert len(calls) == n  # all from the cache
