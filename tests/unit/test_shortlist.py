"""Grouping stretches into parcels and ranking the parcels on hydro (PART 23)."""

import json

from hhsf.analysis import export, shortlist, subdivision
from hhsf.analysis.shortlist import Stretch

_ids = iter(range(1, 10_000))


def stretch(county="47153", parcel="001 001.00", tier="low_flow", hydro=30.0, **kw) -> Stretch:
    """A ranked stretch; `hydro` is the hydro part of its overall score (what's ranked on)."""
    cid = next(_ids)
    defaults = dict(
        corridor_id=cid,
        aoi_key="sequatchie_tn",
        score_run_id=1,
        county_geoid=county,
        parcel_id=parcel,
        gislink=f"{county}-{parcel}",
        county_name="Sequatchie",
        tier=tier,
        overall_score=50.0,
        hydro_score=hydro if tier in ("medium", "low") else None,
        hydro_part=hydro,
        nhdplusid=cid,
        firm_power_w=1500 if tier in ("medium", "low") else 100,
        net_head_ft=50,
        acres=100,
        buildable_acres=10,
        drive_minutes=45,
        in_drive_area=True,
        bbox=(-85.5, 35.2, -85.4, 35.3),
    )
    return Stretch(**(defaults | kw))


def test_one_row_per_parcel_keyed_on_county_and_parcel_id():
    entries = shortlist.build(
        [
            stretch("47153", "001 001.00", hydro=30),
            stretch("47153", "001 001.00", hydro=40),
            # The same parcel ID in another county is another parcel.
            stretch("47115", "001 001.00", hydro=35),
        ]
    )
    assert [e.key for e in entries] == [("47153", "001 001.00"), ("47115", "001 001.00")]
    assert [e.stretches for e in entries] == [2, 1]


def test_parcel_shared_by_two_counties_runs_is_one_row():
    # Grundy's and Marion's runs both scored the same stream, ending on one Marion parcel.
    a = stretch("47115", "043 001.00", hydro=55, aoi_key="marion_tn", nhdplusid=7)
    b = stretch("47115", "043 001.00", hydro=54, aoi_key="grundy_tn", nhdplusid=7)
    c = stretch("47115", "043 001.00", hydro=40, aoi_key="grundy_tn")
    [e] = shortlist.build([a, b, c])
    assert e.best is a
    assert e.stretches == 2  # nhdplusid 7 counted once


def test_ranked_by_hydro_across_counties_not_by_power_group_or_overall():
    big_creek = stretch("47061", "037 001.00", tier="medium", hydro=40, overall_score=58.9)
    # A low-flow stretch with a big drop scores higher on the extended power curve.
    steep = stretch("47007", "096 001.04", tier="low_flow", hydro=54.7, overall_score=57)
    land = stretch("47115", "093 001.00", tier="low_flow", hydro=31, overall_score=81.5)
    entries = shortlist.build([big_creek, land, steep])
    assert [(e.rank, e.best) for e in entries] == [(1, steep), (2, big_creek), (3, land)]
    assert shortlist.row(entries[0])["hydro_subscore"] == 54.7


def test_best_power_group_represents_the_parcel():
    # A 160 W stretch with a big drop outscores the parcel's 1 kW stretch on hydro.
    low = stretch(tier="low_flow", hydro=45, firm_power_w=160, net_head_ft=300)
    big_creek = stretch(tier="medium", hydro=40, firm_power_w=1005, stream_name="Big Creek")
    [e] = shortlist.build([low, big_creek])
    assert e.best is big_creek
    assert shortlist.row(e)["power_group"] == shortlist.KW
    assert e.max_power_w == 1005


def test_highest_hydro_within_the_group_wins():
    a = stretch(tier="low_flow", hydro=40)
    b = stretch(tier="low_flow", hydro=45)
    c = stretch(tier="head_only", hydro=30)
    [e] = shortlist.build([a, b, c])
    assert e.best is c  # head only beats low flow
    [e] = shortlist.build([a, b])
    assert e.best is b


