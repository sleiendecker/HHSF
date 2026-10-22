import csv
import io
import json
from dataclasses import replace

import pytest

from hhsf.analysis import export, scoring, subdivision

PROFILE = scoring.load_profile("hydro_v1")

INFO = export.RunInfo(
    aoi_key="test_aoi",
    aoi_name="Test County, TN",
    score_run_id=7,
    hydro_run_id=5,
    score_code_version="abc1234",
    hydro_code_version="def5678",
    scoring_profile_version="hydro_v1",
    finished_at="2026-10-19 15:01 UTC",
    data_source_versions={"streamstats": "ss v1", "nhdplus_hr_bulk": "final"},
    hydro_params={
        "hydro": {
            "system_efficiency": 0.5,
            "net_head_factor": 0.85,
            "diversion_fraction": 0.5,
            "streamstats": {
                "statistic_code": "D90",
                "regression_regions": ["GC1419"],
                "min_drainage_area_sqmi": 1.3,
            },
        },
        "use_streamstats": True,
        "reference_gauge": {
            "site_no": "03571000",
            "q90_cfs": 77.2,
            "drainage_area_sqmi": 402.0,
            "period": ["2001-10-01", "2025-09-30"],
        },
    },
    score_params={"scoring_profile": PROFILE.model_dump()},
    exported_with="ghi9012",
)

LINE = {"type": "MultiLineString", "coordinates": [[[-85.4, 35.4], [-85.39, 35.39]]]}


def row(i=1, status=scoring.SCORED, tier="medium", rank=1, tier_rank=1, **kw):
    values = {
        "corridor_id": i,
        "nhdplusid": 25000300000000 + i,
        "stream_name": f"Creek {i}",
        "status": status,
        "tier": tier,
        "rank": rank,
        "tier_rank": tier_rank,
        "hydro_score": 67.456,
        "head_subscore": 50.04,
        "power_subscore": 75.0,
        "net_head_ft": 105.4,
        "firm_power_w": 2000.4,
        "firm_flow_cfs": 0.2,
        "flow_method": "streamstats_q90",
        "flow_confidence": "medium",
        "flow_flags": (),
        "drainage_area_sqkm": 2.58999,
        "length_m": 1609.344,
        "outlet_lon": -85.390001,
        "outlet_lat": 35.390004,
        "explanation": {"summary": f"Summary {i}."},
        "geometry": LINE,
    }
    return export.ExportRow(**{**values, **kw})


def head_only(i, rank, tier_rank, **kw):
    kw = {
        "hydro_score": None,
        "power_subscore": None,
        "firm_power_w": None,
        "firm_flow_cfs": None,
        "flow_method": None,
        "flow_confidence": "below_applicability",
        "flow_flags": ("below_regression_min_drainage_area",),
        **kw,
    }
    return row(i, scoring.HEAD_ONLY, scoring.HEAD_ONLY, rank, tier_rank, **kw)


def filtered(i, status=scoring.BELOW_POWER_TARGET, **kw):
    return row(i, status, None, None, None, hydro_score=None, **kw)


ROWS = [
    filtered(1, firm_power_w=400.0, explanation={"summary": "400 W is below the 1 kW target."}),
    head_only(2, 3, 1),
    row(3, tier="low", rank=2, tier_rank=1, flow_method="gauge_drainage_area_ratio"),
    row(4),
    filtered(5, scoring.NOT_SCORABLE, net_head_ft=None, drainage_area_sqkm=None),
]


def read_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


# --- Rows ---------------------------------------------------------------------


def test_record_uses_readable_units_and_rounding():
    r = export.record(row(flow_flags=("a", "b")))
    assert r["hydro_score"] == 67.5
    assert r["head_subscore"] == 50.0
    assert r["net_head_ft"] == 105
    assert r["firm_power_w"] == 2000
    assert r["firm_flow_cfs"] == 0.2
    assert r["firm_flow_gpm"] == 90  # 0.2 cfs x 448.831
    assert r["drainage_area_sqmi"] == 1.0
    assert r["corridor_length_ft"] == 5280
    assert r["reach_length_mi"] == 1.0  # no separate reach length: the corridor is the reach
    assert (r["outlet_lat"], r["outlet_lon"]) == (35.39, -85.39)
    assert r["flow_flags"] == "a; b"
    assert r["explanation"] == "Summary 1."


def test_record_separates_stretch_and_reach_lengths():
    r = export.record(row(length_m=304.8, reach_length_m=3218.688))
    assert r["corridor_length_ft"] == 1000
    assert r["reach_length_mi"] == 2.0


