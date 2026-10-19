"""USGS StreamStats adapter: Q90 (or another flow statistic) at a pour point.

Four web calls per point, following USGS's published workflow (endpoints in
config/data_sources.yaml):

1. delineate the watershed upstream of the point,
2. find the regression regions it falls in,
3. compute the basin characteristics the regression needs (drainage area,
   recession index, climate factor, soil permeability...),
4. evaluate the regression.

Step 3's scenario (equation and parameter limits) is fetched once per region.
Every successful response is cached on disk under data/cache/streamstats/, so a
re-run only calls the service for points it hasn't finished. Failures are never
cached.

The service extrapolates outside the equations' limits and only adds a warning,
so the limits are checked here: below the minimum drainage area there is no
estimate at all (D-09); other characteristics out of range are reported so the
caller can lower the confidence.
"""

import copy
import dataclasses
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from hhsf.analysis.hydro import LowFlowEstimate, StreamStatsSettings
from hhsf.core.config import DATA_DIR, data_source_settings

SOURCE_KEY = "streamstats"
CACHE_DIR = DATA_DIR / "cache" / "streamstats"
DRAINAGE_AREA = "DRNAREA"


class ServiceUnavailable(Exception):
    """StreamStats is down or timing out (5xx, timeouts, connection errors)."""


class PointFailed(Exception):
    """The service answered, but no estimate is possible at this point."""


class Endpoints(BaseModel):
    delineate: str
    regression_regions: str
    scenarios: str
    basin_characteristics: str
    estimate: str


class ServiceConfig(BaseModel):
    version: str
    endpoints: Endpoints
    max_concurrent_requests: int = 4
    timeout_s: float = 120
    retries: int = 2
    max_consecutive_failures: int = 3


def load_service_config() -> ServiceConfig:
    return ServiceConfig.model_validate(data_source_settings(SOURCE_KEY))


class LowFlowSource(Protocol):
    def low_flow(self, lat: float, lon: float) -> LowFlowEstimate: ...


