from dataclasses import replace

import pytest
from pydantic import ValidationError

from hhsf.analysis import scoring
from hhsf.core import units

PROFILE = scoring.load_profile("hydro_v1")


def ft(feet: float) -> float:
    """Net head in meters for a given number of feet."""
    return feet / units.FT_PER_M


def corridor(i=1, head_ft=100.0, watts=2000.0, conf="medium", flow_conf=None, **kw):
    flow_conf = flow_conf or ("unavailable" if watts is None else conf)
    power_conf = "unavailable" if watts is None else conf
    return scoring.CorridorInputs(
        i, None if head_ft is None else ft(head_ft), watts, power_conf, flow_conf, **kw
    )


# --- Profile ------------------------------------------------------------------


def test_profile_matches_d11():
    assert PROFILE.version == "hydro_v1"
    assert PROFILE.power_target_w == 1000
    assert (PROFILE.weights.power, PROFILE.weights.head) == (0.7, 0.3)
    assert PROFILE.head_subscore.breakpoints == [(10, 0), (200, 100)]
    assert PROFILE.power_subscore.breakpoints == [(1000, 50), (3000, 100)]


def test_unknown_profile_lists_available():
    with pytest.raises(FileNotFoundError, match="hydro_v1"):
        scoring.load_profile("nope")


def test_profile_version_must_match_file_name(tmp_path, monkeypatch):
    (tmp_path / "scoring_profiles").mkdir()
    text = (scoring.CONFIG_DIR / "scoring_profiles" / "hydro_v1.yaml").read_text()
    (tmp_path / "scoring_profiles" / "hydro_v2.yaml").write_text(text)
    monkeypatch.setattr(scoring, "CONFIG_DIR", tmp_path)
    with pytest.raises(ValueError, match="says version 'hydro_v1'"):
        scoring.load_profile("hydro_v2")


@pytest.mark.parametrize(
    "points",
    [[(10, 0)], [(10, 0), (10, 100)], [(200, 100), (10, 0)], [(10, 0), (200, 150)]],
)
def test_bad_curves_are_refused(points):
    with pytest.raises(ValidationError):
        scoring.Curve(breakpoints=points)


def test_weights_must_add_to_one():
    with pytest.raises(ValidationError):
        scoring.Weights(power=0.7, head=0.4)
    with pytest.raises(ValidationError):
        scoring.Weights(power=1.2, head=-0.2)


def test_rank_tiers_must_cover_every_tier():
    data = PROFILE.model_dump()
    data["rank_tiers"] = ["medium", "low"]
    with pytest.raises(ValidationError):
        scoring.ScoringProfile.model_validate(data)


# --- Piecewise curves ---------------------------------------------------------


@pytest.mark.parametrize(
    ("head_ft", "expected"),
    [(0, 0), (10, 0), (105, 50), (200, 100), (900, 100)],
)
def test_head_curve(head_ft, expected):
    assert scoring.piecewise(head_ft, PROFILE.head_subscore)[0] == pytest.approx(expected)


@pytest.mark.parametrize(
    ("watts", "expected"),
    [(1000, 50), (2000, 75), (3000, 100), (50_000, 100)],
)
def test_power_curve(watts, expected):
    assert scoring.piecewise(watts, PROFILE.power_subscore)[0] == pytest.approx(expected)


def test_curve_with_several_segments():
    curve = scoring.Curve(breakpoints=[(0, 0), (10, 50), (20, 60)])
    assert scoring.piecewise(5, curve)[0] == 25
    assert scoring.piecewise(15, curve) == (55, "between 10 (50) and 20 (60), linear -> 55.0")


# --- One corridor -------------------------------------------------------------


def test_scored_corridor_combines_power_and_head():
    s = scoring.score_corridor(corridor(head_ft=105, watts=2000), PROFILE)
    assert s.status == scoring.SCORED
    assert s.tier == "medium"
    assert s.power_subscore == pytest.approx(75)
    assert s.head_subscore == pytest.approx(50)
    assert s.hydro_score == pytest.approx(0.7 * 75 + 0.3 * 50)
    assert "Medium confidence" in s.summary