def test_record_keeps_missing_values_missing():
    r = export.record(head_only(2, 1, 1, drainage_area_sqkm=None, outlet_lat=None, explanation={}))
    assert r["hydro_score"] is None
    assert r["firm_flow_cfs"] is None and r["firm_flow_gpm"] is None
    assert r["drainage_area_sqmi"] is None
    assert r["outlet_lat"] is None
    assert r["explanation"] == ""


def test_shortlist_is_ranked_rows_in_rank_order():
    assert [r.corridor_id for r in export.shortlist(ROWS)] == [4, 3, 2]


def test_all_corridors_puts_ranked_first_then_filtered_by_status():
    assert [r.corridor_id for r in export.all_corridors(ROWS)] == [4, 3, 2, 1, 5]


def test_shortlist_csv_columns_and_blanks():
    rows = read_csv(export.to_csv(export.shortlist(ROWS), export.SHORTLIST_COLUMNS))
    assert list(rows[0]) == export.SHORTLIST_COLUMNS
    assert [r["rank"] for r in rows] == ["1", "2", "3"]
    assert rows[2]["tier"] == "head_only"
    assert rows[2]["hydro_score"] == "" and rows[2]["firm_power_w"] == ""
    assert rows[2]["flow_flags"] == "below_regression_min_drainage_area"


def test_all_corridors_csv_says_why_filtered():
    rows = read_csv(export.to_csv(export.all_corridors(ROWS), export.ALL_CORRIDORS_COLUMNS))
    assert list(rows[0])[0] == "status"
    assert len(rows) == len(ROWS)
    dropped = rows[3]
    assert dropped["status"] == "below_power_target"
    assert dropped["rank"] == ""
    assert dropped["explanation"] == "400 W is below the 1 kW target."


def test_csv_quotes_commas_in_text():
    text = export.to_csv([row(stream_name="Creek, East Fork")], export.SHORTLIST_COLUMNS)
    assert read_csv(text)[0]["stream_name"] == "Creek, East Fork"


def test_geojson_has_one_feature_per_row_in_wgs84():
    fc = export.to_geojson(export.shortlist(ROWS), INFO)
    assert fc["type"] == "FeatureCollection"
    assert fc["crs"]["properties"]["name"].endswith("CRS84")
    assert fc["name"] == "hhsf_test_aoi_score_run_7"
    first = fc["features"][0]
    assert first["id"] == 4
    assert first["geometry"] == LINE
    assert list(first["properties"]) == export.GEOJSON_COLUMNS
    assert first["properties"]["firm_power_w"] == 2000
    json.dumps(fc)  # serializable


# --- Report -------------------------------------------------------------------


def test_assumptions_come_from_run_params():
    a = dict(export.assumptions(INFO))
    assert a["System efficiency (water to electricity, D-06)"] == "0.5"
    assert a["Firm flow statistic (D-04)"] == "D90"
    assert a["StreamStats used in this run"] == "yes"
    assert "03571000" in a["Reference gauge for the drainage-area fallback"]
    assert a["Power target, below which corridors drop (D-01)"] == "1,000 W"
    assert a["Score weights (D-11)"] == "power 0.7, head 0.3"
    assert a["Ranking tiers, in order"] == "medium > low > head_only"


def test_stretch_runs_say_how_long_the_penstock_is():
    hydro = {**INFO.hydro_params["hydro"], "corridor": {"penstock_length_ft": 1000}}
    params = {**INFO.hydro_params, "hydro": hydro, "corridor_method": "dem_3dep_steepest_stretch"}
    info = replace(INFO, hydro_params=params)
    assert dict(export.assumptions(info))["Corridor: longest penstock (ft, D-08)"] == "1,000"
    assert "steepest 1,000 ft of each stream reach" in export.caveats(info)[0]


def test_whole_reach_runs_say_so():
    a = dict(export.assumptions(INFO))
    assert a["Corridor: longest penstock (ft, D-08)"].startswith("whole stream reach")
    assert export.caveats(INFO)[0].startswith("**Head and power are for the whole stream reach.**")


def test_assumptions_follow_changed_params():
    params = {"scoring_profile": {**PROFILE.model_dump(), "power_target_w": 2500}}
    info = replace(INFO, score_params=params)
    a = dict(export.assumptions(info))
    assert a["Power target, below which corridors drop (D-01)"] == "2,500 W"
    assert any("2.5 kW target" in c for c in export.caveats(info))


def test_assumptions_without_gauge_or_profile():
    info = replace(INFO, hydro_params={"use_streamstats": False}, score_params={})
    a = dict(export.assumptions(info))
    assert a["Reference gauge for the drainage-area fallback"] == "none"
    assert a["System efficiency (water to electricity, D-06)"] == "–"
    assert a["StreamStats used in this run"] == "no"
    assert "Score weights (D-11)" not in a
    assert any("power target" in c for c in export.caveats(info))