class StreamStats:
    """Low-flow statistics from StreamStats, with an on-disk response cache."""

    def __init__(
        self,
        settings: StreamStatsSettings,
        service: ServiceConfig,
        cache_dir: Path = CACHE_DIR,
        client: httpx.Client | None = None,
        backoff_s: float = 2.0,
    ):
        self.s = settings
        self.service = service
        self.cache_dir = cache_dir
        self.client = client or httpx.Client(timeout=service.timeout_s)
        self.backoff_s = backoff_s
        self._scenarios: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- public ----------------------------------------------------------------

    def low_flow(self, lat: float, lon: float) -> LowFlowEstimate:
        cached = self._cache_get("estimate", lat, lon)
        if cached is not None:
            cached.pop("statistic", None)
            cached["out_of_range"] = tuple(cached["out_of_range"])
            return LowFlowEstimate(**cached)

        delineation = self._delineate(lat, lon)
        region = self._regression_region(lat, lon, _watershed_geometry(delineation))
        scenario = self._scenario(region)
        parameters = scenario["regressionRegions"][0]["parameters"]
        values = self._basin_characteristics(lat, lon, delineation, parameters)

        limits = {p["code"].upper(): p.get("limits") or {} for p in parameters}
        drainage_area = values[DRAINAGE_AREA]
        if drainage_area < limits.get(DRAINAGE_AREA, {}).get("min", self.s.min_drainage_area_sqmi):
            result = LowFlowEstimate(None, region, drainage_area, below_min_drainage_area=True)
        else:
            out_of_range = tuple(
                code for code, value in values.items() if not _within(value, limits.get(code))
            )
            value, std_error = self._estimate(scenario, values)
            result = LowFlowEstimate(value, region, drainage_area, std_error, False, out_of_range)

        record = {**dataclasses.asdict(result), "statistic": self.s.statistic_code}
        self._cache_put("estimate", lat, lon, record)
        return result

    # -- the four steps ----------------------------------------------------------

    def _delineate(self, lat: float, lon: float) -> dict:
        cached = self._cache_get("delineation", lat, lon)
        if cached is not None:
            return cached
        url = self.service.endpoints.delineate.format(region=self.s.region)
        body = self._request("GET", url, params={"lat": lat, "lon": lon})
        _watershed_geometry(body)  # fail before caching a response without a basin
        self._cache_put("delineation", lat, lon, body)
        return body

    def _regression_region(self, lat: float, lon: float, geometry: dict) -> str:
        cached = self._cache_get("regions", lat, lon)
        if cached is None:
            cached = self._request("POST", self.service.endpoints.regression_regions, json=geometry)
            self._cache_put("regions", lat, lon, cached)
        codes = {r.get("code") for r in cached}
        for code in self.s.regression_regions:
            if code in codes:
                return code
        raise PointFailed(f"no low-flow regression region (got {sorted(c for c in codes if c)})")

    def _scenario(self, region: str) -> dict:
        with self._lock:
            if region not in self._scenarios:
                body = self._request(
                    "GET",
                    self.service.endpoints.scenarios,
                    params={
                        "regions": self.s.region,
                        "statisticgroups": self.s.statistic_group,
                        "regressionregions": region,
                    },
                )
                if not body or not body[0].get("regressionRegions"):
                    raise PointFailed(f"no scenario for regression region {region}")
                self._scenarios[region] = body[0]
            return copy.deepcopy(self._scenarios[region])

    def _basin_characteristics(
        self, lat: float, lon: float, delineation: dict, parameters: list[dict]
    ) -> dict[str, float]:
        codes = [p["code"].upper() for p in parameters]
        cached = self._cache_get("basin_characteristics", lat, lon)
        if cached is None:
            request = copy.deepcopy(delineation)
            request["bcrequest"]["bcLabels"] = ";".join(codes)
            url = self.service.endpoints.basin_characteristics
            cached = self._request("POST", url, json=request)
            self._cache_put("basin_characteristics", lat, lon, cached)
        values = _parameter_values(cached)
        missing = [c for c in codes if values.get(c) is None]
        if missing:
            raise PointFailed(f"basin characteristics missing: {', '.join(missing)}")
        return {c: values[c] for c in codes}

    def _estimate(
        self, scenario: dict, values: dict[str, float]
    ) -> tuple[float | None, float | None]:
        for p in scenario["regressionRegions"][0]["parameters"]:
            p["value"] = values[p["code"].upper()]
        url = self.service.endpoints.estimate
        body = self._request("POST", url, params={"regions": self.s.region}, json=[scenario])
        for result in body[0]["regressionRegions"][0].get("results", []):
            if result.get("code") == self.s.statistic_code:
                errors = {e.get("code"): e.get("value") for e in result.get("errors") or []}
                return result.get("value"), errors.get("ASEp")
        raise PointFailed(f"estimate has no {self.s.statistic_code}")

    # -- plumbing ----------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        for attempt in range(self.service.retries + 1):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as e:  # timeouts, refused connections
                error: Exception = ServiceUnavailable(f"{url}: {type(e).__name__}")
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as e:
                        raise PointFailed(f"{url}: response is not JSON") from e
                if response.status_code < 500 and response.status_code != 429:
                    raise PointFailed(f"{url}: HTTP {response.status_code}")
                error = ServiceUnavailable(f"{url}: HTTP {response.status_code}")
            if attempt < self.service.retries:
                time.sleep(self.backoff_s * 2**attempt)
        raise error

    def _cache_path(self, step: str, lat: float, lon: float) -> Path:
        key = f"{self.s.region}_{lat:.6f}_{lon:.6f}"
        if step in ("estimate", "basin_characteristics"):  # depend on the statistic chosen
            key += f"_{self.s.statistic_group}_{self.s.statistic_code}"
        digest = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.cache_dir / step / f"{digest}.json"

    def _cache_get(self, step: str, lat: float, lon: float) -> Any:
        path = self._cache_path(step, lat, lon)
        return json.loads(path.read_text()) if path.exists() else None

    def _cache_put(self, step: str, lat: float, lon: float, body: Any) -> None:
        path = self._cache_path(step, lat, lon)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(body))
        tmp.replace(path)


def _within(value: float, limits: dict | None) -> bool:
    if not limits:
        return True
    low, high = limits.get("min"), limits.get("max")
    return (low is None or value >= low) and (high is None or value <= high)


def _watershed_geometry(delineation: dict) -> dict:
    """The whole-basin polygon from a delineation response (GlobalWshd = 1)."""
    try:
        collections = delineation["bcrequest"]["wsresp"]["featurecollection"][0]
    except (KeyError, IndexError, TypeError) as e:
        raise PointFailed("delineation response has no feature collection") from e
    for item in collections:
        if item.get("name") != "globalwatershed":
            continue
        for feature in item["feature"]["features"]:
            props = {k.lower(): v for k, v in (feature.get("properties") or {}).items()}
            if props.get("globalwshd") == 1:
                return feature["geometry"]
    raise PointFailed("delineation returned no watershed (point may be in an excluded area)")


def _parameter_values(body: Any) -> dict[str, float]:
    """Basin characteristic values keyed by code.

    Accepts a list of {"code", "value"} items or an object wrapping one under
    "parameters". (ss-hydro was down when this was written, so the parser is
    deliberately tolerant; codes are matched case-insensitively.)
    """
    items = body.get("parameters", []) if isinstance(body, dict) else body
    values = {}
    for item in items or []:
        code, value = item.get("code"), item.get("value")
        if code is not None and isinstance(value, int | float):
            values[code.upper()] = float(value)
    return values
