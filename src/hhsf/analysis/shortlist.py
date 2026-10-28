"""The parcel shortlist (PART 23): one row per turbine parcel, ranked by the hydro score.

The scores are per stretch and per county: each county's latest score run ranks its own
stretches, so the map shows a "#1" per county, and a big parcel can hold dozens of
stretches. The owner wants one list of parcels, whittled down by filters (buildable land,
drive time, ...) in the map app, with what's left "racked and stacked" on the hydro score.

- One row per turbine parcel, keyed on the parcel's own county and parcel ID (parcel IDs
  repeat across counties; a stretch analyzed for one county can end on a parcel in the
  next, and two counties' runs can share a parcel). Only ranked stretches count.
- The hydro value of a stretch is the hydro part of its overall score: the hydro score for
  1 kW+ stretches, the head sub-score for head-only ones, and for low-flow ones the hydro
  formula with the power curve extended below the target (0 W -> 0, 1 kW -> 50), so every
  group is on one 0-100 scale (docs/SCORING.md, "The overall score").
- Each parcel is represented by its best stretch: the highest hydro value among the
  stretches of its best power group (1 kW+, then head only, then low flow), so a 1 kW
  stretch is never listed behind a low-flow estimate on the same parcel (a 160 W stretch
  with a big drop can score higher on the extended curve).
- Parcels are ranked by that stretch's hydro value across every county, best first; ties
  break on firm power, then net head. Not by power group: a low-flow parcel with a big drop
  can rank above a 1 kW one, so the group stays a column, a badge and a filter. The overall
  score is kept as a secondary column.

Pure functions apart from `load` and `save`, so the grouping and ranking are unit-tested.
No owner names or addresses: the list is served to the map app (migration 0020).
"""

import json
from dataclasses import dataclass, field

from sqlalchemy import Connection, text

from hhsf.analysis import export, hydro, subdivision
from hhsf.core import units

KW = "kw"
HEAD_ONLY = "head_only"
LOW_FLOW = "low_flow"
# Best first. hydro_scores tiers: medium and low are both 1 kW or more (D-01).
GROUP_ORDER = (KW, HEAD_ONLY, LOW_FLOW)
TIER_GROUP = {"medium": KW, "low": KW, "head_only": HEAD_ONLY, "low_flow": LOW_FLOW}

# Flags the shortlist adds to the exports' (hhsf.analysis.export).
LOW_FLOW_ESTIMATE = "low_flow_estimate"  # a typical-basin number: can't see springs
LOW_CONFIDENCE_FLOW = "low_confidence_flow"  # 1 kW+ on a rough flow estimate
NO_FLOW_ESTIMATE = "no_flow_estimate"  # head only
DEED_ACRES_DIFFER = "deed_acres_differ"  # the deed and the outline disagree by over 25%
DEED_ACRES_TOLERANCE = 0.25
# Georgia (D-20): the buildable acres don't rule out soils Very limited for septic.
SEPTIC_NOT_SCREENED = "septic_not_screened"
# Flow flags worth repeating; the regional-yield basin is in every low-flow row.
FLOW_FLAGS = {
    "streamstats_drainage_area_mismatch",
    "streamstats_pour_point_moved",
    "missing_drainage_area",
    "below_regression_min_drainage_area",
    hydro.REGIONAL_YIELD_BORROWED,
}


@dataclass(frozen=True)
class Stretch:
    """A ranked stretch of the map's saved layer (latest_hydro_scores) with a turbine parcel."""

    corridor_id: int
    aoi_key: str
    score_run_id: int
    county_geoid: str  # the turbine parcel's
    parcel_id: str
    gislink: str
    county_name: str | None
    tier: str
    overall_score: float | None
    hydro_score: float | None
    hydro_part: float | None = None  # see the module docstring; None: no overall score
    nhdplusid: int | None = None
    stream_name: str | None = None
    firm_power_w: float | None = None
    net_head_ft: float | None = None
    buildability_subscore: float | None = None
    agriculture_subscore: float | None = None
    privacy_subscore: float | None = None
    logistics_subscore: float | None = None
    acres: float | None = None
    deed_acres: float | None = None
    turbine_parcel_distance_m: float | None = None
    buildable_acres: float | None = None
    no_soil_acres: float | None = None
    septic_screened: bool | None = None  # False: Very limited soils counted (D-20)
    drive_minutes: float | None = None
    in_drive_area: bool | None = None
    turbine_road_m: float | None = None
    turbine_public_road_m: float | None = None
    turbine_in_sfha: bool | None = None
    stretch_sfha_m: float | None = None
    open_acres: float | None = None
    open_cropland_acres: float | None = None
    prime_acres: float | None = None  # prime and statewide-importance farmland
    buildings_quarter_mile: int | None = None
    road_frontage_m: float | None = None
    nearest_building_m: float | None = None
    parcel_buildings: int | None = None
    subdivision_score: float | None = None
    subdivision_flags: tuple[str, ...] = ()
    flow_flags: tuple[str, ...] = ()
    parts: tuple[dict, ...] = ()  # hydro_scores.explanation["overall"]["parts"]
    left_out: tuple[str, ...] = ()
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)

    @property
    def group(self) -> str:
        return TIER_GROUP[self.tier]

    @property
    def hydro(self) -> float | None:
        """The 0-100 hydro value the list ranks on (the hydro score where there's no part)."""
        return self.hydro_part if self.hydro_part is not None else self.hydro_score


