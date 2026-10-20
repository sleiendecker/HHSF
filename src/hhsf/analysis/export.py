"""Exports for a score run: shortlist CSV + GeoJSON, every corridor as CSV, a report.

Pure functions, no database: export_run.py loads the rows and hands them over, so
every column and every line of the report can be unit-tested. Values are in units
people read (ft, W, cfs and gpm, sq mi, mi), rounded to what the estimates can carry.
"""

import csv
import io
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hhsf.analysis import scoring, stretch
from hhsf.core import units

SHORTLIST_CSV = "shortlist.csv"
SHORTLIST_GEOJSON = "shortlist.geojson"
ALL_CORRIDORS_CSV = "all_corridors.csv"
REPORT_MD = "report.md"

FT_PER_MI = 5280
# The report sits in data/outputs/<aoi>/run_<id>/; links climb back to the repo root.
DOCS = "../../../../docs"
TIER_TITLES = {
    "medium": "Medium confidence (StreamStats Q90)",
    "low": "Low confidence (gauge transfer, regional yield or other fallback flow)",
    scoring.HEAD_ONLY: "Head only (basin too small for a flow estimate)",
}
STATUS_TITLES = {
    scoring.SCORED: "Scored (firm power at or above the target)",
    scoring.HEAD_ONLY: "Head only (no flow estimate, ranked on head)",
    scoring.BELOW_POWER_TARGET: "Filtered: firm power below the target",
    scoring.BELOW_MIN_HEAD: "Filtered: head only, but too little head",
    scoring.NOT_SCORABLE: "Filtered: head or flow missing",
}

SHORTLIST_COLUMNS = [
    "rank",
    "tier",
    "tier_rank",
    "hydro_score",
    "power_subscore",
    "head_subscore",
    "net_head_ft",
    "firm_power_w",
    "firm_flow_cfs",
    "firm_flow_gpm",
    "flow_method",
    "flow_confidence",
    "flow_flags",
    "drainage_area_sqmi",
    "corridor_length_ft",
    "reach_length_mi",
    "stream_name",
    "nhdplusid",
    "outlet_lat",
    "outlet_lon",
    "explanation",
]
ALL_CORRIDORS_COLUMNS = ["status", *SHORTLIST_COLUMNS]


@dataclass(frozen=True)
class ExportRow:
    """One corridor of a score run, joined to its hydro numbers (storage units)."""

    corridor_id: int
    nhdplusid: int
    stream_name: str | None
    status: str
    tier: str | None
    rank: int | None
    tier_rank: int | None
    hydro_score: float | None
    head_subscore: float | None
    power_subscore: float | None
    net_head_ft: float | None
    firm_power_w: float | None
    firm_flow_cfs: float | None
    flow_method: str | None
    flow_confidence: str
    flow_flags: tuple[str, ...]
    drainage_area_sqkm: float | None
    length_m: float
    outlet_lon: float | None
    outlet_lat: float | None
    explanation: dict
    geometry: dict | None = None  # GeoJSON, EPSG:4326
    reach_length_m: float | None = None  # the whole reach; length_m is the corridor (stretch)

    @property
    def ranked(self) -> bool:
        return self.rank is not None


@dataclass(frozen=True)
class RunInfo:
    """Provenance of a score run and the hydro run it scored."""

    aoi_key: str
    score_run_id: int
    hydro_run_id: int
    score_code_version: str
    hydro_code_version: str
    scoring_profile_version: str
    finished_at: str
    data_source_versions: dict
    hydro_params: dict
    score_params: dict
    aoi_name: str | None = None
    exported_with: str | None = None  # code version of the exporter


# --- Rows ---------------------------------------------------------------------


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def record(r: ExportRow) -> dict:
    """One corridor as readable, rounded values (None where there's no estimate)."""
    flow = r.firm_flow_cfs
    return {
        "status": r.status,
        "rank": r.rank,
        "tier": r.tier,
        "tier_rank": r.tier_rank,
        "hydro_score": _round(r.hydro_score, 1),
        "power_subscore": _round(r.power_subscore, 1),
        "head_subscore": _round(r.head_subscore, 1),
        "net_head_ft": _round(r.net_head_ft, 0),
        "firm_power_w": _round(r.firm_power_w, 0),
        "firm_flow_cfs": _round(flow, 3),
        "firm_flow_gpm": None if flow is None else round(units.cfs_to_gpm(flow)),
        "flow_method": r.flow_method,
        "flow_confidence": r.flow_confidence,
        "flow_flags": "; ".join(r.flow_flags),
        "drainage_area_sqmi": (
            None
            if r.drainage_area_sqkm is None
            else round(units.sqkm_to_sqmi(r.drainage_area_sqkm), 2)
        ),
        "corridor_length_ft": round(units.m_to_ft(r.length_m)),
        "reach_length_mi": round(units.m_to_ft(r.reach_length_m or r.length_m) / FT_PER_MI, 2),
        "stream_name": r.stream_name,
        "nhdplusid": r.nhdplusid,
        "outlet_lat": _round(r.outlet_lat, 5),
        "outlet_lon": _round(r.outlet_lon, 5),
        "explanation": r.explanation.get("summary", ""),
    }


