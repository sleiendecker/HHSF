"""Perennial streams from the bulk NHDPlus HR file downloads (the default source).

USGS publishes NHDPlus HR as one file geodatabase per HU4 region (a few hundred
MB zipped). Downloading those once is much faster and more reliable than paging
through the web service. The files are cached in data/raw/ and read straight
from the zip, so nothing is unpacked.

Each file has the same data the web service serves, split across tables:
NHDFlowline (geometry, name, FCode), NHDPlusFlowlineVAA (stream order, drainage
area, smoothed elevations, slope) and NHDPlusEROMMA (mean annual flow). They're
joined on NHDPlusID here and handed to the same parser and loader as the
service path. Only flowlines with a VAA row are kept: that is the routed stream
network, the same set the service's NetworkNHDFlowline layer returns.
"""

import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx
import pyogrio
import shapely
from shapely.geometry.base import BaseGeometry

from hhsf.core import crs
from hhsf.core.config import AOI, DATA_DIR, load_data_source
from hhsf.ingest.arcgis import query_geojson
from hhsf.ingest.hydrography import (
    FCODE_PERENNIAL,
    StreamRecord,
    keep_area,
    parse_record,
    select_perennial,
)

SOURCE_KEY = "nhdplus_hr_bulk"
HU4_SOURCE_KEY = "wbd_hu4"

FLOWLINE_LAYER = "NHDFlowline"
VAA_TABLE = "NHDPlusFlowlineVAA"
FLOW_TABLE = "NHDPlusEROMMA"
FLOWLINE_FIELDS = ["NHDPlusID", "Permanent_Identifier", "GNIS_Name", "FCode"]
VAA_FIELDS = ["NHDPlusID", "StreamOrde", "TotDASqKm", "MaxElevSmo", "MinElevSmo", "Slope"]
FLOW_FIELDS = ["NHDPlusID", "QEMA"]

# Read a little past the AOI's bounding box; the exact clip happens afterwards.
_BBOX_PAD_DEG = 0.01
# Keep SQL IN (...) lists a manageable size.
_ID_CHUNK = 500


def huc4_codes(aoi: AOI, area: BaseGeometry) -> list[str]:
    """HU4 regions the AOI (plus buffer) touches: from the AOI file, else from WBD."""
    if aoi.huc4:
        return aoi.huc4
    xmin, ymin, xmax, ymax = crs.to_storage(keep_area(aoi, area)).bounds
    features = query_geojson(
        load_data_source(HU4_SOURCE_KEY).url,
        {
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "huc4",
            "returnGeometry": "false",
        },
        cache_dir=DATA_DIR / "raw" / HU4_SOURCE_KEY / aoi.key,
    )
    return sorted({f["properties"]["huc4"] for f in features})


def gdb_url(huc4: str) -> str:
    """Download URL of the HU4 region's current geodatabase.

    File names carry a release date for some regions and not others (e.g.
    NHDPLUS_H_0602_HU4_20220418_GDB.zip, NHDPLUS_H_0513_HU4_GDB.zip), so the
    folder is listed to find it.
    """
    folder = urlsplit(load_data_source(SOURCE_KEY).url)
    bucket = f"{folder.scheme}://{folder.netloc}"
    prefix = f"{folder.path.lstrip('/')}NHDPLUS_H_{huc4}_HU4"
    response = httpx.get(bucket, params={"list-type": 2, "prefix": prefix}, timeout=60)
    response.raise_for_status()
    keys = [
        el.text or ""
        for el in ElementTree.fromstring(response.text).iter()
        if el.tag.endswith("}Key") or el.tag == "Key"
    ]
    zips = sorted(k for k in keys if k.endswith("_GDB.zip"))
    if not zips:
        raise RuntimeError(f"No NHDPlus HR file for HU4 {huc4} under {folder.geturl()}")
    return f"{bucket}/{zips[-1]}"


