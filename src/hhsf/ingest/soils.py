"""Soils from USDA NRCS SSURGO, through Soil Data Access (SDA), for septic suitability.

SSURGO maps soils as map units: polygons that each stand for a mix of soil
components (say 80% Lily loam, 15% Ramsey, 5% rock outcrop). Ratings like "septic
tank absorption fields" are given per component; a map unit's rating here is its
dominant condition, as Web Soil Survey shows it by default: the rating held by the
largest share of the map unit, the more limiting one on a tie. See
docs/ASSUMPTIONS.md, "Buildable ground".

SDA answers SQL over HTTP. Per survey area (one per county here: Sequatchie is
TN153) the ingest asks for the map units, their components' septic ratings, and the
polygons in pages, each cached in data/raw/usda_ssurgo/<aoi>/. A county's soils are
replaced as a whole on every ingest.
"""

import json
import shutil
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import shapely
from shapely.geometry import MultiPolygon
from sqlalchemy import Connection, text

from hhsf.core.config import AOI, DATA_DIR, data_source_settings, load_data_source
from hhsf.core.db import record_source
from hhsf.ingest.parcels import as_multipolygon

SOURCE_KEY = "usda_ssurgo"
TIMEOUT_S = 300
RETRIES = 4

NOT_RATED = "Not rated"
# Rating classes, least to most limiting. The Tennessee rule says slightly/moderately,
# the national ones not/somewhat; both end in "Very limited".
LIMITATION_ORDER = {
    NOT_RATED: -1,
    "Not limited": 0,
    "Slightly limited": 0,
    "Somewhat limited": 1,
    "Moderately limited": 1,
    "Very limited": 2,
}
# SSURGO names counties by state postal code + county FIPS. Only the states near the
# AOIs are listed; add more as needed.
STATE_ABBR = {"01": "AL", "13": "GA", "21": "KY", "37": "NC", "47": "TN"}


@dataclass(frozen=True)
class ComponentRating:
    mukey: str
    comppct: float  # the component's share of the map unit, percent
    rule: str  # SSURGO interpretation name (mrulename)
    rating: str  # interphrc: Very limited, Somewhat limited, ...


@dataclass(frozen=True)
class MapUnit:
    mukey: str  # SSURGO's map unit key, unique nationally
    areasymbol: str  # the survey area: TN153
    musym: str | None  # the map unit's label on the soil map: 'LnC'
    muname: str | None  # 'Lily loam, 5 to 12 percent slopes'
    hydric_pct: float | None  # share of the map unit that's hydric soil
    bedrock_min_cm: float | None  # shallowest depth to bedrock among its components
    water_table_min_cm: float | None  # shallowest annual water table
    flooding: str | None  # flooding frequency, dominant condition: None, Rare, Occasional, ...
    drainage: str | None  # drainage class, dominant condition: Well drained, ...
    ratings: dict[str, tuple[str, float]]  # column -> (dominant rating, its share in percent)


@dataclass(frozen=True)
class SoilPolygon:
    mupolygonkey: int
    mukey: str
    areasymbol: str
    geom: MultiPolygon


# --- Soil Data Access ------------------------------------------------------------


def _post(query: str) -> list[dict[str, Any]]:
    """Rows of one SDA query, as dicts keyed by column name."""
    url = data_source_settings(SOURCE_KEY)["url"]
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            response = httpx.post(
                url, json={"format": "JSON+COLUMNNAME", "query": query}, timeout=TIMEOUT_S
            )
            response.raise_for_status()
            if not response.content.strip():
                return []  # SDA answers an empty body when nothing matches
            table = response.json().get("Table", [])
            if not table:
                return []
            columns, *rows = table
            return [dict(zip(columns, row, strict=True)) for row in rows]
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < RETRIES:
                time.sleep(5 * attempt)
    raise RuntimeError(f"Soil Data Access query failed after {RETRIES} attempts") from last_error


def _cached(path: Path, query: str) -> list[dict[str, Any]]:
    if path.exists():
        return json.loads(path.read_text())
    rows = _post(query)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    partial.write_text(json.dumps(rows))
    partial.rename(path)  # only complete answers are cached
    return rows


def _quote(value: str) -> str:
    """A string literal for SDA's SQL (values here come from config and SDA itself)."""
    return "'" + value.replace("'", "''") + "'"


def survey_areas_query(county_geoid: str) -> str:
    """The soil survey areas covering a county (SDA names counties 'TN153' too)."""
    county = f"{STATE_ABBR[county_geoid[:2]]}{county_geoid[2:]}"
    return f"""
        SELECT l.areasymbol, l.areaname, sc.saversion, sc.saverest
        FROM laoverlap lo
        JOIN legend l ON l.lkey = lo.lkey
        JOIN sacatalog sc ON sc.areasymbol = l.areasymbol
        WHERE lo.areatypename = 'County or Parish' AND lo.areasymbol = {_quote(county)}
        ORDER BY l.areasymbol
    """