def shortlist(rows: list[ExportRow]) -> list[ExportRow]:
    """Ranked corridors only, best first."""
    return sorted((r for r in rows if r.ranked), key=lambda r: r.rank)


def all_corridors(rows: list[ExportRow]) -> list[ExportRow]:
    """Ranked corridors in rank order, then the filtered ones grouped by status."""
    return sorted(
        rows,
        key=lambda r: (not r.ranked, r.rank or 0, r.status, -(r.firm_power_w or 0), r.nhdplusid),
    )


def to_csv(rows: list[ExportRow], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: "" if v is None else v for k, v in record(r).items()})
    return out.getvalue()


def to_geojson(rows: list[ExportRow], info: RunInfo) -> dict:
    return {
        "type": "FeatureCollection",
        "name": f"hhsf_{info.aoi_key}_score_run_{info.score_run_id}",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [
            {
                "type": "Feature",
                "id": r.corridor_id,
                "geometry": r.geometry,
                "properties": {k: record(r)[k] for k in SHORTLIST_COLUMNS},
            }
            for r in rows
        ],
    }


# --- Report -------------------------------------------------------------------


def _fmt(value: float | None, spec: str = ",.0f", unit: str = "") -> str:
    return "–" if value is None else f"{value:{spec}}{unit}"


def assumptions(info: RunInfo) -> list[tuple[str, str]]:
    """The assumptions behind the numbers, read from the two runs' params."""
    h = info.hydro_params.get("hydro", {})
    ss = h.get("streamstats", {})
    gauge = info.hydro_params.get("reference_gauge")
    p = info.score_params.get("scoring_profile", {})
    weights = p.get("weights", {})
    penstock_ft = h.get("corridor", {}).get("penstock_length_ft")
    items = [
        (
            "Corridor: longest penstock (ft, D-08)",
            _fmt(penstock_ft) if stretches(info) else "whole stream reach (no elevation model)",
        ),
        ("System efficiency (water to electricity, D-06)", h.get("system_efficiency")),
        ("Net head as a share of gross head (D-07)", h.get("net_head_factor")),
        ("Share of firm flow that can be diverted (D-05)", h.get("diversion_fraction")),
        ("Firm flow statistic (D-04)", ss.get("statistic_code")),
        ("StreamStats regression regions", ", ".join(ss.get("regression_regions", [])) or None),
        (
            "Smallest basin the low-flow equations apply to (sq mi, D-09)",
            ss.get("min_drainage_area_sqmi"),
        ),
        ("StreamStats used in this run", info.hydro_params.get("use_streamstats")),
    ]
    if gauge:
        items.append(
            (
                "Reference gauge for the drainage-area fallback",
                f"USGS {gauge.get('site_no')}: Q90 {gauge.get('q90_cfs')} cfs over "
                f"{gauge.get('drainage_area_sqmi')} sq mi "
                f"({' to '.join(gauge.get('period', []))})",
            )
        )
    else:
        items.append(("Reference gauge for the drainage-area fallback", "none"))
    if p.get("power_target_w") is not None:
        items.append(
            ("Power target, below which corridors drop (D-01)", f"{p['power_target_w']:,.0f} W")
        )
    if weights:
        items.append(
            ("Score weights (D-11)", f"power {weights.get('power')}, head {weights.get('head')}")
        )
    for name in ("power_subscore", "head_subscore"):
        if name in p:
            items.append(
                (
                    f"{name.replace('_', ' ').capitalize()} breakpoints [input, score]",
                    json.dumps(p[name].get("breakpoints")),
                )
            )
    if "head_only" in p:
        items.append(
            (
                "Smallest net head for a head-only corridor (ft)",
                p["head_only"].get("min_net_head_ft"),
            )
        )
    if "rank_tiers" in p:
        items.append(("Ranking tiers, in order", " > ".join(p["rank_tiers"])))
    return [(label, _text(v)) for label, v in items]


