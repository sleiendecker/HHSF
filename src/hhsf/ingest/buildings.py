"""Building footprints from Microsoft's Global ML Building Footprints.

Microsoft traced every building it could find in Bing aerial imagery with a machine
learning model and publishes the outlines as one file per web-map tile (a "quadkey",
zoom 9: about 40 x 50 miles here), listed in a CSV of links. In rural Tennessee this
is far more complete than OpenStreetMap, where many houses were never drawn. Each
outline is a polygon, sometimes with an estimated height; there's no use (house, barn,
shed), name or address. See docs/ASSUMPTIONS.md, "Privacy".

An AOI loads the buildings within 2 km of its county (so a house just over the county
line still counts as a neighbor) from every tile that touches it. Tiles are shared by
neighboring AOIs, so a tile is replaced as a whole: with the buildings within 2 km of
every AOI county loaded so far (keep_area). The tiles reach as far as Chattanooga;
its buildings aren't kept.
Downloads are cached in data/raw/ms_buildings/<release>/.
"""

import csv
import gzip
import io
import json
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import shapely
from shapely.geometry import MultiPolygon, shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, text

from hhsf.core import crs
from hhsf.core.config import DATA_DIR, DataSource, data_source_settings, load_data_source

SOURCE_KEY = "ms_buildings"
TIMEOUT_S = 300

Bounds = tuple[float, float, float, float]


@dataclass(frozen=True)
class TileFile:
    quadkey: str
    url: str
    release: str  # the dated folder in the URL, e.g. "2026-02-03"


@dataclass(frozen=True)
class Building:
    geom: MultiPolygon  # storage CRS
    area_m2: float
    height_m: float | None  # Microsoft's estimate; None where it has none (-1)


def _tile_xy(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    s = math.sin(math.radians(lat))
    n = 2**zoom
    x = int((lon + 180) / 360 * n)
    y = int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)
    return min(x, n - 1), min(y, n - 1)


def _key(x: int, y: int, zoom: int) -> str:
    return "".join(
        str((1 if x & (1 << i) else 0) + (2 if y & (1 << i) else 0))
        for i in range(zoom - 1, -1, -1)
    )


def quadkey(lon: float, lat: float, zoom: int) -> str:
    """The Bing Maps tile key of the web-map tile holding (lon, lat) at `zoom`."""
    return _key(*_tile_xy(lon, lat, zoom), zoom)


