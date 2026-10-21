"""Land cover and tree canopy from the USGS National Land Cover Database (NLCD).

Two 30 m rasters for 2021 from MRLC's coverage service (WCS), both already in the
analysis projection (Albers equal-area, EPSG:5070), so they're read as they are:

- land cover: one class per cell (open water, developed, forest, pasture/hay,
  cultivated crops, ...), classified from Landsat imagery;
- tree canopy cover: the percent of each cell under tree crowns (0-100).

Only a box around the AOI's turbine parcels is downloaded (a county is 1-3 MB),
cached in data/raw/mrlc_nlcd/<aoi>/ and reused while it still covers the parcels.
Nothing goes into the database but the data_sources row: the analysis reads the
files (hhsf.analysis.landcover).
"""

import math
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import rasterio

from hhsf.core.config import DATA_DIR, DataSource, data_source_settings, load_data_source

SOURCE_KEY = "mrlc_nlcd"
CELL_M = 30.0
# NLCD cell edges sit at 15 m past a multiple of 30 (the CONUS grid's origin).
GRID_OFFSET_M = 15.0
PAD_M = 120.0  # past the parcels, so edge cells are always inside the file
TIMEOUT_S = 300
RETRIES = 4
LAYERS = ("land_cover", "tree_canopy")

Bounds = tuple[float, float, float, float]


def snap(bounds: Bounds, pad_m: float = PAD_M) -> Bounds:
    """`bounds` (analysis CRS) padded and widened to whole NLCD cells."""
    xmin, ymin, xmax, ymax = bounds

    def down(v: float) -> float:
        return math.floor((v - GRID_OFFSET_M) / CELL_M) * CELL_M + GRID_OFFSET_M

    def up(v: float) -> float:
        return math.ceil((v - GRID_OFFSET_M) / CELL_M) * CELL_M + GRID_OFFSET_M

    return down(xmin - pad_m), down(ymin - pad_m), up(xmax + pad_m), up(ymax + pad_m)


def covers(path: Path, bounds: Bounds) -> bool:
    with rasterio.open(path) as r:
        b = r.bounds
    return (
        b.left <= bounds[0]
        and b.bottom <= bounds[1]
        and b.right >= bounds[2]
        and b.top >= bounds[3]
    )


def cache_dir(aoi_key: str) -> Path:
    return DATA_DIR / "raw" / SOURCE_KEY / aoi_key


def _download(coverage: str, bounds: Bounds, path: Path) -> None:
    url = data_source_settings(SOURCE_KEY)["url"]
    xmin, ymin, xmax, ymax = bounds
    params = [
        ("service", "WCS"),
        ("version", "2.0.1"),
        ("request", "GetCoverage"),
        ("coverageId", coverage),
        ("subset", f"X({xmin:.0f},{xmax:.0f})"),
        ("subset", f"Y({ymin:.0f},{ymax:.0f})"),
        ("format", "image/geotiff"),
    ]
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            response = httpx.get(url, params=params, timeout=TIMEOUT_S)
            response.raise_for_status()
            if not response.headers.get("content-type", "").startswith("image/"):
                raise RuntimeError(f"MRLC answered without a raster: {response.text[:300]}")
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".part")
            partial.write_bytes(response.content)
            partial.rename(path)  # only complete files are cached
            return
        except (httpx.HTTPError, RuntimeError) as exc:
            last_error = exc
            if attempt < RETRIES:
                time.sleep(5 * attempt)
    raise RuntimeError(
        f"MRLC download of {coverage} failed after {RETRIES} attempts"
    ) from last_error


def fetch_landcover(
    aoi_key: str, bounds: Bounds, refresh: bool = False, echo: Callable[[str], None] = print
) -> tuple[dict[str, Path], DataSource]:
    """The land cover and tree canopy rasters covering `bounds` (analysis CRS), by layer
    name. Both come from the same box, so their cells line up; a box is only downloaded
    when no cached pair covers `bounds`."""
    settings = data_source_settings(SOURCE_KEY)
    box = snap(bounds)
    folder = cache_dir(aoi_key)
    if not refresh:
        for land_cover in sorted(folder.glob(f"{LAYERS[0]}_*.tif")):
            pair = {layer: land_cover.with_name(land_cover.name.replace(LAYERS[0], layer, 1))
                    for layer in LAYERS}  # fmt: skip
            if all(p.exists() for p in pair.values()) and covers(land_cover, box):
                return pair, load_data_source(SOURCE_KEY)
    suffix = "_".join(f"{v:.0f}" for v in box)
    echo(f"  downloading NLCD land cover and tree canopy ({(box[2] - box[0]) / 1000:.0f} x "
         f"{(box[3] - box[1]) / 1000:.0f} km)")  # fmt: skip
    paths = {layer: folder / f"{layer}_{suffix}.tif" for layer in LAYERS}
    for layer, path in paths.items():
        _download(settings["coverages"][layer], box, path)
    return paths, load_data_source(SOURCE_KEY)