def _text(v: object) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def stretches(info: RunInfo) -> bool:
    """True if the hydro run's corridors are steepest stretches (D-08), not whole reaches."""
    return info.hydro_params.get("corridor_method") == stretch.HEAD_METHOD


def _corridor_caveat(info: RunInfo) -> str:
    if not stretches(info):
        return (
            "**Head and power are for the whole stream reach.** This run didn't use the "
            "elevation model, so power assumes a penstock the length of the reach. Long "
            "reaches are overstated; check the drop over a practical pipe length on the visit."
        )
    ft = info.hydro_params.get("hydro", {}).get("corridor", {}).get("penstock_length_ft")
    return (
        f"**Head is over the steepest {_fmt(ft)} ft of each stream reach** (D-08), from the "
        "USGS 10 m elevation model, measured along the stream. Flow is the whole reach's, at "
        "its downstream end, so a stretch near the top of a long reach has a little less. "
        "The coordinates are the bottom of the stretch, where a turbine would go. Confirm "
        "the drop on the visit: 10 m elevations miss small falls and can blur narrow ravines."
    )


def caveats(info: RunInfo) -> list[str]:
    return [
        _corridor_caveat(info),
        "**Gauge-based flows are an upper bound.** Flows scaled from the reference gauge "
        "(`flow_method = gauge_drainage_area_ratio`) came out about 10× StreamStats' Q90 "
        "where both exist, and NHDPlus mean flows (`nhdplus_erom`) by over 100×. Treat "
        "low-tier power as optimistic and measure dry-season flow.",
        "**Small basins use a regional yield.** Basins too small for the low-flow equations "
        "(flag `below_regression_min_drainage_area`) outside the gauge's basin get the "
        "median StreamStats Q90 per square mile of their HUC8 "
        "(`flow_method = regional_streamstats_yield`). It's an extrapolation, so it is low "
        "confidence; it misses springs, which a visit can find.",
        "**Head-only corridors have no flow estimate.** Their basins are too small for the "
        "low-flow equations and had no regional yield to borrow, so they haven't been checked "
        f"against the {_target(info)} target at all. They are ranked on head alone and need a "
        "flow check (springs, dry-season visit).",
        "StreamStats Q90 itself is a regression estimate (roughly ±60%), and nothing here "
        "covers permits, access, ownership or soils.",
    ]


def _target(info: RunInfo) -> str:
    w = info.score_params.get("scoring_profile", {}).get("power_target_w")
    return "power" if w is None else f"{w / 1000:g} kW"


