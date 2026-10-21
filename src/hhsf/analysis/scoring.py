"""Hydro scoring and ranking: absolute, profile-driven, explained factor by factor.

Pure functions, no database, so every score can be unit-tested and reproduced.
The profile (config/scoring_profiles/<version>.yaml) holds every breakpoint and
weight; docs/SCORING.md explains the rules (site-selection.md §3.1, D-01, D-09, D-11).

A corridor ends up in exactly one status:
- scored: firm power at or above the target; hydro score from power and head.
- head_only: no flow because the basin is too small for the regression (D-09);
  no hydro score, ranked on its head sub-score after every power-scored corridor.
- low_flow: firm power below the D-01 target, but on a low-confidence flow; only
  when the profile lists the low_flow tier (D-01, D-09). No hydro score, ranked
  by firm power after every other ranked corridor.
- below_power_target: firm power below the D-01 target; filtered out.
- below_min_head: head-only or low-flow corridor too low to run a small turbine;
  filtered out.
- not_scorable: head or flow missing for some other reason; filtered out.

With an `overall` section (hydro_v4 on, FR-7, D-15) every ranked corridor also gets
an overall score: the hydro part plus buildability, agriculture and logistics
sub-scores of its turbine parcel and turbine end, each made of factors with their own
curves. Parts and factors with no data are left out and the rest rescale (privacy
always, until FR-6 is built). Within each tier, corridors are then ranked by it.
"""

from dataclasses import dataclass, field

import yaml
from pydantic import BaseModel, model_validator

from hhsf.core import units
from hhsf.core.config import CONFIG_DIR

SCORED = "scored"
HEAD_ONLY = "head_only"
LOW_FLOW = "low_flow"
BELOW_POWER_TARGET = "below_power_target"
BELOW_MIN_HEAD = "below_min_head"
NOT_SCORABLE = "not_scorable"
RANKED = (SCORED, HEAD_ONLY, LOW_FLOW)

TIERS = {"medium", "low", HEAD_ONLY}  # every profile ranks these
OPTIONAL_TIERS = {LOW_FLOW}  # listing it in rank_tiers switches it on


class Curve(BaseModel):
    """A piecewise-linear sub-score: [input, score] points, flat beyond the ends."""

    breakpoints: list[tuple[float, float]]

    @model_validator(mode="after")
    def _check(self) -> "Curve":
        xs = [x for x, _ in self.breakpoints]
        if len(xs) < 2 or any(b <= a for a, b in zip(xs, xs[1:], strict=False)):
            raise ValueError("need at least two breakpoints with increasing inputs")
        if any(not 0 <= s <= 100 for _, s in self.breakpoints):
            raise ValueError("breakpoint scores must be between 0 and 100")
        return self


class Weights(BaseModel):
    power: float
    head: float

    @model_validator(mode="after")
    def _check(self) -> "Weights":
        if self.power < 0 or self.head < 0 or abs(self.power + self.head - 1) > 1e-9:
            raise ValueError("weights must be non-negative and add up to 1")
        return self


class SiteFactor(Curve):
    """One overall-score factor: a curve and its weight within its part."""

    weight: float


# The FR-7 parts, and the factors each site part can use (SiteInputs fields).
PARTS = ("hydro", "buildability", "agriculture", "privacy", "logistics")
SITE_FACTORS = {
    "buildability": ("buildable_acres", "nearest_buildable_ft", "public_road_ft"),
    "agriculture": ("farmland_acres", "cropland_acres", "pasture_aum"),
    "logistics": ("drive_minutes",),
}


def _adds_to_one(weights: list[float]) -> bool:
    return all(w >= 0 for w in weights) and abs(sum(weights) - 1) <= 1e-9


class Overall(BaseModel):
    weights: dict[str, float]
    low_flow_power_subscore: Curve
    buildability: dict[str, SiteFactor]
    agriculture: dict[str, SiteFactor]
    logistics: dict[str, SiteFactor]

    @model_validator(mode="after")
    def _check(self) -> "Overall":
        if set(self.weights) != set(PARTS) or not _adds_to_one(list(self.weights.values())):
            raise ValueError(f"overall weights must name {', '.join(PARTS)} and add up to 1")
        for part, allowed in SITE_FACTORS.items():
            factors = getattr(self, part)
            if not factors or not set(factors) <= set(allowed):
                raise ValueError(f"overall.{part} factors must be some of {', '.join(allowed)}")
            if not _adds_to_one([f.weight for f in factors.values()]):
                raise ValueError(f"overall.{part} factor weights must add up to 1")
        return self


