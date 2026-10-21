"""Drive time from downtown Chattanooga, from the local Valhalla routing service (D-02).

Two things, both from config/drive_time.yaml:

- The drive-time area: everywhere within `minutes` of the origin by car (Valhalla's
  isochrone). D-02 makes it the search region. It's saved with the counties it
  touches, so we know which counties to add as AOIs next.
- Each corridor's drive time: minutes and miles from the origin to the road point
  Valhalla snaps the turbine end to (its nearest drivable road in OpenStreetMap), and
  how far that point is from the turbine end. The walk or track from there to the
  stream isn't included.

Valhalla routes on OpenStreetMap with no traffic: free-flowing roads at each road's
typical speed. Drive times are flags for planning visits; nothing is filtered or
re-ranked on them. See docs/ASSUMPTIONS.md, "Roads and drive time".
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import httpx
import yaml
from pydantic import BaseModel
from shapely.geometry import LineString, MultiPolygon, shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, Engine, text

from hhsf.analysis.scoring_run import latest_hydro_run
from hhsf.core import crs
from hhsf.core.config import AOI, CONFIG_DIR, DATA_DIR, load_data_source
from hhsf.core.db import record_source
from hhsf.ingest.arcgis import query_geojson
from hhsf.ingest.boundary import SOURCE_KEY as COUNTIES_SOURCE

SOURCE_KEY = "osm_valhalla"
TIMEOUT_S = 300
SQKM_PER_SQM = 1e-6

# State FIPS codes of the states the area could reach, for readable county names.
STATES = {"01": "AL", "13": "GA", "37": "NC", "47": "TN"}


class Origin(BaseModel):
    key: str
    name: str
    lat: float
    lon: float


class DriveTimeConfig(BaseModel):
    origin: Origin
    minutes: int
    costing: str = "auto"
    valhalla_url: str = "http://localhost:8002"
    end_on_roads: dict[str, Any] = {}  # Valhalla search_filter for the turbine ends
    batch_size: int = 1000


def load_config() -> DriveTimeConfig:
    return DriveTimeConfig.model_validate(
        yaml.safe_load((CONFIG_DIR / "drive_time.yaml").read_text())
    )


class RoutingUnavailable(RuntimeError):
    pass


class ValhallaError(RuntimeError):
    pass


@dataclass(frozen=True)
class DriveTime:
    minutes: float | None  # None: Valhalla found no route (or no road near the point)
    distance_km: float | None
    road_point_m: float | None = None  # from the turbine end to where the drive ends
    any_road: bool = False  # ended on a track or driveway: no route to a public road


@dataclass(frozen=True)
class CountyShare:
    geoid: str
    state_code: str
    county_name: str
    county_sqkm: float
    inside_sqkm: float  # how much of the county is within the drive

    @property
    def label(self) -> str:
        return f"{self.county_name}, {STATES.get(self.state_code, self.state_code)}"

    @property
    def share(self) -> float:
        return self.inside_sqkm / self.county_sqkm if self.county_sqkm else 0.0


# --- Valhalla -------------------------------------------------------------------


class Valhalla:
    def __init__(self, cfg: DriveTimeConfig, client: httpx.Client | None = None):
        self.cfg = cfg
        self.client = client or httpx.Client(base_url=cfg.valhalla_url, timeout=TIMEOUT_S)

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self.client.post(path, json=body)
        except httpx.TransportError as e:
            raise RoutingUnavailable(
                f"Valhalla isn't answering at {self.cfg.valhalla_url}. Start it with "
                "`make routing` (the first start builds the road graph, 5-30 min)."
            ) from e
        if r.status_code >= 400:
            raise ValhallaError(f"Valhalla {path} failed ({r.status_code}): {r.text[:300]}")
        return r.json()

    def version(self) -> str:
        """'Valhalla 3.9.1, road graph built 2026-10-20', for provenance."""
        try:
            s = self.client.get("/status", params={"verbose": "true"}).json()
        except httpx.TransportError as e:
            raise RoutingUnavailable(
                f"Valhalla isn't answering at {self.cfg.valhalla_url}. Start it with "
                "`make routing`."
            ) from e
        return status_version(s)

    def isochrone(self) -> MultiPolygon:
        o = self.cfg.origin
        body = {
            "locations": [{"lat": o.lat, "lon": o.lon}],
            "costing": self.cfg.costing,
            "contours": [{"time": self.cfg.minutes}],
            "polygons": True,
            # Keep islands of reachable road (e.g. across a ridge); 1.0 would drop them.
            "denoise": 0.1,
        }
        return parse_isochrone(self._post("/isochrone", body))

    def drive_times(self, points: Sequence[tuple[float, float]]) -> list[DriveTime]:
        """Drive time from the origin to each (lon, lat), in the same order.

        Points whose nearest public road has no route from the origin (a gated or
        private section, cut off in OpenStreetMap) are tried again on any drivable road.
        """
        out: list[DriveTime] = []
        for batch in batches(points, self.cfg.batch_size):
            out += self._matrix(batch, self.cfg.end_on_roads)
        retry = [i for i, t in enumerate(out) if t.minutes is None]
        for i, t in zip(retry, self._matrix([points[i] for i in retry], {}), strict=True):
            if t.minutes is not None:
                out[i] = replace(t, any_road=True)
        return out

    def _matrix(
        self, targets: Sequence[tuple[float, float]], search_filter: dict[str, Any]
    ) -> list[DriveTime]:
        """One request; if Valhalla fails on it, halves until the failing point is alone.

        Valhalla 3.9 now and then fails a whole request over one point ("Could not find
        candidate edge used for label"). That point gets no drive time.
        """
        if not targets:
            return []
        o = self.cfg.origin
        body = {
            "sources": [{"lat": o.lat, "lon": o.lon}],
            "targets": [
                {"lat": lat, "lon": lon, "search_filter": search_filter} for lon, lat in targets
            ],
            "costing": self.cfg.costing,
            "units": "kilometers",
        }
        try:
            return parse_matrix(self._post("/sources_to_targets", body), targets)
        except ValhallaError:
            if len(targets) == 1:
                return [DriveTime(None, None)]
            half = len(targets) // 2
            return self._matrix(targets[:half], search_filter) + self._matrix(
                targets[half:], search_filter
            )


def status_version(status: dict) -> str:
    version = f"Valhalla {status.get('version', '?')}"
    built = status.get("tileset_last_modified")
    if isinstance(built, int | float):
        version += f", road graph built {datetime.fromtimestamp(built, UTC):%Y-%m-%d}"
    return version


def batches(items: Sequence, size: int) -> Iterator[Sequence]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def parse_isochrone(body: dict[str, Any]) -> MultiPolygon:
    """The isochrone's polygons (Valhalla may also return the input point)."""
    polys: list = []
    for f in body.get("features", []):
        g = shape(f["geometry"])
        if g.geom_type == "Polygon":
            polys.append(g)
        elif g.geom_type == "MultiPolygon":
            polys += list(g.geoms)
    if not polys:
        raise RuntimeError("Valhalla returned no drive-time polygon.")
    area = MultiPolygon(polys)
    return area if area.is_valid else _as_multipolygon(area.buffer(0))


def _as_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    return geom if geom.geom_type == "MultiPolygon" else MultiPolygon([geom])


def parse_matrix(body: dict[str, Any], targets: Sequence[tuple[float, float]]) -> list[DriveTime]:
    """One source to each (lon, lat) target.

    Each cell has the time (s), distance (km) and the road point the drive ends at
    (end_lon, end_lat). Valhalla 3.9 answers like this unless asked not to be verbose.
    """
    cells = sorted(body["sources_to_targets"][0], key=lambda c: c["to_index"])
    if len(cells) != len(targets):
        raise RuntimeError(f"Valhalla answered for {len(cells)} of {len(targets)} points.")
    out = []
    for c, (lon, lat) in zip(cells, targets, strict=True):
        if c.get("time") is None:
            out.append(DriveTime(None, None))
            continue
        end = (c.get("end_lon"), c.get("end_lat"))
        snap = None if None in end else crs.length_m(LineString([(lon, lat), end]))
        out.append(DriveTime(c["time"] / 60, c.get("distance"), snap))
    return out


# --- Counties the area touches ---------------------------------------------------


def county_shares(area: BaseGeometry, counties: list[dict]) -> list[CountyShare]:
    """How much of each county is within the area, biggest share first.

    `counties` are GeoJSON features from TIGERweb (GEOID, NAME, STATE). Areas are
    measured in the analysis CRS. Counties that only touch the edge are left out.
    """
    a = crs.to_analysis(area)
    out = []
    for f in counties:
        c = crs.to_analysis(shape(f["geometry"]))
        inside = c.intersection(a).area * SQKM_PER_SQM
        if inside <= 0:
            continue
        p = f["properties"]
        out.append(CountyShare(p["GEOID"], p["STATE"], p["NAME"], c.area * SQKM_PER_SQM, inside))
    return sorted(out, key=lambda s: (-s.share, s.geoid))


def fetch_counties(area: BaseGeometry) -> list[dict]:
    """TIGERweb counties whose outline box meets the area's box (cached in data/raw/)."""
    source = load_data_source(COUNTIES_SOURCE)
    xmin, ymin, xmax, ymax = (round(v, 3) for v in area.bounds)
    return query_geojson(
        source.url,
        {
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "GEOID,NAME,STATE",
        },
        cache_dir=DATA_DIR / "raw" / COUNTIES_SOURCE / f"box_{xmin}_{ymin}_{xmax}_{ymax}",
    )


# --- Saving ------------------------------------------------------------------------


def save_area(
    conn: Connection,
    cfg: DriveTimeConfig,
    area: MultiPolygon,
    shares: list[CountyShare],
    engine_version: str,
) -> int:
    """Replace the drive-time area (there's one at a time) and its counties.

    Drive times from an earlier origin are deleted too, so nothing mixes two origins.
    """
    source = load_data_source(SOURCE_KEY).model_copy(update={"version": engine_version})
    record_source(conn, source)
    conn.execute(text("DELETE FROM drive_time_areas"))
    conn.execute(
        text("DELETE FROM corridor_drive_times WHERE origin_key <> :k"), {"k": cfg.origin.key}
    )
    o = cfg.origin
    area_id = conn.execute(
        text(
            """
            INSERT INTO drive_time_areas (origin_key, origin_name, origin, minutes, costing,
                                          geom, engine_version, source_key)
            VALUES (:key, :name, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :minutes,
                    :costing, ST_Multi(ST_GeomFromWKB(:wkb, 4326)), :version, :source)
            RETURNING area_id
            """
        ),
        {
            "key": o.key,
            "name": o.name,
            "lon": o.lon,
            "lat": o.lat,
            "minutes": cfg.minutes,
            "costing": cfg.costing,
            "wkb": area.wkb,
            "version": engine_version,
            "source": SOURCE_KEY,
        },
    ).scalar_one()
    if shares:
        conn.execute(
            text(
                """
                INSERT INTO drive_time_area_counties
                    (area_id, geoid, state_code, county_name, county_sqkm, inside_sqkm)
                VALUES (:area_id, :geoid, :state_code, :county_name, :county_sqkm, :inside_sqkm)
                """
            ),
            [{"area_id": area_id, **s.__dict__} for s in shares],
        )
    return area_id


def build_area(
    eng: Engine, cfg: DriveTimeConfig, routing: Valhalla, echo: Callable[[str], None] = print
) -> tuple[MultiPolygon, list[CountyShare]]:
    """Ask Valhalla for the drive-time area and save it with the counties it touches."""
    version = routing.version()
    echo(f"Asking {version} for the {cfg.minutes}-minute drive from {cfg.origin.name}...")
    area = routing.isochrone()
    shares = county_shares(area, fetch_counties(area))
    with eng.begin() as conn:
        save_area(conn, cfg, area, shares, version)
    return area, shares


def turbine_points(conn: Connection, hydro_run_id: int) -> list[tuple[int, float, float]]:
    """(corridor_id, lon, lat) of each corridor's turbine end, the bottom of the stretch."""
    rows = conn.execute(
        text(
            """
            SELECT corridor_id, ST_X(pt) AS lon, ST_Y(pt) AS lat
            FROM hydro_corridors c
            -- NHDPlus draws flowlines upstream to downstream: the last point is the bottom.
            CROSS JOIN LATERAL (
                SELECT ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom))) AS pt
            ) o
            WHERE run_id = :r
            ORDER BY corridor_id
            """
        ),
        {"r": hydro_run_id},
    ).all()
    return [(r.corridor_id, r.lon, r.lat) for r in rows]


def save_drive_times(
    conn: Connection, origin_key: str, corridor_ids: Sequence[int], times: Sequence[DriveTime]
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO corridor_drive_times
                (corridor_id, origin_key, minutes, distance_km, road_point_m, any_road)
            VALUES (:corridor_id, :origin_key, :minutes, :distance_km, :road_point_m, :any_road)
            ON CONFLICT (corridor_id) DO UPDATE SET
                origin_key = EXCLUDED.origin_key,
                minutes = EXCLUDED.minutes,
                distance_km = EXCLUDED.distance_km,
                road_point_m = EXCLUDED.road_point_m,
                any_road = EXCLUDED.any_road,
                computed_at = now()
            """
        ),
        [
            {
                "corridor_id": c,
                "origin_key": origin_key,
                "minutes": t.minutes,
                "distance_km": t.distance_km,
                "road_point_m": t.road_point_m,
                "any_road": t.any_road,
            }
            for c, t in zip(corridor_ids, times, strict=True)
        ],
    )


def compute_drive_times(
    eng: Engine, aoi: AOI, cfg: DriveTimeConfig, routing: Valhalla, hydro_run_id: int | None = None
) -> tuple[int, list[DriveTime]]:
    """Drive time to every corridor of a hydro run (default: the AOI's latest).

    Returns (hydro run id, drive times in corridor order).
    """
    with eng.connect() as conn:
        if hydro_run_id is None:
            hydro_run_id = latest_hydro_run(conn, aoi.key)
        points = turbine_points(conn, hydro_run_id)
    times = routing.drive_times([(lon, lat) for _, lon, lat in points])
    with eng.begin() as conn:
        save_drive_times(conn, cfg.origin.key, [c for c, _, _ in points], times)
    return hydro_run_id, times