def test_report_has_provenance_counts_caveats_and_links():
    md = export.render_report(INFO, ROWS)
    assert md.startswith("# Hydro shortlist: Test County, TN")
    assert "3 of 5 stream reaches made the list" in md
    for text in (
        "| Score run | 7 (finished 2026-10-19 15:01 UTC) |",
        "| Hydro run it scored | 5 |",
        "`hydro_v1`",
        "`abc1234`",
        "`def5678`",
        "`ghi9012`",
        "| streamstats | ss v1 |",
    ):
        assert text in md
    assert "| Filtered: firm power below the target | 1 |" in md
    assert "| Filtered: head or flow missing | 1 |" in md
    assert "| **Total** | **5** |" in md
    assert "| Medium confidence (StreamStats Q90) | 1 |" in md
    assert "D-08" in md and "10×" in md and "no flow estimate" in md
    assert "(../../../../docs/SCORING.md)" in md
    assert "(../../../../docs/ASSUMPTIONS.md)" in md


def test_report_lists_top_n_per_tier_in_order():
    rows = [
        row(i, rank=i, tier_rank=i, stream_name=None if i == 2 else f"C{i}") for i in range(1, 6)
    ]
    rows.append(head_only(9, 6, 1, stream_name="A|B"))
    md = export.render_report(INFO, rows, top_n=3)
    assert "## Top 3 of 5: Medium confidence" in md
    assert "## Top 1 of 1: Head only" in md
    assert "None in this run." in md  # the low tier
    medium = md.split("## Top 3 of 5")[1].split("## Top")[0]
    assert (
        "| 1 | 1 | C1 | – | 67.5 | 105 | 2,000 | 0.2 | streamstats_q90 | 35.39000, -85.39000 |"
        in medium
    )
    assert "(unnamed)" in medium and "C4" not in medium
    head = md.split("## Top 1 of 1: Head only")[1]
    assert "| 1 | 6 | A/B | – | 50.0 | 105 | 1.00 |" in head
    assert md.index("Medium confidence (StreamStats") < md.index("## Low confidence")


def test_report_handles_missing_points_and_unknown_status():
    rows = [row(outlet_lat=None), filtered(2, "something_new")]
    md = export.render_report(INFO, rows)
    assert "| – | 25000300000001 |" in md
    assert "| something_new | 1 |" in md


def test_report_without_aoi_name_or_exporter():
    info = replace(INFO, aoi_name=None, exported_with=None)
    md = export.render_report(info, ROWS)
    assert md.startswith("# Hydro shortlist: test_aoi")
    assert "Exported with" not in md


# --- Files --------------------------------------------------------------------


def test_write_exports_writes_four_files(tmp_path):
    out = tmp_path / "test_aoi" / "run_7"
    paths = export.write_exports(out, INFO, ROWS)
    assert sorted(p.name for p in paths) == sorted(
        ["shortlist.csv", "shortlist.geojson", "all_corridors.csv", "report.md"]
    )
    assert len(read_csv((out / "shortlist.csv").read_text())) == 3
    assert len(read_csv((out / "all_corridors.csv").read_text())) == 5
    assert len(json.loads((out / "shortlist.geojson").read_text())["features"]) == 3
    assert (out / "report.md").read_text().startswith("# Hydro shortlist")


@pytest.mark.parametrize("name", ["shortlist.csv", "report.md"])
def test_exports_are_reproducible(tmp_path, name):
    export.write_exports(tmp_path / "a", INFO, ROWS)
    export.write_exports(tmp_path / "b", INFO, list(reversed(ROWS)))
    assert (tmp_path / "a" / name).read_text() == (tmp_path / "b" / name).read_text()


# --- Parcels ------------------------------------------------------------------

PARCEL = export.Parcel(
    parcel_id="030 001.33",
    county_name="Sequatchie",
    acres=76.34,
    deed_acres=81.39,
    tpad_url="https://assessment.cot.tn.gov/TPAD/Parcel/GIS?gislink=077030",
    owner="SMITH JOHN",
    owner2="SMITH JANE",
    property_address="100 ROGERS RD",
)
ON_PARCEL = row(
    turbine_parcel=PARCEL,
    turbine_parcel_distance_m=0.0,
    stretch_parcel_ids=("030 001.33", "030 037.00"),
    stretch_parcel_owners=("DOE ANN", "SMITH JOHN"),
)


def test_parcel_flags():
    small = replace(PARCEL, acres=4.0)
    assert export.parcel_flags(PARCEL, 0.0, 10) == ()
    assert export.parcel_flags(None, None, 10) == (export.NO_PARCEL,)
    assert export.parcel_flags(small, 30.0, 10) == (
        export.OUTSIDE_PARCEL,
        export.UNDER_MIN_ACRES,
    )