class HeadOnly(BaseModel):
    min_net_head_ft: float


class ScoringProfile(BaseModel):
    version: str
    power_target_w: float
    weights: Weights
    head_subscore: Curve
    power_subscore: Curve
    head_only: HeadOnly
    rank_tiers: list[str]
    # hydro_v3: corridors of 1 kW or more below this net head are filtered too (a
    # big river with a foot of drop is no homestead site). None: only head_only and
    # low_flow corridors have a minimum (head_only.min_net_head_ft).
    min_net_head_ft: float | None = None
    # hydro_v4: the FR-7 overall score ranks corridors within each tier. None: they're
    # ranked by their hydro score (hydro_v1-v3).
    overall: Overall | None = None

    @model_validator(mode="after")
    def _check(self) -> "ScoringProfile":
        required = set(self.rank_tiers) - OPTIONAL_TIERS
        if len(set(self.rank_tiers)) != len(self.rank_tiers) or required != TIERS:
            raise ValueError(
                f"rank_tiers must list each of {sorted(TIERS)} once"
                f" (and optionally {sorted(OPTIONAL_TIERS)})"
            )
        return self

    @property
    def keeps_low_flow(self) -> bool:
        return LOW_FLOW in self.rank_tiers


def load_profile(version: str = "hydro_v1") -> ScoringProfile:
    path = CONFIG_DIR / "scoring_profiles" / f"{version}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in path.parent.glob("*.yaml"))
        raise FileNotFoundError(f"No scoring profile '{version}'. Available: {available}")
    profile = ScoringProfile.model_validate(yaml.safe_load(path.read_text()))
    if profile.version != version:
        raise ValueError(f"{path.name} says version '{profile.version}', expected '{version}'")
    return profile


# --- Sub-scores ---------------------------------------------------------------


def piecewise(x: float, curve: Curve) -> tuple[float, str]:
    """Score `x` on the curve. Returns (score, which part of the curve applied)."""
    points = curve.breakpoints
    (x0, s0), (xn, sn) = points[0], points[-1]
    if x <= x0:
        return s0, f"at or below {x0:g} -> {s0:g}"
    if x >= xn:
        return sn, f"at or above {xn:g} -> {sn:g}"
    for (xa, sa), (xb, sb) in zip(points, points[1:], strict=False):
        if xa <= x <= xb:
            score = sa + (sb - sa) * (x - xa) / (xb - xa)
            return score, f"between {xa:g} ({sa:g}) and {xb:g} ({sb:g}), linear -> {score:.1f}"
    raise AssertionError("unreachable: breakpoints are sorted")  # pragma: no cover


@dataclass(frozen=True)
class Factor:
    """One factor's part in a score, with everything needed to explain it."""

    name: str  # "head", "power", or an overall-score factor such as "buildable_acres"
    input: str  # what was measured, e.g. "net_head_ft"
    value: float | None  # None: nothing measured, scored as the far end (no buildable ground)
    breakpoints: list[tuple[float, float]]
    subscore: float
    weight: float
    reason: str

    @property
    def contribution(self) -> float:
        return self.subscore * self.weight

    def explain(self) -> dict:
        return {
            "factor": self.name,
            "input": self.input,
            "value": None if self.value is None else round(self.value, 2),
            "breakpoints": [list(p) for p in self.breakpoints],
            "subscore": round(self.subscore, 2),
            "weight": self.weight,
            "contribution": round(self.contribution, 2),
            "reason": self.reason,
        }


def head_factor(net_head_ft: float, p: ScoringProfile, weight: float) -> Factor:
    score, where = piecewise(net_head_ft, p.head_subscore)
    return Factor(
        "head",
        "net_head_ft",
        net_head_ft,
        p.head_subscore.breakpoints,
        score,
        weight,
        f"{net_head_ft:.0f} ft net head: {where}",
    )


def power_factor(firm_power_w: float, p: ScoringProfile) -> Factor:
    score, where = piecewise(firm_power_w, p.power_subscore)
    return Factor(
        "power",
        "firm_power_w",
        firm_power_w,
        p.power_subscore.breakpoints,
        score,
        p.weights.power,
        f"{firm_power_w:,.0f} W firm power: {where}",
    )


# --- One corridor -------------------------------------------------------------