@dataclass(frozen=True)
class Entry:
    """One parcel of the shortlist."""

    rank: int
    best: Stretch
    stretches: int
    max_power_w: float | None
    bbox: tuple[float, float, float, float]
    flags: tuple[str, ...] = ()
    checks: tuple[dict, ...] = field(default=())

    @property
    def key(self) -> tuple[str, str]:
        return self.best.county_geoid, self.best.parcel_id


def _hydro_order(s: Stretch) -> tuple:
    """Best first on hydro: the hydro value, then firm power, then net head."""
    return (
        -(s.hydro if s.hydro is not None else -1),
        -(s.firm_power_w if s.firm_power_w is not None else -1),
        -(s.net_head_ft if s.net_head_ft is not None else -1),
    )


def _best_first(s: Stretch) -> tuple:
    """Sort key within a parcel: best power group, then hydro."""
    return (GROUP_ORDER.index(s.group), *_hydro_order(s), s.corridor_id)


def _rank_key(e: tuple[Stretch, list[Stretch]]) -> tuple:
    """Across parcels: hydro only, whatever the group (then the key, so it's stable)."""
    s = e[0]
    return (*_hydro_order(s), s.county_geoid, s.parcel_id)


def build(stretches: list[Stretch], min_acres: float = 10) -> list[Entry]:
    """Group ranked stretches by turbine parcel and rank the parcels, best first."""
    by_parcel: dict[tuple[str, str], list[Stretch]] = {}
    for s in stretches:
        by_parcel.setdefault((s.county_geoid, s.parcel_id), []).append(s)
    grouped = []
    for group in by_parcel.values():
        group.sort(key=_best_first)
        grouped.append((group[0], group))
    grouped.sort(key=_rank_key)
    entries = []
    for i, (best, group) in enumerate(grouped, 1):
        powers = [s.firm_power_w for s in group if s.firm_power_w is not None]
        # Two counties' runs can score the same stream: count it once.
        streams = {s.nhdplusid if s.nhdplusid is not None else -s.corridor_id for s in group}
        fl = flags(best, min_acres)
        entries.append(
            Entry(
                rank=i,
                best=best,
                stretches=len(streams),
                max_power_w=max(powers) if powers else None,
                bbox=best.bbox,
                flags=fl,
                checks=checks(best, fl),
            )
        )
    return entries


def flags(s: Stretch, min_acres: float = 10) -> tuple[str, ...]:
    """What to check about the parcel's best stretch, as short codes (exports' where shared)."""
    out = []
    if s.group == LOW_FLOW:
        out.append(LOW_FLOW_ESTIMATE)
    elif s.group == HEAD_ONLY:
        out.append(NO_FLOW_ESTIMATE)
    elif s.tier == "low":
        out.append(LOW_CONFIDENCE_FLOW)
    out += [f for f in s.flow_flags if f in FLOW_FLAGS]
    if s.turbine_parcel_distance_m:
        out.append(export.OUTSIDE_PARCEL)
    if s.acres is not None and s.acres < min_acres:
        out.append(export.UNDER_MIN_ACRES)
    if s.acres and s.deed_acres and abs(s.deed_acres - s.acres) > DEED_ACRES_TOLERANCE * s.acres:
        out.append(DEED_ACRES_DIFFER)
    if s.turbine_in_sfha:
        out.append(export.TURBINE_IN_SFHA)
    elif s.stretch_sfha_m:
        out.append(export.STRETCH_IN_SFHA)
    if s.in_drive_area is False:
        out.append(export.OUTSIDE_DRIVE_AREA)
    if s.drive_minutes is None:
        out.append(export.NO_DRIVE_TIME)
    if (
        s.turbine_road_m is not None
        and s.turbine_public_road_m is not None
        and s.turbine_road_m < s.turbine_public_road_m
    ):
        out.append(export.ROAD_NOT_PUBLIC)
    if s.buildable_acres == 0:
        out.append(export.NO_BUILDABLE_GROUND)
    if s.no_soil_acres is not None and s.no_soil_acres >= 0.5:
        out.append(export.SOILS_NOT_LOADED)
    if s.septic_screened is False:
        out.append(SEPTIC_NOT_SCREENED)
    if s.nearest_building_m is not None and s.nearest_building_m <= export.NEIGHBOR_CLOSE_M:
        out.append(export.NEIGHBOR_CLOSE)
    if s.parcel_buildings:
        out.append(export.BUILDINGS_ON_PARCEL)
    out += [f for f in s.subdivision_flags if f not in out]
    return tuple(out)