def test_record_has_parcels_and_owners():
    r = export.record(ON_PARCEL)
    assert r["turbine_parcel_id"] == "030 001.33"
    assert r["turbine_parcel_acres"] == 76.3
    assert r["turbine_parcel_distance_ft"] == 0
    assert r["stretch_parcel_count"] == 2
    assert r["stretch_parcel_ids"] == "030 001.33; 030 037.00"
    assert r["turbine_parcel_owner"] == "SMITH JOHN"
    assert r["stretch_parcel_owners"] == "DOE ANN; SMITH JOHN"


def test_record_without_parcels_is_blank():
    r = export.record(row())
    assert r["turbine_parcel_id"] is None
    assert r["stretch_parcel_count"] is None
    assert r["turbine_parcel_owner"] is None


def test_owners_are_in_the_csv_but_never_the_geojson():
    [csv_row] = read_csv(export.to_csv([ON_PARCEL], export.SHORTLIST_COLUMNS))
    assert csv_row["turbine_parcel_owner"] == "SMITH JOHN"
    props = export.to_geojson([ON_PARCEL], INFO)["features"][0]["properties"]
    assert props["turbine_parcel_id"] == "030 001.33"
    assert not export.OWNER_COLUMNS & props.keys()
    assert "SMITH" not in json.dumps(props)


def test_report_lists_the_turbine_parcel_and_owner():
    loaded = replace(INFO, parcels_version="TNMap parcels, downloaded 2026-10-20")
    report = export.render_report(loaded, [ON_PARCEL])
    assert "| 030 001.33 (76) | SMITH JOHN & SMITH JANE |" in report
    assert "| tn_parcels (at export) | TNMap parcels, downloaded 2026-10-20 |" in report
    assert "not legal boundaries" in report


def test_report_says_when_parcels_are_not_loaded():
    assert "No parcels are loaded" in export.render_report(INFO, ROWS)


# --- Flood zones --------------------------------------------------------------

FLOODWAY = export.FloodZone(zone="AE", subtype="FLOODWAY", sfha=True, floodway=True)
ZONE_X = export.FloodZone(zone="X", subtype="AREA OF MINIMAL FLOOD HAZARD", sfha=False)
IN_FLOODWAY = row(
    turbine_flood_zone=FLOODWAY,
    stretch_flood_zones=("AE floodway", "X (minimal hazard)"),
    stretch_sfha_m=30.48,
    flood_flags=export.flood_flags(FLOODWAY, 30.48),
)


@pytest.mark.parametrize(
    ("zone", "subtype", "label"),
    [
        ("A", None, "A"),
        ("AE", "FLOODWAY", "AE floodway"),
        ("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD", "X (0.2% annual chance)"),
        ("X", "AREA OF MINIMAL FLOOD HAZARD", "X (minimal hazard)"),
        (
            "X",
            "AREA WITH REDUCED FLOOD RISK DUE TO LEVEE",
            "X (area with reduced flood risk due to levee)",
        ),
    ],
)
def test_flood_zone_labels(zone, subtype, label):
    assert export.flood_zone_label(zone, subtype) == label


def test_flood_flags():
    assert export.flood_flags(FLOODWAY, 30.0) == (
        export.TURBINE_IN_FLOODWAY,
        export.TURBINE_IN_SFHA,
        export.STRETCH_IN_SFHA,
    )
    assert export.flood_flags(ZONE_X, 0.0) == ()
    assert export.flood_flags(ZONE_X, 12.0) == (export.STRETCH_IN_SFHA,)
    assert export.flood_flags(None, None) == (export.FLOOD_NOT_MAPPED,)


def test_record_has_flood_zones():
    r = export.record(IN_FLOODWAY)
    assert r["turbine_flood_zone"] == "AE"
    assert r["turbine_flood_subtype"] == "FLOODWAY"
    assert r["turbine_in_sfha"] is True
    assert r["stretch_sfha_ft"] == 100
    assert r["stretch_flood_zones"] == "AE floodway; X (minimal hazard)"
    assert r["flood_flags"] == "turbine_in_floodway; turbine_in_sfha; stretch_in_sfha"


def test_record_without_flood_zones_is_blank():
    r = export.record(row())
    assert r["turbine_flood_zone"] is None
    assert r["turbine_in_sfha"] is None
    assert r["stretch_sfha_ft"] is None
    assert r["flood_flags"] == ""


def test_flood_columns_are_in_the_geojson():
    props = export.to_geojson([IN_FLOODWAY], INFO)["features"][0]["properties"]
    assert props["turbine_flood_zone"] == "AE"
    assert props["flood_flags"].startswith("turbine_in_floodway")