@dataclass(frozen=True)
class CorridorInputs:
    """What scoring reads from one hydro_corridors row."""

    corridor_id: int
    net_head_m: float | None
    firm_power_w: float | None
    power_confidence: str  # medium | low | unavailable
    flow_confidence: str  # medium | low | below_applicability | unavailable
    flow_method: str | None = None
    flow_flags: tuple[str, ...] = ()
    site: "SiteInputs | None" = None  # for the overall score; None: no site data


@dataclass(frozen=True)
class SiteInputs:
    """What the overall score reads about a corridor's turbine parcel and turbine end.

    None means no data (the layer isn't loaded, the step wasn't run, no turbine
    parcel), and that factor is left out. Measured zeros are zeros.
    """

    buildable_acres: float | None = None
    nearest_buildable_ft: float | None = None  # None with buildable_acres 0: there's none
    public_road_ft: float | None = None
    farmland_acres: float | None = None  # prime + statewide importance
    cropland_acres: float | None = None  # capability class 1-3
    pasture_aum: float | None = None
    drive_minutes: float | None = None


@dataclass
class Score:
    corridor_id: int
    status: str
    tier: str | None  # medium | low | head_only | low_flow; None when filtered out
    net_head_ft: float | None
    head_subscore: float | None = None
    power_subscore: float | None = None
    hydro_score: float | None = None  # only for status "scored"
    factors: list[Factor] = field(default_factory=list)
    summary: str = ""
    rank: int | None = None  # 1 = best, across all tiers
    tier_rank: int | None = None  # 1 = best within its tier
    firm_power_w: float | None = None  # kept for ordering the low_flow tier
    overall_score: float | None = None  # FR-7; only with a profile's overall section
    buildability_subscore: float | None = None
    agriculture_subscore: float | None = None
    logistics_subscore: float | None = None
    overall: dict | None = None  # how the overall score was reached

    @property
    def sort_score(self) -> float:
        """What orders a tier: the overall score when there is one; else hydro score,
        head for head-only, watts for low flow."""
        if self.overall_score is not None:
            return self.overall_score
        if self.hydro_score is not None:
            return self.hydro_score
        if self.status == LOW_FLOW:
            return self.firm_power_w or 0.0
        return self.head_subscore or 0.0

    def explanation(self, p: ScoringProfile) -> dict:
        out = {
            "profile": p.version,
            "status": self.status,
            "tier": self.tier,
            "summary": self.summary,
            "factors": [f.explain() for f in self.factors],
        }
        if self.overall is not None:
            out["overall"] = self.overall
        return out


def score_corridor(c: CorridorInputs, p: ScoringProfile) -> Score:
    """Filter, then score one corridor against the profile (D-01, D-09, D-11)."""
    if c.net_head_m is None:
        return Score(c.corridor_id, NOT_SCORABLE, None, None, summary="No head estimate.")
    head_ft = units.m_to_ft(c.net_head_m)
    target_kw = p.power_target_w / 1000

    if c.firm_power_w is not None:
        if c.firm_power_w < p.power_target_w:
            if p.keeps_low_flow and c.power_confidence == "low":
                return low_flow_score(c, head_ft, p)
            return Score(
                c.corridor_id,
                BELOW_POWER_TARGET,
                None,
                head_ft,
                summary=f"{c.firm_power_w:,.0f} W firm power is below the {target_kw:g} kW target.",
            )
        if p.min_net_head_ft is not None and head_ft < p.min_net_head_ft:
            return Score(
                c.corridor_id,
                BELOW_MIN_HEAD,
                None,
                head_ft,
                summary=(
                    f"{c.firm_power_w:,.0f} W firm power, but {head_ft:.0f} ft net head is below "
                    f"{p.min_net_head_ft:g} ft."
                ),
            )
        power = power_factor(c.firm_power_w, p)
        head = head_factor(head_ft, p, p.weights.head)
        total = power.contribution + head.contribution
        tier = "medium" if c.power_confidence == "medium" else "low"
        basis = "StreamStats Q90" if tier == "medium" else (c.flow_method or "fallback flow")
        return Score(
            c.corridor_id,
            SCORED,
            tier,
            head_ft,
            head_subscore=head.subscore,
            power_subscore=power.subscore,
            hydro_score=total,
            factors=[power, head],
            summary=(
                f"Hydro score {total:.1f} = power {power.subscore:.1f} x {power.weight:g}"
                f" + head {head.subscore:.1f} x {head.weight:g}."
                f" {tier.capitalize()} confidence (flow from {basis})."
            ),
        )

    if c.flow_confidence == "below_applicability":
        if head_ft < p.head_only.min_net_head_ft:
            return Score(
                c.corridor_id,
                BELOW_MIN_HEAD,
                None,
                head_ft,
                summary=(
                    f"No flow estimate (basin too small) and {head_ft:.0f} ft net head is below "
                    f"{p.head_only.min_net_head_ft:g} ft."
                ),
            )
        # Ranked on head alone, so head carries the whole weight here.
        head = head_factor(head_ft, p, 1.0)
        return Score(
            c.corridor_id,
            HEAD_ONLY,
            HEAD_ONLY,
            head_ft,
            head_subscore=head.subscore,
            factors=[head],
            summary=(
                f"HEAD ONLY: basin too small for a flow estimate (D-09), so no hydro score "
                f"and no check against the {target_kw:g} kW target. Head sub-score "
                f"{head.subscore:.1f}; ranked after every corridor with a power estimate. "
                "Check flow on the visit."
            ),
        )

    return Score(
        c.corridor_id,
        NOT_SCORABLE,
        None,
        head_ft,
        summary=f"No flow estimate ({', '.join(c.flow_flags) or c.flow_confidence}).",
    )