def _ft(m: float) -> str:
    return f"{units.m_to_ft(m):,.0f} ft"


def _acres(acres: float | None, kind: str) -> str:
    """'1 buildable acre', '12 buildable acres', or 'Some buildable acres' when unknown."""
    if acres is None:
        return f"Some {kind} acres"
    n = f"{acres:,.0f}"
    return f"{n} {kind} acre" + ("" if n == "1" else "s")


def checks(s: Stretch, fl: tuple[str, ...]) -> tuple[dict, ...]:
    """The "check on the visit" list: one entry per topic, worded from the flags.

    `warn` marks the ones the data already points at; the rest are checks every site needs.
    """
    out = []

    def add(topic: str, warn: bool, what: str) -> None:
        out.append({"topic": topic, "warn": warn, "text": what})

    borrowed = (
        " It's borrowed from Tennessee streams in the same basin, across the state line."
        if hydro.REGIONAL_YIELD_BORROWED in fl
        else ""
    )
    if s.group == LOW_FLOW:
        add(
            "Dry-season flow",
            True,
            "The power is a regional estimate for a typical basin this size, and it can't see "
            f"springs.{borrowed} Gauge the flow in late summer or early fall, at its lowest.",
        )
    elif s.group == HEAD_ONLY:
        add("Dry-season flow", True, "No flow estimate. Measure the flow in late summer or fall.")
    else:
        add(
            "Dry-season flow",
            s.tier == "low" or any(f in FLOW_FLAGS for f in fl),
            "1 kW or more on the estimated low flow (the flow 90% of days beat)."
            f"{borrowed} Measure it in late summer or fall to confirm.",
        )
    head = f"{s.net_head_ft:,.0f} ft" if s.net_head_ft is not None else "The"
    add(
        "Drop",
        False,
        f"{head} net head is read off a 10 m elevation model. Measure the drop along the "
        "penstock route (GPS altimeter, or a hose and gauge).",
    )
    deed = "Check the deed, easements and the owner at the county register of deeds."
    if export.OUTSIDE_PARCEL in fl:
        deed += " The turbine end falls in a gap between parcels (a road or river bed)."
    if DEED_ACRES_DIFFER in fl:
        deed += f" The deed says {s.deed_acres:,.0f} acres, the outline {s.acres:,.0f}."
    if subdivision.STRETCH_CROSSES_PARCELS in fl:
        deed += " The stretch crosses other parcels: more than one owner."
    add(
        "Deed and owner",
        any(
            f in fl
            for f in (export.OUTSIDE_PARCEL, DEED_ACRES_DIFFER, subdivision.STRETCH_CROSSES_PARCELS)
        ),
        deed,
    )
    if export.NO_BUILDABLE_GROUND in fl:
        add(
            "Septic soils",
            True,
            "No buildable ground found on the parcel (gentle, dry, soils fit for septic). "
            "Look for a house site and ask about a perc test.",
        )
    elif SEPTIC_NOT_SCREENED in fl:
        add(
            "Septic soils",
            True,
            f"{_acres(s.buildable_acres, 'gentle')} out of the floodplain, but not screened "
            "for septic: Georgia's soil survey has only the national rating, which rules out "
            "about twice the ground Tennessee's does. Ask for a perc test early.",
        )
    elif export.SOILS_NOT_LOADED in fl:
        add(
            "Septic soils",
            True,
            "Part of the parcel has no soil survey loaded. Ask for a perc test.",
        )
    else:
        add(
            "Septic soils",
            False,
            f"{_acres(s.buildable_acres, 'buildable')} on the soil survey's septic rating "
            "(1:24,000). A perc test decides.",
        )
    road = []
    if subdivision.NO_ROAD_FRONTAGE in fl:
        road.append("no public road frontage")
    if export.ROAD_NOT_PUBLIC in fl:
        road.append("the nearest road is a trail or private")
    if subdivision.LONG_DRIVEWAY in fl:
        road.append("a long driveway")
    if export.OUTSIDE_DRIVE_AREA in fl:
        road.append("over an hour from downtown Chattanooga")
    if road:
        add(
            "Road access",
            True,
            f"{(r := '; '.join(road))[0].upper()}{r[1:]}. Check the legal access and the road in.",
        )
    else:
        add("Road access", False, "Check the legal access and the road in.")
    if export.TURBINE_IN_SFHA in fl or export.STRETCH_IN_SFHA in fl:
        add(
            "Flooding",
            True,
            "Part of the stretch is in FEMA's 100-year floodplain. Keep the house out of it.",
        )
    return tuple(out)