def test_report_counts_flood_flags_and_says_where_data_is_missing():
    loaded = replace(INFO, flood_version="NFHL effective, downloaded 2026-10-20")
    unmapped = row(2, rank=2, tier_rank=2, flood_flags=(export.FLOOD_NOT_MAPPED,))
    report = export.render_report(loaded, [IN_FLOODWAY, unmapped])
    assert "| fema_nfhl (at export) | NFHL effective, downloaded 2026-10-20 |" in report
    assert "| Turbine end in a floodway | 1 |" in report
    assert "| Turbine end not on any FEMA map | 1 |" in report
    assert "| AE floodway |" in report and "| not mapped |" in report
    assert "never studied" in report and "flagged only" in report


def test_report_says_when_flood_zones_are_not_loaded():
    report = export.render_report(INFO, ROWS)
    assert "No flood zones are loaded" in report
    assert "FEMA flood zones (ranked corridors" not in report


# --- Roads and drive time -------------------------------------------------------

TRAIL = export.Road(road_class="4wd_trail", name=None, public=False, distance_m=100.0)
AREA = export.DriveArea(
    origin_name="Downtown Chattanooga",
    minutes=60,
    built="2026-10-20",
    engine_version="Valhalla 3.9.1, road graph built 2026-10-20",
)
FAR = row(
    turbine_road=TRAIL,
    turbine_public_road_m=804.672,  # half a mile
    drive_minutes=83.4,
    drive_km=101.4,
    drive_road_point_m=30.48,
    in_drive_area=False,
    access_flags=export.access_flags(TRAIL, 83.4, False, True),
)


def test_access_flags():
    local = export.Road("local", "Smith Mountain Rd", True, 50.0)
    assert export.access_flags(TRAIL, 83.4, False, True) == (
        export.OUTSIDE_DRIVE_AREA,
        export.ROAD_NOT_PUBLIC,
    )
    assert export.access_flags(local, 40.0, True, True) == ()
    assert export.access_flags(local, None, True, True) == (export.NO_DRIVE_TIME,)
    assert export.access_flags(local, 40.0, True, True, drive_any_road=True) == (
        export.DRIVE_ENDS_ON_TRACK,
    )
    # No drive-time area built: nothing to say about drives.
    assert export.access_flags(local, None, None, False) == ()


def test_record_has_roads_and_drive_time():
    r = export.record(FAR)
    assert r["turbine_road_ft"] == 328
    assert r["turbine_road_type"] == "4WD trail"
    assert r["turbine_road_name"] is None
    assert r["turbine_public_road_ft"] == 2640
    assert (r["drive_minutes"], r["drive_miles"], r["drive_road_point_ft"]) == (83, 63.0, 100)
    assert r["in_drive_area"] is False
    assert r["access_flags"] == "outside_drive_area; nearest_road_not_public"


def test_record_without_roads_or_drive_time_is_blank():
    r = export.record(row())
    assert r["turbine_road_ft"] is None and r["drive_minutes"] is None
    assert r["in_drive_area"] is None and r["access_flags"] == ""


def test_road_columns_are_in_the_geojson():
    props = export.to_geojson([FAR], INFO)["features"][0]["properties"]
    assert props["turbine_public_road_ft"] == 2640 and props["drive_minutes"] == 83


def test_report_counts_access_and_credits_openstreetmap():
    loaded = replace(INFO, roads_version="TIGER/Line 2025, downloaded 2026-10-20", drive_area=AREA)
    near = row(
        2,
        rank=2,
        tier_rank=2,
        turbine_road=export.Road("local", None, True, 50.0),
        turbine_public_road_m=50.0,
        drive_minutes=40.0,
        in_drive_area=True,
    )
    report = export.render_report(loaded, [FAR, near])
    assert "| Turbine end inside the 60-minute drive area | 1 |" in report
    assert "| Turbine end outside it | 1 |" in report
    assert "| Public road within 1/4 mile of the turbine end | 1 |" in report
    assert "| Nearest road a 4WD trail or private road | 1 |" in report
    assert "| 2,640 | 83 (outside) |" in report and "| 164 | 40 |" in report
    assert "© OpenStreetMap contributors" in report and "Downtown Chattanooga" in report
    assert "| osm_valhalla (at export) |" in report and "| tiger_roads (at export) |" in report


def test_report_says_when_roads_and_drive_area_are_missing():
    report = export.render_report(INFO, ROWS)
    assert "No roads are loaded" in report and "No drive-time area is built" in report
    assert "Roads and drive time (ranked corridors" not in report


# --- Buildable ground ---------------------------------------------------------

BUILD_INFO = export.BuildableInfo(
    max_slope_pct=15.0,
    septic_rating="septic_tn",
    septic_excluded=("Very limited", "Not rated"),
    min_patch_acres=0.5,
    computed="2026-10-21",
)
SOME_GROUND = export.Buildable(gentle_acres=12.34, buildable_acres=4.56, nearest_m=152.4)
NO_GROUND = export.Buildable(gentle_acres=7.4, buildable_acres=0.0, nearest_m=None)


