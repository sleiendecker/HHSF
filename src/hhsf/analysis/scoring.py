"""Hydro scoring and ranking: absolute, profile-driven, explained factor by factor.

Pure functions, no database, so every score can be unit-tested and reproduced.
The profile (config/scoring_profiles/<version>.yaml) holds every breakpoint and
weight; docs/SCORING.md explains the rules (site-selection.md §3.1, D-01, D-09, D-11).

A corridor ends up in exactly one status:
- scored: firm power at or above the target; hydro score from power and head.
- head_only: no flow because the basin is too small for the regression (D-09);
  no hydro score, ranked on its head sub-score after every power-scored corridor.
- below_power_target: firm power below the D-01 target; filtered out.
- below_min_head: head-only corridor too low to run a small turbine; filtered out.
- not_scorable: head or flow missing for some other reason; filtered out.
"""

from dataclasses import dataclass, field

import yaml
from pydantic import BaseModel, model_validator

from hhsf.core import units
from hhsf.core.config import CONFIG_DIR

SCORED = "scored"
HEAD_ONLY = "head_only"
BELOW_POWER_TARGET = "below_power_target"
BELOW_MIN_HEAD = "below_min_head"
NOT_SCORABLE = "not_scorable"
RANKED = (SCORED, HEAD_ONLY)

TIERS = {"medium", "low", HEAD_ONLY}


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

    @model_validator(mode="after")
    def _check(self) -> "ScoringProfile":
        if sorted(self.rank_tiers) != sorted(TIERS):
            raise ValueError(f"rank_tiers must list each of {sorted(TIERS)} once")
        return self


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

    name: str  # "head" or "power"
    input: str  # what was measured, e.g. "net_head_ft"
    value: float
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
            "value": round(self.value, 2),
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


@dataclass
class Score:
    corridor_id: int
    status: str
    tier: str | None  # medium | low | head_only; None when filtered out
    net_head_ft: float | None
    head_subscore: float | None = None
    power_subscore: float | None = None
    hydro_score: float | None = None  # only for status "scored"
    factors: list[Factor] = field(default_factory=list)
    summary: str = ""
    rank: int | None = None  # 1 = best, across all tiers
    tier_rank: int | None = None  # 1 = best within its tier

    @property
    def sort_score(self) -> float:
        """What orders corridors inside a tier: hydro score, or head for head-only."""
        if self.hydro_score is not None:
            return self.hydro_score
        return self.head_subscore or 0.0

    def explanation(self, p: ScoringProfile) -> dict:
        return {
            "profile": p.version,
            "status": self.status,
            "tier": self.tier,
            "summary": self.summary,
            "factors": [f.explain() for f in self.factors],
        }


def score_corridor(c: CorridorInputs, p: ScoringProfile) -> Score:
    """Filter, then score one corridor against the profile (D-01, D-09, D-11)."""
    if c.net_head_m is None:
        return Score(c.corridor_id, NOT_SCORABLE, None, None, summary="No head estimate.")
    head_ft = units.m_to_ft(c.net_head_m)
    target_kw = p.power_target_w / 1000

    if c.firm_power_w is not None:
        if c.firm_power_w < p.power_target_w:
            return Score(
                c.corridor_id,
                BELOW_POWER_TARGET,
                None,
                head_ft,
                summary=f"{c.firm_power_w:,.0f} W firm power is below the {target_kw:g} kW target.",
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
                f"and no check against the {target_kw:g} kW target. Ranked on head "
                f"{head.subscore:.1f} after every corridor with a power estimate. "
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


# --- Ranking ------------------------------------------------------------------


def rank(scores: list[Score], p: ScoringProfile) -> list[Score]:
    """Number the ranked corridors in place; returns them best first.

    Every corridor in an earlier tier ranks above every corridor in a later one
    (profile `rank_tiers`), so a low-confidence corridor can never outrank a
    medium one on score alone. Ties break on net head, then corridor id, so the
    order is always the same.
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
    rank(scores, p)
    return scores