def low_flow_score(c: CorridorInputs, head_ft: float, p: ScoringProfile) -> Score:
    """Below the target on a low-confidence flow: listed last, flagged LOW FLOW (D-01)."""
    target_kw = p.power_target_w / 1000
    basis = c.flow_method or "fallback flow"
    if head_ft < p.head_only.min_net_head_ft:
        return Score(
            c.corridor_id,
            BELOW_MIN_HEAD,
            None,
            head_ft,
            summary=(
                f"{c.firm_power_w:,.0f} W firm power on a low-confidence flow ({basis}), and "
                f"{head_ft:.0f} ft net head is below {p.head_only.min_net_head_ft:g} ft."
            ),
        )
    return Score(
        c.corridor_id,
        LOW_FLOW,
        LOW_FLOW,
        head_ft,
        firm_power_w=c.firm_power_w,
        summary=(
            f"LOW FLOW: {c.firm_power_w:,.0f} W firm power is below the {target_kw:g} kW target, "
            f"but the flow is a low-confidence estimate ({basis}), so it stays on the list (D-01). "
            "No hydro score; listed after every other ranked corridor. A spring or a "
            "dry-season measurement could change it."
        ),
    )


# --- Overall score (FR-7) -----------------------------------------------------

SITE_FACTOR_TEXT = {
    "buildable_acres": ("{:,.1f} acres of buildable ground", "no buildable ground computed"),
    "nearest_buildable_ft": ("nearest buildable ground {:,.0f} ft away", "no buildable ground"),
    "public_road_ft": ("public road {:,.0f} ft away", "no roads loaded"),
    "farmland_acres": ("{:,.1f} acres of prime farmland", "no farmland computed"),
    "cropland_acres": ("{:,.1f} acres of class 1-3 cropland", "no farmland computed"),
    "pasture_aum": ("pasture for {:,.0f} AUM a year", "no farmland computed"),
    "drive_minutes": ("{:,.0f} min drive", "no drive time"),
}


@dataclass(frozen=True)
class Part:
    """One FR-7 part of the overall score. `factors` carry weights rescaled over the
    factors that had data, so their contributions add up to `subscore`."""

    name: str
    subscore: float | None  # None: no data at all, left out
    factors: list[Factor]
    missing: list[str]  # factors left out for lack of data


def _rescaled(items: list[tuple[Factor, float]]) -> list[Factor]:
    total = sum(w for _, w in items)
    return [
        Factor(f.name, f.input, f.value, f.breakpoints, f.subscore, w / total, f.reason)
        for f, w in items
    ]