def test_buildable_flags():
    assert export.buildable_flags(PARCEL, SOME_GROUND) == ()
    assert export.buildable_flags(PARCEL, NO_GROUND) == (export.NO_BUILDABLE_GROUND,)
    assert export.buildable_flags(PARCEL, None) == (export.BUILDABLE_NOT_COMPUTED,)
    assert export.buildable_flags(None, None) == ()  # no parcel: the parcel flags say so
    partly = export.Buildable(gentle_acres=9.0, buildable_acres=2.0, nearest_m=0, no_soil_acres=3)
    assert export.buildable_flags(PARCEL, partly, 0.5) == (export.SOILS_NOT_LOADED,)
    assert export.buildable_flags(PARCEL, partly, 5) == ()


def test_record_has_buildable_ground():
    r = export.record(replace(ON_PARCEL, turbine_buildable=SOME_GROUND))
    assert r["turbine_parcel_gentle_acres"] == 12.3
    assert r["turbine_parcel_buildable_acres"] == 4.6
    assert r["nearest_buildable_ft"] == 500
    none = export.record(
        replace(ON_PARCEL, turbine_buildable=NO_GROUND, buildable_flags=("no_buildable_ground",))
    )
    assert none["turbine_parcel_buildable_acres"] == 0.0 and none["nearest_buildable_ft"] is None
    assert none["buildable_flags"] == "no_buildable_ground"
    blank = export.record(ROWS[0])
    assert blank["turbine_parcel_buildable_acres"] is None and blank["buildable_flags"] == ""


def test_buildable_columns_are_in_the_geojson():
    rows = [replace(ON_PARCEL, turbine_buildable=SOME_GROUND)]
    props = export.to_geojson(rows, INFO)["features"][0]["properties"]
    assert props["turbine_parcel_buildable_acres"] == 4.6 and props["nearest_buildable_ft"] == 500


def test_report_counts_buildable_ground_and_says_it_is_a_screen():
    loaded = replace(INFO, buildable=BUILD_INFO, soils_version="TN153 version 20, downloaded")
    rows = [
        replace(ON_PARCEL, turbine_buildable=SOME_GROUND),
        row(
            2,
            rank=2,
            tier_rank=2,
            turbine_parcel=PARCEL,
            turbine_buildable=NO_GROUND,
            buildable_flags=(export.NO_BUILDABLE_GROUND,),
        ),
        row(
            3,
            rank=3,
            tier_rank=3,
            turbine_parcel=PARCEL,
            buildable_flags=(export.BUILDABLE_NOT_COMPUTED,),
        ),
    ]
    report = export.render_report(loaded, rows)
    assert "| Some buildable ground | 1 |" in report
    assert "| Within 1/4 mile of the turbine end | 1 |" in report
    assert "| None | 1 |" in report
    assert "| Not computed (turbine parcel not analyzed) | 1 |" in report
    assert "sloped 15% or less" in report and "Tennessee septic rating" in report
    assert "Very limited or Not rated" in report and "0.5 acres" in report
    assert "| usda_ssurgo (at export) |" in report
    assert "| 4.6 |" in report  # the tier table's buildable column


def test_report_says_when_buildable_ground_is_missing():
    report = export.render_report(INFO, ROWS)
    assert "Buildable ground isn't computed" in report
    assert "Buildable ground on the turbine parcel (ranked" not in report


# --- Farmland -----------------------------------------------------------------

FARM_INFO = export.FarmlandInfo(
    max_capability_class=3, wet_drainage=("Poorly drained",), computed="2026-10-21"
)
SOME_FARMLAND = export.Farmland(
    parcel_acres=76.3,
    soil_acres=76.3,
    prime_acres=12.34,
    prime_if_acres=1.0,
    statewide_acres=0.0,
    cropland_acres=30.06,
    wet_acres=2.0,
    pasture_aum=410.4,
)
NO_FARMLAND = replace(SOME_FARMLAND, prime_acres=0.0, prime_if_acres=0.0, cropland_acres=0.0)


def test_farmland_flags():
    assert export.farmland_flags(PARCEL, SOME_FARMLAND) == ()
    assert export.farmland_flags(PARCEL, NO_FARMLAND) == (export.NO_FARMLAND,)
    # Prime only if drained doesn't count as farmland; statewide importance does.
    assert export.farmland_flags(PARCEL, replace(NO_FARMLAND, prime_if_acres=5)) == (
        export.NO_FARMLAND,
    )
    assert export.farmland_flags(PARCEL, replace(NO_FARMLAND, statewide_acres=5)) == ()
    assert export.farmland_flags(PARCEL, None) == (export.FARMLAND_NOT_COMPUTED,)
    assert export.farmland_flags(None, None) == ()
    partly = replace(SOME_FARMLAND, soil_acres=70.0)
    assert export.farmland_flags(PARCEL, partly) == (export.SOILS_NOT_LOADED,)
    assert export.farmland_flags(PARCEL, replace(SOME_FARMLAND, soil_acres=75.8)) == ()