def tile_bounds(key: str) -> Bounds:
    """A tile's (lon, lat) bounds."""
    x = y = 0
    for digit in key:
        x, y = x * 2 + (int(digit) & 1), y * 2 + (int(digit) >> 1)
    n = 2 ** len(key)

    def lat(row: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * row / n))))

    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def quadkeys(bounds: Bounds, zoom: int) -> list[str]:
    """Every tile key at `zoom` touching `bounds` (lon/lat)."""
    x0, y1 = _tile_xy(bounds[0], bounds[1], zoom)  # tile rows count down from the north
    x1, y0 = _tile_xy(bounds[2], bounds[3], zoom)
    return sorted(_key(x, y, zoom) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def parse_links(csv_text: str, region: str) -> dict[str, list[TileFile]]:
    """The region's files in the dataset's link list, by quadkey (a tile can have several)."""
    out: dict[str, list[TileFile]] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        if row["Location"] != region:
            continue
        release = row["Url"].split("/global-buildings/", 1)[-1].split("/", 1)[0]
        out.setdefault(row["QuadKey"], []).append(TileFile(row["QuadKey"], row["Url"], release))
    return out


def links(refresh: bool = False) -> dict[str, list[TileFile]]:
    """The region's tile files, from the link list (cached in data/raw/ms_buildings/)."""
    s = data_source_settings(SOURCE_KEY)
    path = DATA_DIR / "raw" / SOURCE_KEY / "dataset-links.csv"
    if refresh or not path.exists():
        response = httpx.get(s["url"], timeout=TIMEOUT_S, follow_redirects=True)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        partial.write_bytes(response.content)
        partial.rename(path)
    return parse_links(path.read_text(), s["region"])


def download(tile: TileFile, refresh: bool = False) -> Path:
    """One tile file, downloaded once."""
    name = tile.url.rsplit("/", 1)[-1]
    path = DATA_DIR / "raw" / SOURCE_KEY / tile.release / f"{tile.quadkey}-{name}"
    if path.exists() and not refresh:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    with httpx.stream("GET", tile.url, timeout=TIMEOUT_S, follow_redirects=True) as r:
        r.raise_for_status()
        with partial.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    partial.rename(path)  # only complete files are cached
    return path


def read_tile(path: Path, keep: BaseGeometry | None = None) -> list[Building]:
    """The buildings in one tile file (gzipped GeoJSON, one feature per line); with
    `keep` (analysis CRS), only those touching it."""
    geoms, heights = [], []
    with gzip.open(path, "rt") as f:
        for line in f:
            if not line.strip():
                continue
            feature = json.loads(line)
            geom = shape(feature["geometry"])
            if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            geoms.append(MultiPolygon([geom]) if geom.geom_type == "Polygon" else geom)
            height = (feature.get("properties") or {}).get("height")
            heights.append(height if height is not None and height > 0 else None)
    if not geoms:
        return []
    projected = crs.to_analysis(np.array(geoms, dtype=object))
    inside = np.ones(len(geoms), bool)
    if keep is not None:
        shapely.prepare(keep)
        inside = shapely.intersects(keep, projected)
    areas = shapely.area(projected)
    return [
        Building(g, float(a), h)
        for g, a, h, i in zip(geoms, areas, heights, inside, strict=True)
        if i
    ]


def keep_area(conn: Connection, county: BaseGeometry) -> BaseGeometry:
    """Where buildings are kept: every AOI county loaded so far and `county` (storage
    CRS), `pad_m` around them, in the analysis CRS. A tile is reloaded whole by each
    AOI that touches it, so it keeps the buildings of every AOI loaded before."""
    rows = conn.execute(
        text("SELECT ST_AsBinary(geom) FROM counties WHERE aoi_key IS NOT NULL")
    ).scalars()
    counties = [county, *(shapely.from_wkb(bytes(wkb)) for wkb in rows)]
    pad = data_source_settings(SOURCE_KEY)["pad_m"]
    return crs.to_analysis(shapely.union_all(counties)).buffer(pad)


def fetch_buildings(
    keep: BaseGeometry, refresh: bool = False, echo: Callable[[str], None] = print
) -> tuple[Iterator[tuple[str, list[Building]]], DataSource]:
    """Each tile touching `keep` (analysis CRS, see keep_area) and its buildings that touch
    it, one tile at a time; and the data source record, naming the release."""
    s = data_source_settings(SOURCE_KEY)
    files = links(refresh)
    bounds = crs.to_storage(shapely.box(*keep.bounds)).bounds
    keys = [
        k
        for k in quadkeys(bounds, s["zoom"])
        if k in files and keep.intersects(crs.to_analysis(shapely.box(*tile_bounds(k))))
    ]
    releases = sorted({t.release for k in keys for t in files[k]})
    source = load_data_source(SOURCE_KEY)
    source = source.model_copy(
        update={"version": f"{source.version}, release {', '.join(releases)}"}
    )
    echo(f"  {len(keys)} tiles: {', '.join(keys)}")

    def tiles() -> Iterator[tuple[str, list[Building]]]:
        for key in keys:
            yield key, [b for t in files[key] for b in read_tile(download(t, refresh), keep)]

    return tiles(), source


def load_tile(conn: Connection, key: str, buildings: list[Building]) -> int:
    """Replace one tile's buildings. Returns how many were loaded."""
    conn.execute(text("DELETE FROM buildings WHERE quadkey = :k"), {"k": key})
    if not buildings:
        return 0
    cursor = conn.connection.dbapi_connection.cursor()  # same transaction as `conn`
    copy_sql = "COPY buildings (quadkey, area_m2, height_m, geom, source_key) FROM STDIN"
    with cursor.copy(copy_sql) as copy:
        for b in buildings:
            # PostGIS reads hex WKB with an SRID (EWKB) as a geometry's text form.
            geom = shapely.set_srid(b.geom, crs.STORAGE_EPSG)
            ewkb = shapely.to_wkb(geom, hex=True, include_srid=True)
            copy.write_row((key, b.area_m2, b.height_m, ewkb, SOURCE_KEY))
    return len(buildings)