def test_explanation_shows_inputs_breakpoints_and_why():
    s = scoring.score_corridor(corridor(head_ft=105, watts=2000), PROFILE)
    e = s.explanation(PROFILE)
    assert e["profile"] == "hydro_v1"
    power, head = e["factors"]
    assert power == {
        "factor": "power",
        "input": "firm_power_w",
        "value": 2000,
        "breakpoints": [[1000, 50], [3000, 100]],
        "subscore": 75,
        "weight": 0.7,
        "contribution": 52.5,
        "reason": "2,000 W firm power: between 1000 (50) and 3000 (100), linear -> 75.0",
    }
    assert head["input"] == "net_head_ft"
    assert head["contribution"] == pytest.approx(15)
    assert "105 ft net head" in head["reason"]
    assert sum(f["contribution"] for f in e["factors"]) == pytest.approx(s.hydro_score)


def test_exactly_at_target_is_scored():
    assert scoring.score_corridor(corridor(watts=1000), PROFILE).status == scoring.SCORED


def test_below_power_target_is_filtered_not_scored():
    s = scoring.score_corridor(corridor(watts=999), PROFILE)
    assert s.status == scoring.BELOW_POWER_TARGET
    assert s.hydro_score is None and s.tier is None
    assert "below the 1 kW target" in s.summary


def test_low_confidence_power_says_where_flow_came_from():
    s = scoring.score_corridor(
        corridor(watts=2000, conf="low", flow_method="gauge_drainage_area_ratio"), PROFILE
    )
    assert s.tier == "low"
    assert "gauge_drainage_area_ratio" in s.summary


def test_small_basin_is_head_only_and_marked():
    c = corridor(head_ft=105, watts=None, flow_conf="below_applicability")
    s = scoring.score_corridor(c, PROFILE)
    assert s.status == scoring.HEAD_ONLY
    assert s.tier == scoring.HEAD_ONLY
    assert s.hydro_score is None
    assert s.head_subscore == pytest.approx(50)
    assert s.summary.startswith("HEAD ONLY")
    assert s.explanation(PROFILE)["factors"][0]["weight"] == 1.0


def test_head_only_too_low_is_filtered():
    c = corridor(head_ft=5, watts=None, flow_conf="below_applicability")
    assert scoring.score_corridor(c, PROFILE).status == scoring.BELOW_MIN_HEAD


def test_missing_head_is_not_scorable():
    s = scoring.score_corridor(corridor(head_ft=None, watts=None), PROFILE)
    assert s.status == scoring.NOT_SCORABLE
    assert s.net_head_ft is None


def test_missing_flow_for_other_reasons_is_not_scorable():
    c = corridor(watts=None, flow_conf="unavailable", flow_flags=("missing_drainage_area",))
    s = scoring.score_corridor(c, PROFILE)
    assert s.status == scoring.NOT_SCORABLE
    assert "missing_drainage_area" in s.summary


def test_score_is_absolute():
    """A corridor's score never depends on which other corridors are scored (A5)."""
    alone = scoring.score_all([corridor(1, 105, 2000)], PROFILE)[0].hydro_score
    crowd = [corridor(1, 105, 2000), corridor(2, 900, 90_000), corridor(3, 12, 1001)]
    assert scoring.score_all(crowd, PROFILE)[0].hydro_score == alone


# --- Ranking ------------------------------------------------------------------


def test_low_confidence_never_outranks_medium():
    scores = scoring.score_all(
        [
            corridor(1, head_ft=20, watts=1000, conf="medium"),  # weak but medium
            corridor(2, head_ft=900, watts=90_000, conf="low"),  # perfect but low
            corridor(3, head_ft=900, watts=None, flow_conf="below_applicability"),
            corridor(4, head_ft=50, watts=500),  # filtered
        ],
        PROFILE,
    )
    by_id = {s.corridor_id: s for s in scores}
    assert by_id[2].hydro_score > by_id[1].hydro_score
    assert [by_id[i].rank for i in (1, 2, 3)] == [1, 2, 3]
    assert by_id[3].tier_rank == 1
    assert by_id[4].rank is None


def test_rank_orders_by_score_then_head_then_id():
    scores = scoring.score_all(
        [
            corridor(1, head_ft=300, watts=5000),  # 100
            corridor(2, head_ft=250, watts=5000),  # 100, less head
            corridor(3, head_ft=300, watts=5000),  # 100, same head, higher id
            corridor(4, head_ft=105, watts=2000),
            corridor(5, head_ft=60, watts=None, flow_conf="below_applicability"),
            corridor(6, head_ft=150, watts=None, flow_conf="below_applicability"),
        ],
        PROFILE,
    )
    ranked = sorted((s for s in scores if s.rank), key=lambda s: s.rank)
    assert [s.corridor_id for s in ranked] == [1, 3, 2, 4, 6, 5]
    assert [s.tier_rank for s in ranked] == [1, 2, 3, 4, 1, 2]


