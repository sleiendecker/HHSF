import { useEffect, useRef } from "react";
import maplibregl, { type MapGeoJSONFeature } from "maplibre-gl";

// Free basemap, no API key needed.
const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const TILES = `${window.location.origin}/tiles`;

const M_TO_FT = 3.28084;
const CFS_TO_GPM = 448.831;
const SQKM_TO_ACRES = 247.105;

function fmt(value: unknown, scale = 1, digits = 0): string {
  return typeof value === "number" ? (value * scale).toLocaleString(undefined, { maximumFractionDigits: digits }) : "—";
}

function popupHtml(f: MapGeoJSONFeature): string {
  const p = f.properties;
  const drop =
    typeof p.max_elev_m === "number" && typeof p.min_elev_m === "number" ? p.max_elev_m - p.min_elev_m : undefined;
  return `
    <div class="popup">
      <strong>${p.stream_name ?? "Unnamed stream"}</strong>
      <dl>
        <dt>Elevation drop</dt><dd>${fmt(drop, M_TO_FT)} ft</dd>
        <dt>Reach length</dt><dd>${fmt(p.length_m, M_TO_FT)} ft</dd>
        <dt>Slope</dt><dd>${fmt(p.slope, 100, 1)}%</dd>
        <dt>Drainage area</dt><dd>${fmt(p.drainage_area_sqkm, SQKM_TO_ACRES)} ac</dd>
        <dt>Mean flow (est.)</dt><dd>${fmt(p.mean_flow_cfs, CFS_TO_GPM)} gpm</dd>
        <dt>Stream order</dt><dd>${fmt(p.stream_order)}</dd>
      </dl>
    </div>`;
}

export function App() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const map = new maplibregl.Map({
      container: container.current!,
      style: BASEMAP,
      center: [-85.42, 35.36],
      zoom: 10,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }));

    map.on("load", () => {
      map.addSource("counties", { type: "vector", tiles: [`${TILES}/counties/{z}/{x}/{y}`] });
      map.addSource("streams", { type: "vector", tiles: [`${TILES}/streams/{z}/{x}/{y}`] });

      map.addLayer({
        id: "county-outline",
        type: "line",
        source: "counties",
        "source-layer": "counties",
        paint: { "line-color": "#555", "line-width": 1.5, "line-dasharray": [3, 2] },
      });

      // Bigger streams (higher order) draw wider and darker.
      map.addLayer({
        id: "streams",
        type: "line",
        source: "streams",
        "source-layer": "streams",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["interpolate", ["linear"], ["get", "stream_order"], 1, "#6baed6", 5, "#08306b"],
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            9,
            ["interpolate", ["linear"], ["get", "stream_order"], 1, 1, 5, 3],
            14,
            ["interpolate", ["linear"], ["get", "stream_order"], 1, 2.5, 5, 7],
          ],
        },
      });

      // Invisible wide lines on top so thin streams are easy to click.
      map.addLayer({
        id: "streams-hit",
        type: "line",
        source: "streams",
        "source-layer": "streams",
        paint: { "line-color": "#000", "line-opacity": 0, "line-width": 14 },
      });

      map.on("click", "streams-hit", (e) => {
        const feature = e.features?.[0];
        if (feature) new maplibregl.Popup().setLngLat(e.lngLat).setHTML(popupHtml(feature)).addTo(map);
      });
      map.on("mouseenter", "streams-hit", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "streams-hit", () => (map.getCanvas().style.cursor = ""));
    });

    return () => map.remove();
  }, []);

  return (
    <>
      <div ref={container} className="map" />
      <div className="panel">
        <h1>Hydro Homestead Site Finder</h1>
        <p>Perennial streams in Sequatchie County, TN. Click a stream for its numbers.</p>
      </div>
    </>
  );
}