def test_record_has_farmland():
    r = export.record(replace(ON_PARCEL, turbine_farmland=SOME_FARMLAND))
    assert r["turbine_parcel_prime_acres"] == 12.3
    assert r["turbine_parcel_cropland_acres"] == 30.1
    assert r["turbine_parcel_pasture_aum"] == 410
    blank = export.record(ROWS[0])
    assert blank["turbine_parcel_prime_acres"] is None and blank["farmland_flags"] == ""
    props = export.to_geojson([replace(ON_PARCEL, turbine_farmland=SOME_FARMLAND)], INFO)
    assert props["features"][0]["properties"]["turbine_parcel_prime_acres"] == 12.3


def test_report_counts_farmland_and_says_it_is_the_soils():
    loaded = replace(INFO, farmland=FARM_INFO, soils_version="TN153 version 20, downloaded")
    rows = [
        replace(ON_PARCEL, turbine_farmland=SOME_FARMLAND),
        row(
            2,
            rank=2,
            tier_rank=2,
            turbine_parcel=PARCEL,
            turbine_farmland=NO_FARMLAND,
            farmland_flags=(export.NO_FARMLAND,),
        ),
    ]
    report = export.render_report(loaded, rows)
    assert "| Some prime farmland or farmland of statewide importance | 1 |" in report
    assert "| Some cropland (capability class 1–3) | 1 |" in report
    assert "| Neither | 1 |" in report
    assert "not what the land is now" in report and "class 1–3" in report
    assert "| 12 / 30 |" in report  # the tier table's prime / cropland column


def test_report_says_when_farmland_is_missing():
    report = export.render_report(INFO, ROWS)
    assert "Farmland isn't computed" in report
    assert "Farmland on the turbine parcel (ranked" not in report


# --- Overall score ------------------------------------------------------------


def test_record_and_report_have_the_overall_score():
    v4 = replace(
        INFO,
        score_params={"scoring_profile": scoring.load_profile("hydro_v4").model_dump()},
    )
    r = replace(
        ON_PARCEL,
        overall_score=54.84,
        buildability_subscore=61.2,
        agriculture_subscore=None,
        logistics_subscore=52.0,
    )
    rec = export.record(r)
    assert rec["overall_score"] == 54.8 and rec["agriculture_subscore"] is None
    assert export.SHORTLIST_COLUMNS[3:8] == [
        "overall_score",
        "buildability_subscore",
        "agriculture_subscore",
        "privacy_subscore",
        "logistics_subscore",
    ]
    report = export.render_report(v4, [r])
    assert "| 1 | 1 | Creek 1 | 54.8 |" in report
    assert "hydro 0.4, buildability 0.2, agriculture 0.2, privacy 0.1, logistics 0.1" in report
    assert "The overall score ranks each tier" in report and "never scored as zero" in report
    assert "Privacy (FR-6) isn't in this profile" in report
    assert "The overall score ranks each tier" not in export.render_report(INFO, [r])


# --- Land cover ---------------------------------------------------------------

OPEN = export.Landcover(
    open_acres=12.34, open_cropland_acres=8.0, forest_acres=60.0, developed_acres=1.0,
    canopy_pct=71.6,
)  # fmt: skip


def test_landcover_flags_and_record():
    assert export.landcover_flags(PARCEL, OPEN) == ()
    closed = replace(OPEN, open_acres=0.0, open_cropland_acres=0.0)
    assert export.landcover_flags(PARCEL, closed) == (export.NO_OPEN_LAND,)
    assert export.landcover_flags(PARCEL, None) == (export.LANDCOVER_NOT_COMPUTED,)
    assert export.landcover_flags(None, None) == ()
    r = export.record(replace(ON_PARCEL, turbine_landcover=OPEN))
    assert r["turbine_parcel_open_acres"] == 12.3 and r["turbine_parcel_canopy_pct"] == 72


def test_report_counts_land_cover():
    loaded = replace(INFO, landcover_year=2021)
    rows = [
        replace(ON_PARCEL, turbine_landcover=OPEN),
        row(2, rank=2, tier_rank=2, turbine_parcel=PARCEL, landcover_flags=(export.NO_OPEN_LAND,)),
    ]
    report = export.render_report(loaded, rows)
    assert "| Some open land (field, pasture, crops) | 1 |" in report
    assert "| No open land (all forest, shrub, water or built up) | 1 |" in report
    assert "Land cover is from USGS NLCD 2021" in report
    assert "Land cover isn't computed" in export.render_report(INFO, ROWS)