def test_tier_order_comes_from_profile():
    data = PROFILE.model_dump()
    data["rank_tiers"] = ["low", "medium", "head_only"]
    p = scoring.ScoringProfile.model_validate(data)
    scores = scoring.score_all([corridor(1, conf="medium"), corridor(2, conf="low")], p)
    assert [s.rank for s in scores] == [2, 1]


# --- Low-flow tier (hydro_v2) -------------------------------------------------

V2 = scoring.load_profile("hydro_v2")


def test_v2_differs_from_v1_only_by_the_low_flow_tier():
    v1, v2 = PROFILE.model_dump(), V2.model_dump()
    assert v2.pop("rank_tiers") == ["medium", "low", "head_only", "low_flow"]
    v1.pop("rank_tiers")
    assert {**v1, "version": "hydro_v2"} == v2
    assert V2.keeps_low_flow and not PROFILE.keeps_low_flow


def test_rank_tiers_refuse_unknown_or_repeated_tiers():
    for tiers in (["medium", "low", "head_only", "extra"], ["medium", "low", "head_only", "low"]):
        data = {**V2.model_dump(), "rank_tiers": tiers}
        with pytest.raises(ValidationError):
            scoring.ScoringProfile.model_validate(data)


def test_low_confidence_below_target_stays_listed_as_low_flow():
    s = scoring.score_corridor(
        corridor(watts=56, conf="low", flow_method="regional_streamstats_yield"), V2
    )
    assert s.status == scoring.LOW_FLOW and s.tier == scoring.LOW_FLOW
    assert s.hydro_score is None
    assert s.summary.startswith("LOW FLOW: 56 W")
    assert "regional_streamstats_yield" in s.summary
    # hydro_v1 filters the same corridor.
    assert scoring.score_corridor(corridor(watts=56, conf="low"), PROFILE).status == (
        scoring.BELOW_POWER_TARGET
    )


def test_medium_confidence_below_target_is_still_filtered():
    s = scoring.score_corridor(corridor(watts=500, conf="medium"), V2)
    assert s.status == scoring.BELOW_POWER_TARGET and s.rank is None


def test_low_flow_with_too_little_head_is_filtered():
    s = scoring.score_corridor(corridor(head_ft=5, watts=20, conf="low"), V2)
    assert s.status == scoring.BELOW_MIN_HEAD
    assert "low-confidence flow" in s.summary


def test_low_flow_ranks_last_by_power():
    scores = scoring.score_all(
        [
            corridor(1, head_ft=300, watts=20, conf="low"),
            corridor(2, head_ft=100, watts=50, conf="low"),
            corridor(3, head_ft=60, watts=None, flow_conf="below_applicability"),
            corridor(4, head_ft=50, watts=1200, conf="low"),
            corridor(5, head_ft=50, watts=900, conf="medium"),  # filtered
        ],
        V2,
    )
    ranked = sorted((s for s in scores if s.rank), key=lambda s: s.rank)
    assert [s.corridor_id for s in ranked] == [4, 3, 2, 1]
    assert [s.tier_rank for s in ranked] == [1, 1, 1, 2]


# --- Minimum head for every tier (hydro_v3) -------------------------------------

V3 = scoring.load_profile("hydro_v3")


def test_v3_differs_from_v2_only_by_the_minimum_head_for_every_tier():
    v2, v3 = V2.model_dump(), V3.model_dump()
    assert (v2.pop("min_net_head_ft"), v3.pop("min_net_head_ft")) == (None, 10)
    assert {**v2, "version": "hydro_v3"} == v3


def test_v3_filters_corridors_over_the_target_with_too_little_head():
    # A big river: plenty of power, a foot of drop.
    river = corridor(head_ft=1.0, watts=1200, conf="medium")
    s = scoring.score_corridor(river, V3)
    assert (s.status, s.tier, s.rank, s.hydro_score) == (scoring.BELOW_MIN_HEAD, None, None, None)
    assert s.summary == "1,200 W firm power, but 1 ft net head is below 10 ft."
    assert scoring.score_corridor(river, V2).status == scoring.SCORED
    assert scoring.score_corridor(corridor(head_ft=10.0, watts=1200), V3).status == (scoring.SCORED)


