"""Minimal client for ArcGIS REST feature queries (TIGERweb, USGS National Map).

Responses are cached to data/raw/ so re-running an ingest doesn't re-download.

The USGS service answers small, plain spatial queries in a second or two, but
large areas and extra options (sorting, paging offsets, returnZ) push it onto a
slow path that times out with 504s. So large areas are split into small boxes,
and a box that hits the record limit is split again instead of paged.
"""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

TIMEOUT_S = 90
RETRIES = 4

BBox = tuple[float, float, float, float]


def _get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            response = httpx.get(url, params=params, timeout=TIMEOUT_S)
            response.raise_for_status()
            body = response.json()
            if "error" in body:
                raise RuntimeError(f"ArcGIS error: {body['error']}")
            return body
        except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < RETRIES:
                time.sleep(5 * attempt)
    raise RuntimeError(f"Query failed after {RETRIES} attempts: {url}") from last_error


def _exceeded_limit(body: dict[str, Any]) -> bool:
    return bool(
        body.get("exceededTransferLimit") or body.get("properties", {}).get("exceededTransferLimit")
    )


def query_once(url: str, params: dict[str, Any], cache_file: Path) -> dict[str, Any]:
    """One GeoJSON query, cached to a file."""
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    body = _get(url, {**params, "f": "geojson", "outSR": 4326})
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(body))
    return body


def query_geojson(url: str, params: dict[str, Any], cache_dir: Path) -> list[dict[str, Any]]:
    """Features from a small, non-spatial query (e.g. one county by ID)."""
    body = query_once(url, params, cache_dir / "result.geojson")
    if _exceeded_limit(body):
        raise RuntimeError("Query hit the service's record limit; narrow it down.")
    return body.get("features", [])


def query_geojson_tiled(
    url: str,
    params: dict[str, Any],
    bbox: BBox,
    cache_dir: Path,
    cell_deg: float = 0.1,
) -> Iterator[dict[str, Any]]:
    """Features intersecting bbox, fetched as a grid of small boxes.

    Features crossing box edges come back more than once; callers should
    de-duplicate on a stable ID.
    """
    xmin, ymin, xmax, ymax = bbox
    cols = max(1, round((xmax - xmin) / cell_deg))
    rows = max(1, round((ymax - ymin) / cell_deg))
    dx, dy = (xmax - xmin) / cols, (ymax - ymin) / rows
    for i in range(cols):
        for j in range(rows):
            x0, y0 = xmin + i * dx, ymin + j * dy
            yield from _query_box(url, params, (x0, y0, x0 + dx, y0 + dy), cache_dir / f"{i}_{j}")


def _query_box(url: str, params: dict[str, Any], box: BBox, cache_dir: Path):
    xmin, ymin, xmax, ymax = box
    body = query_once(
        url,
        {
            **params,
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
        },
        cache_dir / "result.geojson",
    )
    if not _exceeded_limit(body):
        yield from body.get("features", [])
        return
    # Too many features for one response: split into quarters and try again.
    xmid, ymid = (xmin + xmax) / 2, (ymin + ymax) / 2
    quarters = [
        (xmin, ymin, xmid, ymid),
        (xmid, ymin, xmax, ymid),
        (xmin, ymid, xmid, ymax),
        (xmid, ymid, xmax, ymax),
    ]
    for n, quarter in enumerate(quarters):
        yield from _query_box(url, params, quarter, cache_dir / f"q{n}")