def test_ties_break_on_power_then_head_then_key():
    a = stretch("47153", "002", hydro=50, firm_power_w=500, net_head_ft=100)
    b = stretch("47153", "001", hydro=50, firm_power_w=900, net_head_ft=40)
    c = stretch("47153", "003", hydro=50, firm_power_w=900, net_head_ft=80)
    d = stretch("47153", "000", hydro=50, firm_power_w=500, net_head_ft=100)
    assert [e.best for e in shortlist.build([a, b, c, d])] == [c, b, d, a]


def test_no_overall_score_falls_back_to_the_hydro_score_then_last():
    a = stretch("47153", "001", tier="medium", hydro=None, hydro_score=60)
    b = stretch("47153", "002", hydro=None)
    c = stretch("47153", "003", hydro=10)
    assert [e.best for e in shortlist.build([b, c, a])] == [a, c, b]


def test_flags_and_visit_checks():
    s = stretch(
        tier="low_flow",
        buildable_acres=0,
        acres=5,
        deed_acres=12,
        in_drive_area=False,
        stretch_sfha_m=30,
        subdivision_flags=(subdivision.NO_ROAD_FRONTAGE, subdivision.STRETCH_CROSSES_PARCELS),
        flow_flags=("below_regression_min_drainage_area", "regional_yield_basin:06020001"),
    )
    [e] = shortlist.build([s], min_acres=10)
    assert e.flags == (
        shortlist.LOW_FLOW_ESTIMATE,
        "below_regression_min_drainage_area",
        export.UNDER_MIN_ACRES,
        shortlist.DEED_ACRES_DIFFER,
        export.STRETCH_IN_SFHA,
        export.OUTSIDE_DRIVE_AREA,
        export.NO_BUILDABLE_GROUND,
        subdivision.NO_ROAD_FRONTAGE,
        subdivision.STRETCH_CROSSES_PARCELS,
    )
    checks = {c["topic"]: c for c in e.checks}
    assert list(checks) == [
        "Dry-season flow",
        "Drop",
        "Deed and owner",
        "Septic soils",
        "Road access",
        "Flooding",
    ]
    assert all(checks[t]["warn"] for t in ("Dry-season flow", "Deed and owner", "Septic soils"))
    assert "more than one owner" in checks["Deed and owner"]["text"]
    assert "The deed says 12 acres, the outline 5" in checks["Deed and owner"]["text"]
    assert checks["Road access"]["text"].startswith("No public road frontage; over an hour")
    assert checks["Drop"]["warn"] is False and "50 ft net head" in checks["Drop"]["text"]


def test_a_clean_1kw_site_only_has_the_standing_checks():
    [e] = shortlist.build([stretch(tier="medium", hydro=60)])
    assert e.flags == ()
    assert [c["topic"] for c in e.checks if c["warn"]] == []
    assert "Flooding" not in [c["topic"] for c in e.checks]


def test_row_is_rounded_and_carries_no_owner_fields():
    parts = (
        {
            "part": "hydro",
            "subscore": 31.25,
            "weight": 0.4,
            "contribution": 12.5,
            "factors": [
                {"factor": "power", "reason": "r", "subscore": 1, "weight": 1, "breakpoints": []}
            ],
            "missing": [],
        },
    )
    [e] = shortlist.build(
        [stretch(overall_score=81.46872, hydro=31.25, firm_power_w=35.59, parts=parts)]
    )
    r = shortlist.row(e)
    assert r["overall_score"] == 81.5 and r["firm_power_w"] == 36 and r["hydro_subscore"] == 31.2
    explained = json.loads(r["parts"])
    assert explained["parts"][0]["factors"] == [
        {"factor": "power", "reason": "r", "subscore": 1, "weight": 1}
    ]
    assert not any(word in key for key in r for word in ("owner", "address", "tpad"))


def test_row_carries_what_the_filters_need_in_feet():
    s = stretch(
        road_frontage_m=100,
        turbine_public_road_m=402.3,
        buildings_quarter_mile=3,
        turbine_in_sfha=False,
        prime_acres=12.34,
        open_cropland_acres=None,
    )
    r = shortlist.row(shortlist.build([s])[0])
    assert (r["road_frontage_ft"], r["public_road_ft"]) == (328, 1320)
    assert (r["buildings_quarter_mile"], r["turbine_in_sfha"]) == (3, False)
    assert (r["prime_acres"], r["open_cropland_acres"]) == (12.3, None)
