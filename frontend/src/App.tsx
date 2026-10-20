import { useEffect, useRef, useState } from "react";
import maplibregl, { type MapGeoJSONFeature } from "maplibre-gl";
import { Legend } from "./Legend";
import { CFS_TO_GPM, SCORE_LAYERS, scorePopupHtml } from "./scores";

// Free basemap, no API key needed.
const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const TILES = `${window.location.origin}/tiles`;

const M_TO_FT = 3.28084;
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

// Scores and plain streams are the same lines, so only one set shows at a time.
function setScoresVisible(map: maplibregl.Map, visible: boolean) {
  const show = (on: boolean) => (on ? "visible" : "none");
  for (const layer of SCORE_LAYERS) map.setLayoutProperty(layer.id, "visibility", show(visible));
  map.setLayoutProperty("streams", "visibility", show(!visible));
}

export function App() {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map>();
  const [showScores, setShowScores] = useState(true);
  const showScoresRef = useRef(showScores);

  useEffect(() => {
    const map = new maplibregl.Map({
      container: container.current!,
      style: BASEMAP,
      center: [-85.42, 35.36],
      zoom: 10,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }));

    map.on("load", () => {
      map.addSource("counties", { type: "vector", tiles: [`${TILES}/counties/{z}/{x}/{y}`] });
      map.addSource("streams", { type: "vector", tiles: [`${TILES}/streams/{z}/{x}/{y}`] });
      map.addSource("scores", {
        type: "vector",
        tiles: [`${TILES}/latest_hydro_scores/{z}/{x}/{y}`],
      });

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

      // Every perennial stream has a scored corridor, so the scores replace the streams layer when shown.
      for (const layer of SCORE_LAYERS) map.addLayer(layer);
      setScoresVisible(map, showScoresRef.current);

      // With scores on, a click explains the score; with them off, it shows the stream's numbers.
      map.on("click", (e) => {
        const layer = showScoresRef.current ? "scores-hit" : "streams-hit";
        // Where lines are close, prefer the best-ranked one under the pointer.
        const byRank = (f: MapGeoJSONFeature) => (typeof f.properties.rank === "number" ? f.properties.rank : Infinity);
        const [feature] = map.queryRenderedFeatures(e.point, { layers: [layer] }).sort((a, b) => byRank(a) - byRank(b));
        if (!feature) return;
        const html = showScoresRef.current ? scorePopupHtml(feature) : popupHtml(feature);
        new maplibregl.Popup({ maxWidth: "340px" }).setLngLat(e.lngLat).setHTML(html).addTo(map);
      });
      for (const layer of ["scores-hit", "streams-hit"]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
    });

    return () => map.remove();
  }, []);

  useEffect(() => {
    showScoresRef.current = showScores;
    if (mapRef.current?.getLayer("scores-hit")) setScoresVisible(mapRef.current, showScores);
  }, [showScores]);

  return (
    <>
      <div ref={container} className="map" />
      <div className="panel">
        <h1>Hydro Homestead Site Finder</h1>
        <p>
          {showScores
            ? "Year-round streams in Sequatchie County, TN, colored by how well they could power a homestead. Click a stream to see why it scored what it did."
            : "Perennial streams in Sequatchie County, TN. Click a stream for its numbers."}
        </p>
        <label className="toggle">
          <input type="checkbox" checked={showScores} onChange={(e) => setShowScores(e.target.checked)} />
          Show hydro scores
        </label>
      </div>
      {showScores && <Legend />}
    </>
  );
}
