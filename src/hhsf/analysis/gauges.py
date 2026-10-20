"""Measured low flow at a USGS stream gauge, and the basin it can be scaled to.

The reference gauge for an AOI is set in config/aoi/<key>.yaml. Its Q90 is
computed from approved daily mean discharge (NWIS), and its 8-digit basin
(WBD HUC8) decides which reaches may borrow it. Responses are cached under
data/cache/ so a re-run doesn't download them again.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from hhsf.analysis.hydro import GaugeReference, GaugeTransferSettings, outlet
from hhsf.core import crs
from hhsf.core.config import DATA_DIR, ReferenceGauge, data_source_settings
from hhsf.ingest.arcgis import query_once

CACHE_DIR = DATA_DIR / "cache" / "nwis"
TIMEOUT_S = 120
DAYS_PER_YEAR = 365.25


class GaugeError(Exception):
    """The gauge record can't support a Q90 (missing, too short, unreadable)."""


def exceedance_flow(daily_cfs: list[float], percent: float = 90) -> float:
    """Flow equaled or exceeded `percent` of the days (Q90 = the 10th percentile)."""
    if not daily_cfs:
        raise GaugeError("no daily values")
    return float(np.percentile(daily_cfs, 100 - percent))


def approved_values(nwis_dv: dict) -> list[float]:
    """Approved ("A") daily means from an NWIS dv JSON response.

    Provisional values can still be revised, and NWIS marks missing days with
    large negative no-data values, so both are dropped.
    """
    try:
        series = nwis_dv["value"]["timeSeries"]
    except (KeyError, TypeError) as e:
        raise GaugeError("NWIS response has no time series") from e
    if not series:
        raise GaugeError("NWIS returned no discharge record for this period")
    values = []
    for item in series[0]["values"][0]["value"]:
        value = float(item["value"])
        if "A" in item.get("qualifiers", []) and value >= 0:
            values.append(value)
    return values


def parse_drainage_area(site_rdb: str) -> float:
    """drain_area_va (square miles) from an NWIS site service RDB response."""
    lines = [line for line in site_rdb.splitlines() if line and not line.startswith("#")]
    if len(lines) < 3:
        raise GaugeError("NWIS site response has no rows")
    header, row = lines[0].split("\t"), lines[2].split("\t")
    try:
        return float(row[header.index("drain_area_va")])
    except (ValueError, IndexError) as e:
        raise GaugeError("NWIS site has no drainage area") from e


def _cached_get(url: str, params: dict, path: Path, client: httpx.Client) -> str:
    if path.exists():
        return path.read_text()
    response = client.get(url, params=params)
    if response.status_code >= 400:
        raise GaugeError(f"{url}: HTTP {response.status_code}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text)
    return response.text


def fetch_reference(
    gauge: ReferenceGauge,
    settings: GaugeTransferSettings,
    cache_dir: Path = CACHE_DIR,
    client: httpx.Client | None = None,
) -> tuple[GaugeReference, dict]:
    """The gauge's Q90 and drainage area, plus a record of how it was computed."""
    nwis = data_source_settings("nwis")
    client = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        dv = _cached_get(
            nwis["daily_values"],
            {
                "format": "json",
                "sites": gauge.site_no,
                "parameterCd": "00060",
                "statCd": "00003",
                "startDT": settings.start,
                "endDT": settings.end,
            },
            cache_dir / f"dv_{gauge.site_no}_{settings.start}_{settings.end}.json",
            client,
        )
        site = _cached_get(
            nwis["site"],
            {"format": "rdb", "sites": gauge.site_no, "siteOutput": "expanded"},
            cache_dir / f"site_{gauge.site_no}.rdb",
            client,
        )
    except httpx.TransportError as e:
        raise GaugeError(f"NWIS unreachable: {type(e).__name__}") from e
    values = approved_values(json.loads(dv))
    years = len(values) / DAYS_PER_YEAR
    if years < settings.min_years:
        raise GaugeError(f"only {years:.1f} years of approved record (need {settings.min_years})")
    reference = GaugeReference(gauge.site_no, exceedance_flow(values), parse_drainage_area(site))
    record = {
        "site_no": reference.site_no,
        "huc8": gauge.huc8,
        "q90_cfs": reference.q90_cfs,
        "drainage_area_sqmi": reference.drainage_area_sqmi,
        "period": [settings.start, settings.end],
        "approved_days": len(values),
        "mean_cfs": float(np.mean(values)),
    }
    return reference, record


def fetch_basin(huc8: str, cache_dir: Path = CACHE_DIR) -> BaseGeometry:
    """The HUC8 polygon (storage CRS) from the Watershed Boundary Dataset."""
    body = query_once(
        data_source_settings("wbd_hu8")["url"],
        {"where": f"huc8='{huc8}'", "outFields": "huc8,name", "returnGeometry": "true"},
        cache_dir / f"wbd_hu8_{huc8}.geojson",
    )
    if not body.get("features"):
        raise GaugeError(f"no WBD basin {huc8}")
    return shape(body["features"][0]["geometry"])


@dataclass
class GaugeTransfer:
    """A reference gauge and the basin whose reaches may use it."""

    reference: GaugeReference
    basin: BaseGeometry  # analysis CRS
    record: dict

    def covers(self, reach_geom: BaseGeometry) -> bool:
        """True if the reach's downstream end is inside the gauge's basin."""
        return self.basin.contains(outlet(reach_geom))


def load_transfer(gauge: ReferenceGauge, settings: GaugeTransferSettings) -> GaugeTransfer:
    reference, record = fetch_reference(gauge, settings)
    return GaugeTransfer(reference, crs.to_analysis(fetch_basin(gauge.huc8)), record)