# --- Database ------------------------------------------------------------------

LOAD_SQL = """
    SELECT l.corridor_id, l.aoi_key, l.score_run_id, p.county_geoid,
           l.turbine_parcel_id AS parcel_id, l.turbine_parcel_gislink AS gislink,
           p.county_name, l.tier, l.overall_score, l.hydro_score, l.nhdplusid,
           l.stream_name, l.firm_power_w, l.net_head_ft, l.buildability_subscore,
           l.agriculture_subscore, l.privacy_subscore, l.logistics_subscore,
           l.turbine_parcel_acres AS acres, l.turbine_parcel_deed_acres AS deed_acres,
           l.turbine_parcel_distance_m, l.turbine_buildable_acres AS buildable_acres,
           b.no_soil_acres, 'Very limited' = ANY(b.septic_excluded) AS septic_screened,
           l.drive_minutes, l.in_drive_area, l.turbine_road_m,
           l.turbine_public_road_m, l.turbine_in_sfha, l.stretch_sfha_m,
           l.turbine_open_acres AS open_acres,
           l.turbine_open_cropland_acres AS open_cropland_acres,
           l.turbine_prime_acres + coalesce(l.turbine_statewide_acres, 0) AS prime_acres,
           l.buildings_quarter_mile, l.road_frontage_m, l.nearest_building_m, l.parcel_buildings,
           l.subdivision_score, l.subdivision_flags, l.flow_flags,
           h.explanation -> 'overall' AS overall,
           ARRAY[ST_XMin(e.box), ST_YMin(e.box), ST_XMax(e.box), ST_YMax(e.box)] AS bbox
    FROM latest_hydro_scores l
    JOIN parcels p ON p.gislink = l.turbine_parcel_gislink
    LEFT JOIN hydro_scores h ON h.run_id = l.score_run_id AND h.corridor_id = l.corridor_id
    LEFT JOIN parcel_buildable b ON b.gislink = l.turbine_parcel_gislink
    CROSS JOIN LATERAL (SELECT ST_Envelope(ST_Collect(p.geom, l.geom)) AS box) e
    WHERE l.rank IS NOT NULL
"""


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(v for v in (value or "").split(",") if v)


def load(conn: Connection) -> list[Stretch]:
    """Every ranked stretch of the map's saved layer that ends on a parcel."""
    out = []
    for r in conn.execute(text(LOAD_SQL)).mappings():
        row = dict(r)
        overall = row.pop("overall") or {}
        row["subdivision_flags"] = _split(row["subdivision_flags"])
        row["flow_flags"] = _split(row["flow_flags"])
        row["bbox"] = tuple(row["bbox"])
        parts = tuple(overall.get("parts", ()))
        out.append(
            Stretch(
                **row,
                hydro_part=_part(parts, "hydro"),
                parts=parts,
                left_out=tuple(overall.get("left_out", ())),
            )
        )
    return out


def _part(parts: tuple[dict, ...], name: str) -> float | None:
    return next((p["subscore"] for p in parts if p["part"] == name), None)


def _explained(s: Stretch) -> dict:
    """The overall score's parts for the detail card: each factor's reason, no breakpoints."""
    return {
        "parts": [
            {
                "part": p["part"],
                "subscore": p["subscore"],
                "weight": p["weight"],
                "contribution": p["contribution"],
                "factors": [
                    {k: f.get(k) for k in ("factor", "reason", "subscore", "weight")}
                    for f in p.get("factors", ())
                ],
                "missing": p.get("missing", []),
            }
            for p in s.parts
        ],
        "left_out": list(s.left_out),
    }


