"""Grouping stretches into parcels and ranking the parcels (PART 23)."""

import json

from hhsf.analysis import export, shortlist, subdivision
from hhsf.analysis.shortlist import Stretch

_ids = iter(range(1, 10_000))


def stretch(county="47153", parcel="001 001.00", tier="low_flow", overall=50.0, **kw) -> Stretch:
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
        overall_score=overall,
        hydro_score=None,
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
            stretch("47153", "001 001.00", overall=60),
            stretch("47153", "001 001.00", overall=70),
            # The same parcel ID in another county is another parcel.
            stretch("47115", "001 001.00", overall=65),
        ]
    )
    assert [e.key for e in entries] == [("47153", "001 001.00"), ("47115", "001 001.00")]
    assert [e.stretches for e in entries] == [2, 1]


def test_parcel_shared_by_two_counties_runs_is_one_row():
    # Grundy's and Marion's runs both scored the same stream, ending on one Marion parcel.
    a = stretch("47115", "043 001.00", overall=55, aoi_key="marion_tn", nhdplusid=7)
    b = stretch("47115", "043 001.00", overall=54, aoi_key="grundy_tn", nhdplusid=7)
    c = stretch("47115", "043 001.00", overall=40, aoi_key="grundy_tn")
    [e] = shortlist.build([a, b, c])
    assert e.best is a
    assert e.stretches == 2  # nhdplusid 7 counted once


def test_ranked_by_overall_score_across_counties_not_by_power_group():
    kw = stretch("47061", "037 001.00", tier="medium", overall=58.9, hydro_score=40)
    land = stretch("47115", "093 001.00", tier="low_flow", overall=81.5)
    mid = stretch("47153", "042 024.00", tier="low_flow", overall=70)
    entries = shortlist.build([kw, land, mid])
    assert [(e.rank, e.best) for e in entries] == [(1, land), (2, mid), (3, kw)]


def test_best_power_group_represents_the_parcel():
    # Big Creek's parcel: low-flow stretches score a little higher than its 1 kW stretch.
    low = stretch(tier="low_flow", overall=59.8, firm_power_w=125)
    big_creek = stretch(tier="medium", overall=58.9, firm_power_w=1005, stream_name="Big Creek")
    [e] = shortlist.build([low, big_creek])
    assert e.best is big_creek
    assert shortlist.row(e)["power_group"] == shortlist.KW
    assert e.max_power_w == 1005


def test_highest_overall_within_the_group_wins():
    a = stretch(tier="low_flow", overall=40)
    b = stretch(tier="low_flow", overall=45)
    c = stretch(tier="head_only", overall=30)
    [e] = shortlist.build([a, b, c])
    assert e.best is c  # head only beats low flow
    [e] = shortlist.build([a, b])
    assert e.best is b


def test_ties_break_on_hydro_then_power_then_key():
    a = stretch("47153", "002", overall=50, hydro_score=10)
    b = stretch("47153", "001", overall=50, hydro_score=20)
    c = stretch("47153", "003", overall=50, hydro_score=20, firm_power_w=900)
    assert [e.best for e in shortlist.build([a, b, c])] == [c, b, a]


def test_missing_overall_score_ranks_last():
    a = stretch("47153", "001", overall=None)
    b = stretch("47153", "002", overall=1)
    assert [e.best for e in shortlist.build([a, b])] == [b, a]


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
    [e] = shortlist.build([stretch(tier="medium", overall=60)])
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
    [e] = shortlist.build([stretch(overall=81.46872, firm_power_w=35.59, parts=parts)])
    r = shortlist.row(e)
    assert r["overall_score"] == 81.5 and r["firm_power_w"] == 36 and r["hydro_subscore"] == 31.2
    explained = json.loads(r["parts"])
    assert explained["parts"][0]["factors"] == [
        {"factor": "power", "reason": "r", "subscore": 1, "weight": 1}
    ]
    assert not any(word in key for key in r for word in ("owner", "address", "tpad"))