# --- Privacy ------------------------------------------------------------------

PRIVATE = export.Privacy(
    home_site="buildable", lat=35.4, lon=-85.4, buildings_quarter_mile=0, buildings_half_mile=2,
    buildings_per_sqmi=3.18, nearest_building_m=900.0, parcel_buildings=0, public_road_m=400.0,
    road_woods_m=250.0,
)  # fmt: skip
PRIVACY_INFO = export.PrivacyInfo(
    min_footprint_sqft=400, woods_canopy_pct=50, buildings_version="Global ML 2026-02-03",
    computed="2026-10-21",
)  # fmt: skip


def test_privacy_flags_and_record():
    assert export.privacy_flags(PRIVATE) == ()
    exposed = replace(
        PRIVATE, nearest_building_m=150.0, public_road_m=100.0, road_woods_m=0.0, parcel_buildings=2
    )
    assert export.privacy_flags(exposed) == (
        export.NEIGHBOR_CLOSE,
        export.OPEN_TO_ROAD,
        export.BUILDINGS_ON_PARCEL,
    )
    # A road over 500 ft away isn't flagged, woods or not.
    assert export.OPEN_TO_ROAD not in export.privacy_flags(replace(exposed, public_road_m=200.0))
    assert export.privacy_flags(None) == (export.PRIVACY_NOT_COMPUTED,)
    r = export.record(replace(ON_PARCEL, privacy=PRIVATE, privacy_subscore=88.26))
    assert r["privacy_subscore"] == 88.3 and r["home_site"] == "buildable"
    assert r["nearest_building_ft"] == 2953 and r["road_woods_ft"] == 820
    assert r["buildings_per_sqmi"] == 3.2


def test_report_counts_privacy_and_shows_neighbors():
    loaded = replace(INFO, privacy=PRIVACY_INFO)
    rows = [
        replace(ON_PARCEL, privacy=PRIVATE),
        row(2, rank=2, tier_rank=2, privacy_flags=(export.PRIVACY_NOT_COMPUTED,)),
    ]
    report = export.render_report(loaded, rows)
    assert "| No other building within 1/4 mile | 1 |" in report
    assert "| Not computed | 1 |" in report
    assert "Privacy (FR-6) is measured at a home site" in report and "400 sq ft" in report
    assert "0 (nearest 2,953 ft)" in report
    assert "Privacy isn't computed" in export.render_report(INFO, ROWS)


# --- Subdivision feasibility ----------------------------------------------------

CUT = export.Subdivision(
    score=81.25, homestead_cut=True, frontage_m=120.0, stretch_share=0.96, driveway_m=400.0,
    wire_m=150.0, piece_acres=6.04, flags=(subdivision.LONG_DRIVEWAY,),
)  # fmt: skip
CUT_INFO = export.SubdivisionInfo(min_frontage_ft=50, min_stretch_share=0.9, computed="2026-10-22")


def test_subdivision_flags_and_record():
    assert export.subdivision_flags(PARCEL, CUT) == (subdivision.LONG_DRIVEWAY,)
    assert export.subdivision_flags(PARCEL, None) == (export.SUBDIVISION_NOT_COMPUTED,)
    assert export.subdivision_flags(None, None) == ()  # parcel_flags already say why
    r = export.record(replace(ON_PARCEL, subdivision=CUT, subdivision_flags=CUT.flags))
    assert r["subdivision_score"] == 81.2 and r["homestead_cut"] == "yes"
    assert r["road_frontage_ft"] == 394 and r["stretch_on_parcel_pct"] == 96
    assert r["cut_driveway_ft"] == 1312 and r["homestead_piece_acres"] == 6.0
    assert r["subdivision_flags"] == "long_driveway"
    assert export.record(ON_PARCEL)["homestead_cut"] is None


def test_report_counts_homestead_cuts():
    loaded = replace(INFO, subdivision=CUT_INFO)
    rows = [
        replace(ON_PARCEL, subdivision=CUT, subdivision_flags=CUT.flags),
        row(2, rank=2, tier_rank=2, subdivision_flags=(export.SUBDIVISION_NOT_COMPUTED,)),
    ]
    report = export.render_report(loaded, rows)
    assert "| A homestead piece can be cut | 1 |" in report
    assert "| Driveway over 1/4 mile | 1 |" in report
    assert "| Not computed | 1 |" in report
    assert "yes (81)" in report  # the tier table's Homestead cut column
    assert "Subdivision feasibility (FR-9) asks" in report and "90% or more" in report
    assert "Subdivision feasibility isn't computed" in export.render_report(INFO, ROWS)