INSERT_SQL = """
    INSERT INTO parcel_shortlist (county_geoid, parcel_id, gislink, county_name, rank,
        overall_score, hydro_subscore, buildability_subscore, agriculture_subscore,
        privacy_subscore, logistics_subscore, power_group, tier, corridor_id, aoi_key,
        score_run_id, stream_name, firm_power_w, max_power_w, net_head_ft, stretches, acres,
        deed_acres, buildable_acres, drive_minutes, open_acres, open_cropland_acres,
        prime_acres, buildings_quarter_mile, road_frontage_ft, public_road_ft, turbine_in_sfha,
        subdivision_score, flags, checks, parts, bbox)
    VALUES (:county_geoid, :parcel_id, :gislink, :county_name, :rank, :overall_score,
        :hydro_subscore, :buildability_subscore, :agriculture_subscore, :privacy_subscore,
        :logistics_subscore, :power_group, :tier, :corridor_id, :aoi_key, :score_run_id,
        :stream_name, :firm_power_w, :max_power_w, :net_head_ft, :stretches, :acres,
        :deed_acres, :buildable_acres, :drive_minutes, :open_acres, :open_cropland_acres,
        :prime_acres, :buildings_quarter_mile, :road_frontage_ft, :public_road_ft,
        :turbine_in_sfha, :subdivision_score, :flags, CAST(:checks AS jsonb),
        CAST(:parts AS jsonb), :bbox)
"""


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _r_ft(m: float | None) -> float | None:
    return None if m is None else round(units.m_to_ft(m))


def row(e: Entry) -> dict:
    """The parcel_shortlist row, rounded to what the estimates carry (it's served whole)."""
    s = e.best
    return {
        "county_geoid": s.county_geoid,
        "parcel_id": s.parcel_id,
        "gislink": s.gislink,
        "county_name": s.county_name,
        "rank": e.rank,
        "overall_score": _r(s.overall_score),
        # What the list ranks on: the hydro part (see the module docstring).
        "hydro_subscore": _r(s.hydro),
        "buildability_subscore": _r(s.buildability_subscore),
        "agriculture_subscore": _r(s.agriculture_subscore),
        "privacy_subscore": _r(s.privacy_subscore),
        "logistics_subscore": _r(s.logistics_subscore),
        "power_group": s.group,
        "tier": s.tier,
        "corridor_id": s.corridor_id,
        "aoi_key": s.aoi_key,
        "score_run_id": s.score_run_id,
        "stream_name": s.stream_name,
        "firm_power_w": _r(s.firm_power_w, 0),
        "max_power_w": _r(e.max_power_w, 0),
        "net_head_ft": _r(s.net_head_ft),
        "stretches": e.stretches,
        "acres": _r(s.acres),
        "deed_acres": _r(s.deed_acres),
        "buildable_acres": _r(s.buildable_acres),
        "drive_minutes": _r(s.drive_minutes),
        "open_acres": _r(s.open_acres),
        "open_cropland_acres": _r(s.open_cropland_acres),
        "prime_acres": _r(s.prime_acres),
        "buildings_quarter_mile": s.buildings_quarter_mile,
        "road_frontage_ft": _r_ft(s.road_frontage_m),
        "public_road_ft": _r_ft(s.turbine_public_road_m),
        "turbine_in_sfha": s.turbine_in_sfha,
        "subdivision_score": _r(s.subdivision_score),
        "flags": list(e.flags),
        "checks": json.dumps(list(e.checks)),
        "parts": json.dumps(_explained(s)),
        "bbox": [round(v, 5) for v in e.bbox],
    }


def save(conn: Connection, entries: list[Entry]) -> None:
    """Replace the shortlist. DELETE, not TRUNCATE, so the map app keeps reading the old
    rows until this commits. The research status is in its own table and is kept."""
    conn.execute(text("DELETE FROM parcel_shortlist"))
    if entries:
        conn.execute(text(INSERT_SQL), [row(e) for e in entries])


def refresh(conn: Connection, min_acres: float = 10) -> list[Entry]:
    """Rebuild the shortlist from the map's saved layer (run after it's refreshed)."""
    entries = build(load(conn), min_acres)
    save(conn, entries)
    return entries
