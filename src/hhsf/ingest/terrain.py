"""Terrain: the USGS 3DEP 1/3 arc-second (~10 m) DEM under the AOI's streams.

USGS publishes it as 1x1 degree cloud-optimized GeoTIFFs (about 470 MB each for
Tennessee). Those files are tiled internally, so instead of downloading whole
tiles this reads just the part under the AOI's streams over HTTP (range
requests) and saves it as one GeoTIFF in data/raw/usgs_3dep_13/<aoi>.tif. The
analysis samples elevations along each reach from that file (analysis/stretch.py).
"""

import math
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import rasterio
from rasterio.merge import merge
from rasterio.transform import Affine

from hhsf.core.config import DATA_DIR, DataSource, load_data_source

SOURCE_KEY = "usgs_3dep_13"

# Read a little past the streams' extent so a reach's ends are never on the edge (~500 m).
PAD_DEG = 0.005

# GDAL settings for reading remote GeoTIFFs: don't list the S3 folder, retry hiccups.
_REMOTE = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
}

Bounds = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax in degrees


def dem_path(aoi_key: str) -> Path:
    return DATA_DIR / "raw" / SOURCE_KEY / f"{aoi_key}.tif"


def padded(bounds: Bounds, pad: float = PAD_DEG) -> Bounds:
    xmin, ymin, xmax, ymax = bounds
    return xmin - pad, ymin - pad, xmax + pad, ymax + pad


def tile_names(bounds: Bounds) -> list[str]:
    """The 1x1 degree tiles covering `bounds`, e.g. ["n36w086"] (North America only).

    Tiles are named by their north-west corner: n36w086 spans 35-36 N and 85-86 W.
    """
    xmin, ymin, xmax, ymax = bounds
    if xmax >= 0 or ymin < 0:
        raise ValueError("3DEP tile names are only worked out for the western/northern hemispheres")
    return [
        f"n{lat + 1:02d}w{-lon:03d}"
        for lat in range(math.floor(ymin), math.floor(ymax) + 1)
        for lon in range(math.floor(xmin), math.floor(xmax) + 1)
    ]


def tile_url(source: DataSource, name: str) -> str:
    return f"{source.url}{name}/USGS_13_{name}.tif"


def tile_info(url: str) -> tuple[int, str]:
    """(size in bytes, last-modified date YYYY-MM-DD) of a tile, without downloading it."""
    response = httpx.head(url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    modified = response.headers.get("last-modified")
    date = parsedate_to_datetime(modified).date().isoformat() if modified else "unknown"
    return int(response.headers.get("content-length", 0)), date


def share_of_tile(bounds: Bounds, name: str) -> float:
    """Fraction of the tile's area inside `bounds` (to estimate how much is read)."""
    north, west = int(name[1:3]), -int(name[4:7])
    xmin, ymin, xmax, ymax = bounds
    width = max(0.0, min(xmax, west + 1) - max(xmin, west))
    height = max(0.0, min(ymax, north) - max(ymin, north - 1))
    return width * height


def snap(bounds: Bounds, transform: Affine) -> Bounds:
    """Grow bounds out to the tile's cell edges, so cells are copied as they are.

    (Bounds off the cell grid would make the merge shift every cell a fraction.)
    """
    xmin, ymin, xmax, ymax = bounds
    x0, y0, rx, ry = transform.c, transform.f, transform.a, -transform.e
    return (
        x0 + math.floor((xmin - x0) / rx) * rx,
        y0 - math.ceil((y0 - ymin) / ry) * ry,
        x0 + math.ceil((xmax - x0) / rx) * rx,
        y0 - math.floor((y0 - ymax) / ry) * ry,
    )


def covers(path: Path, bounds: Bounds) -> bool:
    """True if the saved DEM already covers `bounds` (so it can be reused)."""
    if not path.exists():
        return False
    with rasterio.open(path) as dem:
        b, cell = dem.bounds, max(dem.res)
    # The saved edges snap to the tile's cell grid, so allow one cell of slack.
    xmin, ymin, xmax, ymax = padded(bounds, -cell)
    return b.left <= xmin and b.bottom <= ymin and b.right >= xmax and b.top >= ymax


def fetch_dem(
    aoi_key: str, stream_bounds: Bounds, echo: Callable[[str], None] = print
) -> tuple[Path, DataSource]:
    """Save the DEM under the streams (plus PAD_DEG) to data/raw/, once.

    Returns the file and the data source record (its version names the tiles used
    and their last-modified dates).
    """
    source = load_data_source(SOURCE_KEY)
    bounds = padded(stream_bounds)
    path = dem_path(aoi_key)
    if covers(path, bounds):
        with rasterio.open(path) as dem:
            version = dem.tags().get("source_version", source.version)
        echo(f"Using the saved DEM {path.name} ({path.stat().st_size / 1e6:.0f} MB).")
        return path, source.model_copy(update={"version": version})

    names = tile_names(bounds)
    urls = [tile_url(source, n) for n in names]
    dates = []
    for name, url in zip(names, urls, strict=True):
        size, date = tile_info(url)
        dates.append(date)
        share = share_of_tile(bounds, name)
        echo(
            f"Reading about {size * share / 1e6:.0f} MB of 3DEP tile {name} "
            f"({size / 1e6:.0f} MB in all, updated {date}): only the part under the streams."
        )
    tiles = ", ".join(f"{n} {d}" for n, d in zip(names, dates, strict=True))
    version = f"{source.version} ({tiles})"

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.stem + ".part.tif")
    sources = [f"/vsicurl/{u}" if u.startswith("http") else u for u in urls]
    with rasterio.Env(**_REMOTE):
        with rasterio.open(sources[0]) as first:
            bounds = snap(bounds, first.transform)
        merge(
            sources,
            bounds=bounds,
            dst_path=partial,
            dst_kwds={
                "driver": "GTiff",
                "compress": "deflate",
                "predictor": 3,  # floating-point predictor: much smaller elevation files
                "tiled": True,
                "blockxsize": 512,
                "blockysize": 512,
            },
        )
    with rasterio.open(partial, "r+") as dem:
        dem.update_tags(source_version=version, source_tiles=",".join(urls))
    # Rename only when complete, so an interrupted read is never mistaken for a good file.
    partial.rename(path)
    echo(f"Saved {path.name} ({path.stat().st_size / 1e6:.0f} MB).")
    return path, source.model_copy(update={"version": version})