# --- Overall score (hydro_v4, FR-7) ---------------------------------------------

V4 = scoring.load_profile("hydro_v4")
V5 = scoring.load_profile("hydro_v5")
# Analyzed, and there's nothing: no buildable ground, farmland or open land; a long drive.
BARE = scoring.SiteInputs(
    buildable_acres=0,
    public_road_ft=5280,
    farmland_acres=0,
    cropland_acres=0,
    pasture_aum=0,
    open_cropland_acres=0,
    drive_minutes=120,
)
GOOD_SITE = scoring.SiteInputs(
    buildable_acres=5,
    nearest_buildable_ft=0,
    public_road_ft=500,
    farmland_acres=20,
    cropland_acres=40,
    pasture_aum=240,
    drive_minutes=30,
)


def test_v4_keeps_v3_hydro_rules_and_adds_fr7_weights():
    v3 = scoring.load_profile("hydro_v3")
    assert V4.model_dump(exclude={"version", "overall"}) == v3.model_dump(
        exclude={"version", "overall"}
    )
    assert V4.overall.weights == {
        "hydro": 0.4,
        "buildability": 0.2,
        "agriculture": 0.2,
        "privacy": 0.1,
        "logistics": 0.1,
    }
    assert v3.overall is None


def test_overall_profile_rejects_bad_weights_and_unknown_factors():
    raw = V4.overall.model_dump()
    with pytest.raises(ValidationError, match="add up to 1"):
        scoring.Overall.model_validate({**raw, "weights": {**raw["weights"], "hydro": 0.5}})
    bad = {**raw, "logistics": {"fuel_price": {"weight": 1, "breakpoints": [[0, 0], [1, 100]]}}}
    with pytest.raises(ValidationError, match="logistics factors"):
        scoring.Overall.model_validate(bad)


def test_best_site_scores_its_hydro_part_rescaled_without_privacy():
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000, site=GOOD_SITE)], V4)
    assert s.hydro_score == pytest.approx(100)
    assert (s.buildability_subscore, s.agriculture_subscore, s.logistics_subscore) == (
        100,
        100,
        100,
    )
    assert s.overall_score == pytest.approx(100)
    assert s.overall["left_out"] == ["privacy"]
    weights = {p["part"]: p["weight"] for p in s.overall["parts"]}
    assert weights["hydro"] == pytest.approx(0.4 / 0.9, abs=1e-4)
    assert sum(p["contribution"] for p in s.overall["parts"]) == pytest.approx(100, abs=0.05)
    assert "Overall 100.0 = hydro 100.0 x 0.44" in s.summary


def test_missing_data_is_left_out_but_measured_zeros_count():
    # No turbine parcel: only road access and drive time are known.
    no_parcel = scoring.SiteInputs(public_road_ft=500, drive_minutes=30)
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000, site=no_parcel)], V4)
    assert s.agriculture_subscore is None and s.buildability_subscore == 100
    build = next(p for p in s.overall["parts"] if p["part"] == "buildability")
    assert build["missing"] == ["buildable_acres", "nearest_buildable_ft"]
    assert s.overall["left_out"] == ["privacy", "agriculture"]
    assert s.overall_score == pytest.approx(100)
    # Analyzed, and there's nothing: zeros, and the distance scores as the far end.
    bare = BARE
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000, site=bare)], V4)
    assert (s.buildability_subscore, s.agriculture_subscore, s.logistics_subscore) == (0, 0, 0)
    assert s.overall_score == pytest.approx(100 * 0.4 / 0.9)
    # No site data at all: the hydro part alone.
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000)], V4)
    assert s.overall_score == pytest.approx(100)


def test_low_flow_hydro_part_extends_the_power_curve_below_the_target():
    c = corridor(head_ft=200, watts=500, conf="low", flow_method="regional_streamstats_yield")
    [s] = scoring.score_all([c], V4)
    assert s.status == scoring.LOW_FLOW and s.hydro_score is None
    hydro = next(p for p in s.overall["parts"] if p["part"] == "hydro")
    # 500 W is halfway to 1 kW: 25 x 0.7 + head 100 x 0.3.
    assert hydro["subscore"] == pytest.approx(47.5)