def map_units_query(areasymbol: str) -> str:
    return f"""
        SELECT mu.mukey, l.areasymbol, mu.musym, mu.muname, ma.hydclprs, ma.brockdepmin,
               ma.wtdepannmin, ma.flodfreqdcd, ma.drclassdcd
        FROM legend l
        JOIN mapunit mu ON mu.lkey = l.lkey
        LEFT JOIN muaggatt ma ON ma.mukey = mu.mukey
        WHERE l.areasymbol = {_quote(areasymbol)}
        ORDER BY mu.mukey
    """


def ratings_query(areasymbol: str, rules: Iterable[str]) -> str:
    """Every component's top-level rating (ruledepth 0) under each rule."""
    names = ", ".join(_quote(r) for r in rules)
    return f"""
        SELECT c.mukey, c.cokey, c.comppct_r, ci.mrulename, ci.interphrc
        FROM legend l
        JOIN mapunit mu ON mu.lkey = l.lkey
        JOIN component c ON c.mukey = mu.mukey
        JOIN cointerp ci ON ci.cokey = c.cokey AND ci.ruledepth = 0
        WHERE l.areasymbol = {_quote(areasymbol)} AND ci.mrulename IN ({names})
        ORDER BY c.mukey, c.cokey
    """


def polygons_query(areasymbol: str, after: int, page_size: int) -> str:
    """One page of polygons, keyed and ordered by mupolygonkey (SDA has no offsets)."""
    return f"""
        SELECT TOP {page_size} mupolygonkey, mukey, mupolygongeo.STAsText() AS wkt
        FROM mupolygon
        WHERE areasymbol = {_quote(areasymbol)} AND mupolygonkey > {after}
        ORDER BY mupolygonkey
    """


# --- Parsing ---------------------------------------------------------------------


def _float(value: Any) -> float | None:
    return None if value in (None, "") else float(value)


def _text(value: Any) -> str | None:
    s = " ".join(str(value).split()) if value is not None else ""
    return s or None


def dominant_condition(ratings: Iterable[tuple[str, float]]) -> tuple[str, float]:
    """The rating held by the largest share of a map unit, and that share in percent.

    `ratings` are (rating, component percent). Shares of the same rating add up; on a
    tie the more limiting rating wins (Web Soil Survey's "dominant condition", ties
    broken toward "higher"). No rated components: Not rated, 0%.
    """
    shares: dict[str, float] = defaultdict(float)
    for rating, pct in ratings:
        shares[rating] += pct
    if not shares:
        return NOT_RATED, 0.0
    rating = max(shares, key=lambda r: (shares[r], LIMITATION_ORDER.get(r, 0)))
    return rating, shares[rating]


def parse_ratings(rows: Iterable[dict[str, Any]]) -> list[ComponentRating]:
    return [
        ComponentRating(
            mukey=str(r["mukey"]),
            comppct=_float(r["comppct_r"]) or 0.0,
            rule=r["mrulename"],
            rating=_text(r["interphrc"]) or NOT_RATED,
        )
        for r in rows
    ]


def parse_map_units(
    rows: Iterable[dict[str, Any]], ratings: list[ComponentRating], rules: dict[str, str]
) -> list[MapUnit]:
    """Map units with each rule's dominant rating. `rules`: column -> SSURGO rule name."""
    by_unit: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for c in ratings:
        by_unit[(c.mukey, c.rule)].append((c.rating, c.comppct))
    units = []
    for r in rows:
        mukey = str(r["mukey"])
        units.append(
            MapUnit(
                mukey=mukey,
                areasymbol=r["areasymbol"],
                musym=_text(r["musym"]),
                muname=_text(r["muname"]),
                hydric_pct=_float(r["hydclprs"]),
                bedrock_min_cm=_float(r["brockdepmin"]),
                water_table_min_cm=_float(r["wtdepannmin"]),
                flooding=_text(r["flodfreqdcd"]),
                drainage=_text(r["drclassdcd"]),
                ratings={
                    column: dominant_condition(by_unit[(mukey, rule)])
                    for column, rule in rules.items()
                },
            )
        )
    return units


def parse_polygon(row: dict[str, Any], areasymbol: str) -> SoilPolygon:
    return SoilPolygon(
        mupolygonkey=int(row["mupolygonkey"]),
        mukey=str(row["mukey"]),
        areasymbol=areasymbol,
        geom=as_multipolygon(shapely.from_wkt(row["wkt"])),
    )


# --- Fetch and load --------------------------------------------------------------


def septic_rules() -> dict[str, str]:
    """Soils table column -> SSURGO septic rule name (config/data_sources.yaml)."""
    return dict(data_source_settings(SOURCE_KEY)["septic_rules"])


def cache_dir(aoi: AOI) -> Path:
    return DATA_DIR / "raw" / SOURCE_KEY / aoi.key


