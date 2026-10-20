"""The 8-digit basins (WBD HUC8) an AOI's reaches drain to, for the regional low-flow yield.

A reach too small for StreamStats' regression (D-09) borrows the median StreamStats
Q90 per square mile of the in-range reaches in its own HUC8 (hydro.regional_flow).
This module only answers "which HUC8 is this reach's outlet in?". The polygons
come from the Watershed Boundary Dataset and are cached under data/cache/.
"""

from dataclasses import dataclass

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from hhsf.analysis import hydro
from hhsf.core import crs
from hhsf.core.config import AOI, DATA_DIR, data_source_settings
from hhsf.ingest.arcgis import query_once
from hhsf.ingest.hydrography import keep_area

CACHE_DIR = DATA_DIR / "cache" / "wbd"


@dataclass
class Basins:
    polygons: dict[str, BaseGeometry]  # HUC8 code -> polygon, analysis CRS

    def huc8_of(self, reach_geom: BaseGeometry) -> str | None:
        """The HUC8 the reach's downstream end is in (None: none of these)."""
        outlet = hydro.outlet(reach_geom)
        for code, polygon in self.polygons.items():
            if polygon.contains(outlet):
                return code
        return None


def load_basins(aoi: AOI, area: BaseGeometry, cache_dir=CACHE_DIR) -> Basins:
    """Every HUC8 touching the AOI's county plus buffer (`area`: the county, storage CRS)."""
    xmin, ymin, xmax, ymax = crs.to_storage(keep_area(aoi, area)).bounds
    body = query_once(
        data_source_settings("wbd_hu8")["url"],
        {
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "huc8,name",
            "returnGeometry": "true",
        },
        cache_dir / f"wbd_hu8_{aoi.key}.geojson",
    )
    features = body.get("features") or []
    if not features:
        raise RuntimeError(f"no WBD basins found around {aoi.name}")
    return Basins(
        {
            f["properties"]["huc8"]: crs.to_analysis(shape(f["geometry"]))
            for f in sorted(features, key=lambda f: f["properties"]["huc8"])
        }
    )