def test_v4_ranks_each_tier_by_overall_score():
    poor_land = BARE
    corridors = [
        corridor(1, head_ft=200, watts=3000, site=poor_land),  # best hydro, no land
        corridor(2, head_ft=150, watts=2000, site=GOOD_SITE),
        corridor(3, head_ft=50, watts=1500, conf="low", site=GOOD_SITE),
    ]
    by_id = {s.corridor_id: s for s in scoring.score_all(corridors, V4)}
    assert by_id[1].hydro_score > by_id[2].hydro_score
    assert (by_id[2].rank, by_id[1].rank) == (1, 2)
    # A low-confidence corridor never outranks a medium one, whatever its overall score.
    assert by_id[3].overall_score > by_id[1].overall_score and by_id[3].rank == 3
    # v3 ranks by hydro score and has no overall score.
    v3 = {s.corridor_id: s for s in scoring.score_all(corridors, scoring.load_profile("hydro_v3"))}
    assert (v3[1].rank, v3[2].rank) == (1, 2) and v3[1].overall_score is None
    assert "overall" not in v3[1].explanation(PROFILE)


def test_v5_counts_open_cropland_and_v4_ignores_it():
    forested = replace(GOOD_SITE, open_cropland_acres=0)
    [v4] = scoring.score_all([corridor(head_ft=200, watts=3000, site=forested)], V4)
    [v5] = scoring.score_all([corridor(head_ft=200, watts=3000, site=forested)], V5)
    assert v4.agriculture_subscore == pytest.approx(100)
    assert v5.agriculture_subscore == pytest.approx(70)  # 0.3 of it is open cropland
    agri = next(p for p in v5.overall["parts"] if p["part"] == "agriculture")
    assert [f["factor"] for f in agri["factors"]][-1] == "open_cropland_acres"
    # Land cover not computed: that factor is left out, not scored as zero.
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000, site=GOOD_SITE)], V5)
    assert s.agriculture_subscore == pytest.approx(100)
    assert (
        "open_cropland_acres"
        in next(p for p in s.overall["parts"] if p["part"] == "agriculture")["missing"]
    )


V6 = scoring.load_profile("hydro_v6")
SECLUDED = dict(
    buildings_quarter_mile=0,
    nearest_building_ft=None,  # none within the search radius
    buildings_per_sqmi=0,
    road_woods_ft=600,
    home_road_ft=2000,
)


def test_v6_is_v5_plus_privacy():
    assert V6.model_dump(exclude={"version", "overall"}) == V5.model_dump(
        exclude={"version", "overall"}
    )
    assert V6.overall.model_dump(exclude={"privacy"}) == V5.overall.model_dump(exclude={"privacy"})
    assert V5.overall.privacy is None and set(V6.overall.privacy) == set(
        scoring.SITE_FACTORS["privacy"]
    )


def test_v6_scores_privacy_and_no_part_is_left_out():
    site = replace(GOOD_SITE, open_cropland_acres=20, **SECLUDED)
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000, site=site)], V6)
    assert s.privacy_subscore == pytest.approx(100)  # no building within 2 miles: far end
    assert s.overall["left_out"] == [] and "left out" not in s.summary
    privacy = next(p for p in s.overall["parts"] if p["part"] == "privacy")
    assert privacy["weight"] == pytest.approx(0.1)
    near = next(f for f in privacy["factors"] if f["factor"] == "nearest_building_ft")
    assert "no other building within the search radius" in near["reason"]


def test_v6_crowded_site_scores_low_and_missing_privacy_is_left_out():
    crowded = replace(
        GOOD_SITE,
        buildings_quarter_mile=15,
        nearest_building_ft=150,
        buildings_per_sqmi=120,
        road_woods_ft=0,
        home_road_ft=80,
    )
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000, site=crowded)], V6)
    assert s.privacy_subscore == 0
    # Privacy not computed: left out and the rest rescaled, as in v5.
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000, site=GOOD_SITE)], V6)
    assert s.privacy_subscore is None and "privacy" in s.overall["left_out"]
    # v5 never scores privacy, even with the data.
    [s] = scoring.score_all([corridor(head_ft=200, watts=3000, site=crowded)], V5)
    assert s.privacy_subscore is None and s.overall["left_out"][0] == "privacy"