def download(huc4: str, echo=print) -> Path:
    """The HU4 region's zipped geodatabase, downloaded once to data/raw/."""
    cache_dir = DATA_DIR / "raw" / SOURCE_KEY
    cached = sorted(cache_dir.glob(f"NHDPLUS_H_{huc4}_HU4*_GDB.zip"))
    if cached:
        return cached[-1]
    url = gdb_url(huc4)
    path = cache_dir / url.rsplit("/", 1)[-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as response:
        response.raise_for_status()
        size_mb = int(response.headers.get("content-length", 0)) / 1e6
        echo(f"Downloading {path.name} ({size_mb:.0f} MB)...")
        with partial.open("wb") as f:
            for chunk in response.iter_bytes(1 << 20):
                f.write(chunk)
    # Rename only when complete, so an interrupted download is never mistaken for a good file.
    partial.rename(path)
    return path


def _read_table(
    path: str, layer: str, fields: list[str], where: str | None = None, **kwargs: Any
) -> tuple[list[dict[str, Any]], list[bytes] | None, str | None]:
    """Rows of a layer as dicts with lowercase keys, plus WKB geometries and CRS if any.

    Field names are matched ignoring case: some releases use NHDPlusID, others nhdplusid.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "Measured", UserWarning)
        info = pyogrio.read_info(path, layer=layer)
    actual = {name.lower(): name for name in info["fields"]}
    with warnings.catch_warnings():
        # Flowlines are "Measured 3D"; GDAL drops M (and we drop Z later). That's fine.
        warnings.filterwarnings("ignore", "Measured", UserWarning)
        meta, _, geometry, columns = pyogrio.raw.read(
            path, layer=layer, columns=[actual[f.lower()] for f in fields], where=where, **kwargs
        )
    names = [n.lower() for n in meta["fields"]]
    rows = [
        {name: _plain(values[i]) for name, values in zip(names, columns, strict=True)}
        for i in range(len(columns[0]) if columns else 0)
    ]
    return rows, geometry, meta.get("crs")


def _plain(value: Any) -> Any:
    """numpy scalars to plain Python values (empty strings become None)."""
    if hasattr(value, "item"):
        value = value.item()
    return None if value == "" else value


def _by_id(path: str, table: str, fields: list[str], ids: list[int]) -> dict[int, dict]:
    rows: dict[int, dict] = {}
    for start in range(0, len(ids), _ID_CHUNK):
        chunk = ",".join(str(i) for i in ids[start : start + _ID_CHUNK])
        found, _, _ = _read_table(
            path, table, fields, where=f"NHDPlusID IN ({chunk})", read_geometry=False
        )
        rows.update({int(r["nhdplusid"]): r for r in found})
    return rows


def read_flowlines(path: str, bbox: tuple[float, float, float, float]) -> list[StreamRecord]:
    """Perennial network flowlines in bbox (WGS84 degrees) from one geodatabase."""
    flowlines, geometry, file_crs = _read_table(
        path, FLOWLINE_LAYER, FLOWLINE_FIELDS, where=f"FCode = {FCODE_PERENNIAL}", bbox=bbox
    )
    ids = [int(f["nhdplusid"]) for f in flowlines]
    vaa = _by_id(path, VAA_TABLE, VAA_FIELDS, ids)
    flow = _by_id(path, FLOW_TABLE, FLOW_FIELDS, ids)
    records = []
    for props, wkb in zip(flowlines, geometry, strict=True):
        nhdplusid = int(props["nhdplusid"])
        if wkb is None or nhdplusid not in vaa:
            continue  # no geometry, or not part of the routed network
        geom = crs.file_to_storage(shapely.from_wkb(wkb), file_crs)
        records.append(parse_record({**props, **vaa[nhdplusid], **flow.get(nhdplusid, {})}, geom))
    return records


def fetch_perennial(aoi: AOI, area: BaseGeometry, echo=print) -> Iterator[list[StreamRecord]]:
    """Perennial flowlines touching the AOI (plus its buffer), one batch per HU4 file."""
    keep = keep_area(aoi, area)
    xmin, ymin, xmax, ymax = crs.to_storage(keep).bounds
    bbox = (xmin - _BBOX_PAD_DEG, ymin - _BBOX_PAD_DEG, xmax + _BBOX_PAD_DEG, ymax + _BBOX_PAD_DEG)
    seen: set[int] = set()
    for huc4 in huc4_codes(aoi, area):
        zip_path = download(huc4, echo)
        echo(f"Reading {zip_path.name}...")
        yield select_perennial(read_flowlines(f"/vsizip/{zip_path}", bbox), keep, seen)