def site_part(name: str, spec: dict[str, SiteFactor], site: SiteInputs) -> Part:
    """A buildability, agriculture or logistics sub-score from the site inputs."""
    items, missing = [], []
    for factor, curve in spec.items():
        value = getattr(site, factor)
        found, _ = SITE_FACTOR_TEXT[factor]
        if value is not None:
            score, where = piecewise(value, curve)
            reason = f"{found.format(value)}: {where}"
        elif factor == "nearest_buildable_ft" and site.buildable_acres == 0:
            score = curve.breakpoints[-1][1]  # none at all scores as the far end
            reason = f"no buildable ground on the turbine parcel -> {score:g}"
        else:
            missing.append(factor)
            continue
        items.append(
            (Factor(factor, factor, value, curve.breakpoints, score, 1.0, reason), curve.weight)
        )
    if not items or sum(w for _, w in items) == 0:
        return Part(name, None, [], missing)
    factors = _rescaled(items)
    return Part(name, sum(f.contribution for f in factors), factors, missing)


def hydro_part(s: Score, p: ScoringProfile) -> Part:
    """The hydro score, the head sub-score for head-only corridors, or for low-flow
    ones the hydro formula with the power curve extended below the target."""
    assert p.overall is not None
    if s.status == SCORED:
        return Part("hydro", s.hydro_score, s.factors, [])
    if s.status == HEAD_ONLY:
        return Part("hydro", s.head_subscore, s.factors, [])
    curve = p.overall.low_flow_power_subscore
    score, where = piecewise(s.firm_power_w or 0.0, curve)
    power = Factor(
        "power",
        "firm_power_w",
        s.firm_power_w,
        curve.breakpoints,
        score,
        p.weights.power,
        f"{s.firm_power_w or 0:,.0f} W firm power (below the target): {where}",
    )
    head = head_factor(s.net_head_ft or 0.0, p, p.weights.head)
    return Part("hydro", power.contribution + head.contribution, [power, head], [])


def add_overall(s: Score, site: SiteInputs | None, p: ScoringProfile) -> None:
    """Give a ranked corridor its overall score, sub-scores and explanation, in place."""
    o = p.overall
    assert o is not None
    site = site or SiteInputs()
    parts = [hydro_part(s, p)] + [
        site_part(name, getattr(o, name), site)
        for name in ("buildability", "agriculture", "logistics")
    ]
    used = [(part, o.weights[part.name]) for part in parts if part.subscore is not None]
    total = sum(w for _, w in used)
    s.overall_score = sum(part.subscore * w for part, w in used) / total
    by_name = {part.name: part.subscore for part in parts}
    s.buildability_subscore = by_name["buildability"]
    s.agriculture_subscore = by_name["agriculture"]
    s.logistics_subscore = by_name["logistics"]
    left_out = ["privacy"] + [part.name for part in parts if part.subscore is None]
    s.overall = {
        "score": round(s.overall_score, 2),
        "parts": [
            {
                "part": part.name,
                "subscore": round(part.subscore, 2),
                "weight": round(w / total, 4),
                "contribution": round(part.subscore * w / total, 2),
                "factors": [f.explain() for f in part.factors],
                "missing": part.missing,
            }
            for part, w in used
        ],
        "left_out": left_out,
    }
    terms = " + ".join(f"{part.name} {part.subscore:.1f} x {w / total:.2f}" for part, w in used)
    s.summary += (
        f" Overall {s.overall_score:.1f} = {terms} ({', '.join(left_out)} left out: no data)."
    )


# --- Ranking ------------------------------------------------------------------


def rank(scores: list[Score], p: ScoringProfile) -> list[Score]:
    """Number the ranked corridors in place; returns them best first.

    Every corridor in an earlier tier ranks above every corridor in a later one
    (profile `rank_tiers`), so a low-confidence corridor can never outrank a
    medium one on score alone. Within a tier, the overall score orders them when
    there is one. Ties break on net head, then corridor id, so the order is always
    the same.
    """
    order = {t: i for i, t in enumerate(p.rank_tiers)}
    ranked = sorted(
        (s for s in scores if s.status in RANKED),
        key=lambda s: (order[s.tier], -s.sort_score, -(s.net_head_ft or 0), s.corridor_id),
    )
    within: dict[str, int] = {}
    for i, s in enumerate(ranked, 1):
        within[s.tier] = within.get(s.tier, 0) + 1
        s.rank, s.tier_rank = i, within[s.tier]
    return ranked


def score_all(corridors: list[CorridorInputs], p: ScoringProfile) -> list[Score]:
    """Score and rank every corridor. Returns all of them, filtered ones included."""
    scores = [score_corridor(c, p) for c in corridors]
    if p.overall is not None:
        for c, s in zip(corridors, scores, strict=True):
            if s.status in RANKED:
                add_overall(s, c.site, p)
    rank(scores, p)
    return scores