def fetch_soils(
    aoi: AOI, refresh: bool = False, echo: Callable[[str], None] = print
) -> tuple[list[MapUnit], list[SoilPolygon], dict[str, str]]:
    """The county's map units and polygons, and each survey area's version and date
    ({'TN153': 'TN153 version 20 (8/28/2026)'})."""
    folder = cache_dir(aoi)
    if refresh and folder.exists():
        shutil.rmtree(folder)
    page_size = data_source_settings(SOURCE_KEY).get("page_size", 1000)
    rules = septic_rules()
    areas = _cached(folder / "survey_areas.json", survey_areas_query(aoi.county_geoid))
    if not areas:
        raise RuntimeError(f"Soil Data Access has no soil survey for {aoi.name}.")
    units: list[MapUnit] = []
    polygons: list[SoilPolygon] = []
    for area in areas:
        symbol = area["areasymbol"]
        echo(f"  {symbol} {area['areaname']} (version {area['saversion']}, {area['saverest']})")
        unit_rows = _cached(folder / symbol / "map_units.json", map_units_query(symbol))
        rating_rows = _cached(
            folder / symbol / "ratings.json", ratings_query(symbol, rules.values())
        )
        units += parse_map_units(unit_rows, parse_ratings(rating_rows), rules)
        after, page = 0, 0
        while True:
            rows = _cached(
                folder / symbol / f"polygons_{page:04d}.json",
                polygons_query(symbol, after, page_size),
            )
            polygons += [parse_polygon(r, symbol) for r in rows]
            if len(rows) < page_size:
                break
            after, page = int(rows[-1]["mupolygonkey"]), page + 1
        echo(f"  {len(polygons):,} soil polygons so far")
    versions = {
        a["areasymbol"]: f"{a['areasymbol']} version {a['saversion']} ({a['saverest'].split()[0]})"
        for a in areas
    }
    return units, polygons, versions


def load_soils(
    conn: Connection,
    aoi: AOI,
    units: list[MapUnit],
    polygons: list[SoilPolygon],
    versions: dict[str, str],
) -> int:
    """Replace the county's soil polygons. Returns the number loaded.

    Map unit attributes are copied onto each polygon: there are only a few hundred map
    units, and one flat table is simpler to serve as map tiles. Large polygons are cut
    into pieces of at most 256 corners (as flood zones are) so lookups stay quick.
    """
    if not polygons:
        raise RuntimeError(f"Soil Data Access returned no soil polygons for {aoi.name}.")
    source = load_data_source(SOURCE_KEY)
    # One data_sources row for all counties; each polygon keeps its own survey's version.
    record_source(
        conn,
        source.model_copy(update={"version": f"{source.version}: {'; '.join(versions.values())}"}),
    )
    conn.execute(text("DELETE FROM soils WHERE county_geoid = :g"), {"g": aoi.county_geoid})
    by_key = {u.mukey: u for u in units}
    rows = []
    for p in polygons:
        u = by_key.get(p.mukey)
        if u is None:
            raise RuntimeError(f"Soil polygon {p.mupolygonkey} has unknown map unit {p.mukey}")
        septic_tn, septic_tn_pct = u.ratings.get("septic_tn", (NOT_RATED, 0.0))
        septic_national, septic_national_pct = u.ratings.get("septic_national", (NOT_RATED, 0.0))
        rows.append(
            {
                "mupolygonkey": p.mupolygonkey,
                "mukey": u.mukey,
                "areasymbol": u.areasymbol,
                "county_geoid": aoi.county_geoid,
                "survey_version": versions[p.areasymbol],
                "musym": u.musym,
                "muname": u.muname,
                "septic_tn": septic_tn,
                "septic_tn_pct": septic_tn_pct,
                "septic_national": septic_national,
                "septic_national_pct": septic_national_pct,
                "hydric_pct": u.hydric_pct,
                "bedrock_min_cm": u.bedrock_min_cm,
                "water_table_min_cm": u.water_table_min_cm,
                "flooding": u.flooding,
                "drainage": u.drainage,
                "wkb": p.geom.wkb,
                "source_key": SOURCE_KEY,
            }
        )
    conn.execute(
        text(
            """
            INSERT INTO soils (mupolygonkey, mukey, areasymbol, county_geoid, survey_version,
                               musym, muname,
                               septic_tn, septic_tn_pct, septic_national, septic_national_pct,
                               hydric_pct, bedrock_min_cm, water_table_min_cm, flooding,
                               drainage, geom, source_key)
            SELECT :mupolygonkey, :mukey, :areasymbol, :county_geoid, :survey_version,
                   :musym, :muname,
                   :septic_tn, :septic_tn_pct, :septic_national, :septic_national_pct,
                   :hydric_pct, :bedrock_min_cm, :water_table_min_cm, :flooding, :drainage,
                   ST_Multi(piece), :source_key
            FROM ST_Subdivide(ST_GeomFromWKB(:wkb, 4326), 256) AS piece
            """
        ),
        rows,
    )
    return len(rows)
