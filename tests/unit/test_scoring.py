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
