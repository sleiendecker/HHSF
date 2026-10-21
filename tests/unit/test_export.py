import csv
import io
import json
from dataclasses import replace

import pytest

from hhsf.analysis import export, scoring

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
        "| 1 | 1 | C1 | 67.5 | 105 | 2,000 | 0.2 | streamstats_q90 | 35.39000, -85.39000 |"
        in medium
    )
    assert "(unnamed)" in medium and "C4" not in medium
    head = md.split("## Top 1 of 1: Head only")[1]
    assert "| 1 | 6 | A/B | 50.0 | 105 | 1.00 |" in head
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