def _table(header: list[str], lines: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    out += ["| " + " | ".join(cells) + " |" for cells in lines]
    return out


def _name(r: ExportRow) -> str:
    return (r.stream_name or "(unnamed)").replace("|", "/")


def _place(r: ExportRow) -> str:
    if r.outlet_lat is None or r.outlet_lon is None:
        return "–"
    return f"{r.outlet_lat:.5f}, {r.outlet_lon:.5f}"


def tier_table(rows: list[ExportRow], tier: str) -> list[str]:
    if tier == scoring.HEAD_ONLY:
        header = [
            "#",
            "Rank",
            "Stream",
            "Head score",
            "Net head (ft)",
            "Drainage (sq mi)",
            "Outlet (lat, lon)",
            "NHDPlus ID",
        ]
        lines = [
            [
                str(r.tier_rank),
                str(r.rank),
                _name(r),
                _fmt(r.head_subscore, ".1f"),
                _fmt(r.net_head_ft),
                _fmt(record(r)["drainage_area_sqmi"], ".2f"),
                _place(r),
                str(r.nhdplusid),
            ]
            for r in rows
        ]
    else:
        header = [
            "#",
            "Rank",
            "Stream",
            "Score",
            "Net head (ft)",
            "Firm power (W)",
            "Firm flow (cfs)",
            "Flow method",
            "Outlet (lat, lon)",
            "NHDPlus ID",
        ]
        lines = [
            [
                str(r.tier_rank),
                str(r.rank),
                _name(r),
                _fmt(r.hydro_score, ".1f"),
                _fmt(r.net_head_ft),
                _fmt(r.firm_power_w),
                _fmt(r.firm_flow_cfs, ".3g"),
                r.flow_method or "–",
                _place(r),
                str(r.nhdplusid),
            ]
            for r in rows
        ]
    return _table(header, lines)


def render_report(info: RunInfo, rows: list[ExportRow], top_n: int = 20) -> str:
    """The summary report for planning visits, as Markdown."""
    status = Counter(r.status for r in rows)
    tiers = Counter(r.tier for r in rows if r.ranked)
    ranked = shortlist(rows)
    order = info.score_params.get("scoring_profile", {}).get("rank_tiers") or list(TIER_TITLES)
    area = f"{info.aoi_name} (`{info.aoi_key}`)" if info.aoi_name else f"`{info.aoi_key}`"

    out = [
        f"# Hydro shortlist: {info.aoi_name or info.aoi_key}",
        "",
        f"{len(ranked)} of {len(rows)} stream reaches made the list. Read it tier by tier: "
        "every medium-confidence site ranks above every low-confidence one, and head-only "
        f"sites come last. How scores and tiers work: [docs/SCORING.md]({DOCS}/SCORING.md). "
        f"How head, flow and power are estimated: [docs/ASSUMPTIONS.md]({DOCS}/ASSUMPTIONS.md).",
        "",
        "## Run",
        "",
        *_table(
            ["", ""],
            [
                ["Area", area],
                ["Score run", f"{info.score_run_id} (finished {info.finished_at})"],
                ["Hydro run it scored", str(info.hydro_run_id)],
                ["Scoring profile", f"`{info.scoring_profile_version}`"],
                ["Code version (score run)", f"`{info.score_code_version}`"],
                ["Code version (hydro run)", f"`{info.hydro_code_version}`"],
                *([["Exported with", f"`{info.exported_with}`"]] if info.exported_with else []),
            ],
        ),
        "",
        "### Data sources",
        "",
        *_table(
            ["Source", "Version"],
            [[k, str(v)] for k, v in sorted(info.data_source_versions.items())],
        ),
        "",
        "## Key assumptions",
        "",
        "Read from this run's saved settings (`analysis_runs.params`).",
        "",
        *_table(["Assumption", "Value"], [[a, v] for a, v in assumptions(info)]),
        "",
        "## Counts",
        "",
        *_table(
            ["Status", "Corridors"],
            [[STATUS_TITLES.get(s, s), str(status[s])] for s in STATUS_TITLES if status[s]]
            + [[s, str(n)] for s, n in sorted(status.items()) if s not in STATUS_TITLES]
            + [["**Total**", f"**{len(rows)}**"]],
        ),
        "",
        *_table(
            ["Ranked tier", "Corridors"],
            [[TIER_TITLES.get(t, t), str(tiers[t])] for t in order],
        ),
        "",
        "## Caveats",
        "",
        *[f"- {c}" for c in caveats(info)],
        "",
    ]
    for tier in order:
        in_tier = [r for r in ranked if r.tier == tier]
        shown = in_tier[:top_n]
        title = TIER_TITLES.get(tier, tier)
        out += [f"## Top {len(shown)} of {len(in_tier)}: {title}" if shown else f"## {title}", ""]
        out += [*tier_table(shown, tier), ""] if shown else ["None in this run.", ""]
    out += [
        "## Files",
        "",
        f"- `{SHORTLIST_CSV}`: every ranked corridor, in rank order.",
        f"- `{SHORTLIST_GEOJSON}`: the same, with each reach's line (EPSG:4326), for GIS "
        "or Google Earth.",
        f"- `{ALL_CORRIDORS_CSV}`: every corridor, filtered ones included, with the reason "
        'in `explanation`. Use it to answer "why isn\'t this stream on the list?".',
        "",
        "The outlet is the downstream end of the reach, where a powerhouse would sit. "
        "Unnamed streams can be found by NHDPlus ID or by the outlet point.",
        "",
    ]
    return "\n".join(out)


# --- Files --------------------------------------------------------------------


def write_exports(out_dir: Path, info: RunInfo, rows: list[ExportRow]) -> list[Path]:
    """Write the four export files; returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = shortlist(rows)
    files = {
        SHORTLIST_CSV: to_csv(ranked, SHORTLIST_COLUMNS),
        SHORTLIST_GEOJSON: json.dumps(to_geojson(ranked, info)) + "\n",
        ALL_CORRIDORS_CSV: to_csv(all_corridors(rows), ALL_CORRIDORS_COLUMNS),
        REPORT_MD: render_report(info, rows),
    }
    paths = []
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths
