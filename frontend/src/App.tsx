import { useEffect, useRef, useState } from "react";
import maplibregl, { type ExpressionSpecification, type MapGeoJSONFeature } from "maplibre-gl";
import { BasemapControl } from "./BasemapControl";
import { LIGHT_STYLE, addBasemapLayers, saveBasemap, savedBasemap, showBasemap } from "./basemaps";
import { Collapsible } from "./Collapsible";
import { Legend } from "./Legend";
import { CFS_TO_GPM, SCORE_LAYERS, scorePopupHtml } from "./scores";

const TILES = `${window.location.origin}/tiles`;

// Where the map opens until the analyzed counties' extent is known (Sequatchie County).
const START_CENTER: [number, number] = [-85.42, 35.36];

const M_TO_FT = 3.28084;
const SQKM_TO_ACRES = 247.105;

function fmt(value: unknown, scale = 1, digits = 0): string {
  return typeof value === "number" ? (value * scale).toLocaleString(undefined, { maximumFractionDigits: digits }) : "—";
}

// Bigger streams (higher order) draw wider; `extra` widens the line for its halo.
function streamWidth(extra = 0): ExpressionSpecification {
  const byOrder = (low: number, high: number): ExpressionSpecification =>
    ["+", ["interpolate", ["linear"], ["get", "stream_order"], 1, low, 5, high], extra];
  return ["interpolate", ["linear"], ["zoom"], 9, byOrder(1, 3), 14, byOrder(2.5, 7)];
}

// Zoom to every loaded county. Martin works out a table's extent when it starts
// (`make ingest` restarts it), and reports the whole world for an empty table.
async function fitToCounties(map: maplibregl.Map) {
  try {
    const response = await fetch(`${TILES}/counties`);
    const { bounds } = await response.json();
    if (!Array.isArray(bounds) || bounds[2] - bounds[0] > 10) return;
    map.fitBounds(bounds as [number, number, number, number], { padding: 40, duration: 0 });
  } catch {
    // No tile server yet: stay on the start view.
  }
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

// Martin serves parcels from zoom 12 (martin/martin.yaml): the five counties hold ~80,000.
const PARCELS_MIN_ZOOM = 12;
// And FEMA flood zones from zoom 10.
const FLOOD_MIN_ZOOM = 10;
// Floodway, the rest of the 100-year floodplain (SFHA), the 0.2% annual chance area. Zone X
// "minimal hazard" covers everything else and isn't drawn.
const FLOOD_COLORS = { floodway: "#1d4ed8", sfha: "#3b82f6", moderate: "#93c5fd" };
// TIGER roads from zoom 10. Public roads solid; 4WD trails, private and parking lot roads dashed.
const ROADS_MIN_ZOOM = 10;
const ROAD_COLORS = { public: "#d97706", other: "#78350f" };
// The area within a 1-hour drive of downtown Chattanooga (D-02, migration 0012).
const DRIVE_AREA_COLOR = "#15803d";
// SSURGO soils and the turbine parcels' buildable ground from zoom 11 (migration 0013).
const SOILS_MIN_ZOOM = 11;
// The Tennessee septic rating (septic_tn), the one the buildable-ground step uses.
const SEPTIC_COLORS = {
  "Slightly limited": "#4d9221",
  "Moderately limited": "#e6ab02",
  "Very limited": "#c51b7d",
  "Not rated": "#9ca3af",
};
const BUILDABLE_COLOR = "#16a34a";

// Scores and plain streams are the same lines, so only one set shows at a time.
function setScoresVisible(map: maplibregl.Map, visible: boolean) {
  const show = (on: boolean) => (on ? "visible" : "none");
  for (const layer of SCORE_LAYERS) map.setLayoutProperty(layer.id, "visibility", show(visible));
  for (const layer of ["streams-halo", "streams"]) map.setLayoutProperty(layer, "visibility", show(!visible));
}

export function App() {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map>();
  const [showScores, setShowScores] = useState(true);
  const showScoresRef = useRef(showScores);
  const [showParcels, setShowParcels] = useState(false);
  const showParcelsRef = useRef(showParcels);
  const [showFlood, setShowFlood] = useState(false);
  const showFloodRef = useRef(showFlood);
  const [showRoads, setShowRoads] = useState(false);
  const showRoadsRef = useRef(showRoads);
  const [showDriveArea, setShowDriveArea] = useState(false);
  const showDriveAreaRef = useRef(showDriveArea);
  const [showSoils, setShowSoils] = useState(false);
  const showSoilsRef = useRef(showSoils);
  const [showBuildable, setShowBuildable] = useState(false);
  const showBuildableRef = useRef(showBuildable);
  const [zoom, setZoom] = useState(10);

  useEffect(() => {
    const map = new maplibregl.Map({
      container: container.current!,
      style: LIGHT_STYLE,
      center: START_CENTER,
      zoom: 10,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    const basemaps = new BasemapControl(savedBasemap(), (id) => {
      saveBasemap(id);
      if (map.getLayer("scores-hit")) showBasemap(map, id);
    });
    map.addControl(basemaps, "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }));

    map.on("load", () => {
      fitToCounties(map);
      addBasemapLayers(map);
      map.addSource("counties", { type: "vector", tiles: [`${TILES}/counties/{z}/{x}/{y}`] });
      map.addSource("streams", { type: "vector", tiles: [`${TILES}/streams/{z}/{x}/{y}`] });
      map.addSource("parcels", {
        type: "vector",
        tiles: [`${TILES}/parcels/{z}/{x}/{y}`],
        minzoom: PARCELS_MIN_ZOOM,
        attribution: "Parcels: Tennessee Comptroller of the Treasury",
      });
      map.addSource("flood", {
        type: "vector",
        tiles: [`${TILES}/flood_zones/{z}/{x}/{y}`],
        minzoom: FLOOD_MIN_ZOOM,
        attribution: "Flood zones: FEMA National Flood Hazard Layer",
      });
      map.addSource("roads", {
        type: "vector",
        tiles: [`${TILES}/roads/{z}/{x}/{y}`],
        minzoom: ROADS_MIN_ZOOM,
        attribution: "Roads: US Census Bureau TIGER/Line",
      });
      map.addSource("drive-area", {
        type: "vector",
        tiles: [`${TILES}/drive_time_areas/{z}/{x}/{y}`],
        attribution: "Drive time: Valhalla, © OpenStreetMap contributors",
      });
      map.addSource("soils", {
        type: "vector",
        tiles: [`${TILES}/soils/{z}/{x}/{y}`],
        minzoom: SOILS_MIN_ZOOM,
        attribution: "Soils: USDA NRCS SSURGO",
      });
      map.addSource("buildable", {
        type: "vector",
        tiles: [`${TILES}/parcel_buildable/{z}/{x}/{y}`],
        minzoom: SOILS_MIN_ZOOM,
      });
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

      // The drive-time area: a light wash with a solid edge, under everything else.
      const driveVisibility = showDriveAreaRef.current ? "visible" : "none";
      map.addLayer({
        id: "drive-area-fill",
        type: "fill",
        source: "drive-area",
        "source-layer": "drive_time_areas",
        layout: { visibility: driveVisibility },
        paint: { "fill-color": DRIVE_AREA_COLOR, "fill-opacity": 0.08 },
      });
      map.addLayer({
        id: "drive-area-outline",
        type: "line",
        source: "drive-area",
        "source-layer": "drive_time_areas",
        layout: { visibility: driveVisibility },
        paint: { "line-color": DRIVE_AREA_COLOR, "line-width": 2 },
      });

      // Septic ratings at the bottom. Stored in pieces (migration 0013), so fill only.
      map.addLayer({
        id: "soils-fill",
        type: "fill",
        source: "soils",
        "source-layer": "soils",
        minzoom: SOILS_MIN_ZOOM,
        layout: { visibility: showSoilsRef.current ? "visible" : "none" },
        paint: {
          "fill-color": [
            "match",
            ["get", "septic_tn"],
            "Slightly limited",
            SEPTIC_COLORS["Slightly limited"],
            "Moderately limited",
            SEPTIC_COLORS["Moderately limited"],
            "Very limited",
            SEPTIC_COLORS["Very limited"],
            SEPTIC_COLORS["Not rated"],
          ],
          "fill-opacity": 0.3,
          "fill-antialias": false,
        },
      });

      // Flood zones under everything else. Stored in pieces (migration 0011), so fill only: outlines would show the seams.
      map.addLayer({
        id: "flood-fill",
        type: "fill",
        source: "flood",
        "source-layer": "flood_zones",
        minzoom: FLOOD_MIN_ZOOM,
        filter: ["any", ["==", ["get", "sfha"], true], ["in", "0.2 PCT", ["coalesce", ["get", "subtype"], ""]]],
        layout: { visibility: showFloodRef.current ? "visible" : "none" },
        paint: {
          "fill-color": [
            "case",
            ["==", ["get", "floodway"], true],
            FLOOD_COLORS.floodway,
            ["==", ["get", "sfha"], true],
            FLOOD_COLORS.sfha,
            FLOOD_COLORS.moderate,
          ],
          "fill-opacity": 0.35,
          "fill-antialias": false,
        },
      });

      // Buildable ground on turbine parcels: a wash with a solid edge (one shape per parcel).
      const buildableVisibility = showBuildableRef.current ? "visible" : "none";
      map.addLayer({
        id: "buildable-fill",
        type: "fill",
        source: "buildable",
        "source-layer": "parcel_buildable",
        minzoom: SOILS_MIN_ZOOM,
        layout: { visibility: buildableVisibility },
        paint: { "fill-color": BUILDABLE_COLOR, "fill-opacity": 0.3 },
      });
      map.addLayer({
        id: "buildable-outline",
        type: "line",
        source: "buildable",
        "source-layer": "parcel_buildable",
        minzoom: SOILS_MIN_ZOOM,
        layout: { visibility: buildableVisibility },
        paint: { "line-color": BUILDABLE_COLOR, "line-width": 1.2 },
      });

      // Property lines under the streams; showBasemap() sets the color. No owner data in these tiles.
      map.addLayer({
        id: "parcel-outline",
        type: "line",
        source: "parcels",
        "source-layer": "parcels",
        minzoom: PARCELS_MIN_ZOOM,
        layout: { visibility: showParcelsRef.current ? "visible" : "none" },
        paint: { "line-color": "#c2549d", "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.6, 16, 1.6] },
      });

      // TIGER roads under the streams.
      map.addLayer({
        id: "roads",
        type: "line",
        source: "roads",
        "source-layer": "roads",
        minzoom: ROADS_MIN_ZOOM,
        layout: { visibility: showRoadsRef.current ? "visible" : "none", "line-cap": "round" },
        paint: {
          "line-color": ["case", ["==", ["get", "public"], true], ROAD_COLORS.public, ROAD_COLORS.other],
          "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.8, 16, 2.5],
          "line-dasharray": ["case", ["==", ["get", "public"], true], ["literal", [1, 0]], ["literal", [2, 1.5]]],
        },
      });

      // A white outline so streams stand out on imagery; showBasemap() sets how strong.
      map.addLayer({
        id: "streams-halo",
        type: "line",
        source: "streams",
        "source-layer": "streams",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#fff", "line-opacity": 0, "line-width": streamWidth(2) },
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
          "line-width": streamWidth(),
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
      showBasemap(map, basemaps.value);

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

    map.on("zoomend", () => setZoom(map.getZoom()));

    return () => map.remove();
  }, []);

  useEffect(() => {
    showParcelsRef.current = showParcels;
    if (mapRef.current?.getLayer("parcel-outline"))
      mapRef.current.setLayoutProperty("parcel-outline", "visibility", showParcels ? "visible" : "none");
  }, [showParcels]);

  useEffect(() => {
    showFloodRef.current = showFlood;
    if (mapRef.current?.getLayer("flood-fill"))
      mapRef.current.setLayoutProperty("flood-fill", "visibility", showFlood ? "visible" : "none");
  }, [showFlood]);

  useEffect(() => {
    showRoadsRef.current = showRoads;
    if (mapRef.current?.getLayer("roads"))
      mapRef.current.setLayoutProperty("roads", "visibility", showRoads ? "visible" : "none");
  }, [showRoads]);

  useEffect(() => {
    showDriveAreaRef.current = showDriveArea;
    for (const layer of ["drive-area-fill", "drive-area-outline"])
      if (mapRef.current?.getLayer(layer))
        mapRef.current.setLayoutProperty(layer, "visibility", showDriveArea ? "visible" : "none");
  }, [showDriveArea]);

  useEffect(() => {
    showSoilsRef.current = showSoils;
    if (mapRef.current?.getLayer("soils-fill"))
      mapRef.current.setLayoutProperty("soils-fill", "visibility", showSoils ? "visible" : "none");
  }, [showSoils]);

  useEffect(() => {
    showBuildableRef.current = showBuildable;
    for (const layer of ["buildable-fill", "buildable-outline"])
      if (mapRef.current?.getLayer(layer))
        mapRef.current.setLayoutProperty(layer, "visibility", showBuildable ? "visible" : "none");
  }, [showBuildable]);

  useEffect(() => {
    showScoresRef.current = showScores;
    if (mapRef.current?.getLayer("scores-hit")) setScoresVisible(mapRef.current, showScores);
  }, [showScores]);

  return (
    <>
      <div ref={container} className="map" />
      <div className="sidebar">
        <Collapsible id="panel" className="panel" title={<h1>Hydro Homestead Site Finder</h1>}>
          <p>
            {showScores
              ? "The steepest stretch (up to 1,000 ft) of each year-round stream in the analyzed counties (dashed outlines), colored by how well it could power a homestead. Click one to see why it scored what it did."
              : "Perennial streams in the analyzed counties (dashed outlines). Click a stream for its numbers."}
          </p>
          <label className="toggle">
            <input type="checkbox" checked={showScores} onChange={(e) => setShowScores(e.target.checked)} />
            Show hydro scores
          </label>
          <label className="toggle">
            <input type="checkbox" checked={showParcels} onChange={(e) => setShowParcels(e.target.checked)} />
            Show property lines
          </label>
          {showParcels && zoom < PARCELS_MIN_ZOOM && <p className="hint">Zoom in to see property lines.</p>}
          <label className="toggle">
            <input type="checkbox" checked={showFlood} onChange={(e) => setShowFlood(e.target.checked)} />
            Show FEMA flood zones
          </label>
          {showFlood && (
            <p className="hint">
              {zoom < FLOOD_MIN_ZOOM && "Zoom in to see flood zones. "}
              <span className="swatch" style={{ background: FLOOD_COLORS.floodway }} /> floodway{" "}
              <span className="swatch" style={{ background: FLOOD_COLORS.sfha }} /> 100-year{" "}
              <span className="swatch" style={{ background: FLOOD_COLORS.moderate }} /> 500-year. Small creeks are mostly unmapped.
            </p>
          )}
          <label className="toggle">
            <input type="checkbox" checked={showRoads} onChange={(e) => setShowRoads(e.target.checked)} />
            Show Census roads
          </label>
          {showRoads && (
            <p className="hint">
              {zoom < ROADS_MIN_ZOOM && "Zoom in to see roads. "}
              <span className="swatch" style={{ background: ROAD_COLORS.public }} /> public{" "}
              <span className="swatch" style={{ background: ROAD_COLORS.other }} /> 4WD trail or private (dashed). Most driveways and logging roads are missing.
            </p>
          )}
          <label className="toggle">
            <input type="checkbox" checked={showDriveArea} onChange={(e) => setShowDriveArea(e.target.checked)} />
            Show 1-hour drive from downtown Chattanooga
          </label>
          {showDriveArea && (
            <p className="hint">
              <span className="swatch" style={{ background: DRIVE_AREA_COLOR }} /> by car, no traffic (the search region, D-02).
            </p>
          )}
          <label className="toggle">
            <input type="checkbox" checked={showBuildable} onChange={(e) => setShowBuildable(e.target.checked)} />
            Show buildable ground on turbine parcels
          </label>
          {showBuildable && (
            <p className="hint">
              {zoom < SOILS_MIN_ZOOM && "Zoom in to see buildable ground. "}
              <span className="swatch" style={{ background: BUILDABLE_COLOR }} /> slope 15% or less, outside the 100-year
              floodplain, soils not very limited for septic, in patches of half an acre or more. A screen, not a site
              evaluation.
            </p>
          )}
          <label className="toggle">
            <input type="checkbox" checked={showSoils} onChange={(e) => setShowSoils(e.target.checked)} />
            Show soils rated for septic
          </label>
          {showSoils && (
            <p className="hint">
              {zoom < SOILS_MIN_ZOOM && "Zoom in to see soils. "}
              Tennessee septic rating:{" "}
              {Object.entries(SEPTIC_COLORS).map(([label, color]) => (
                <span key={label}>
                  <span className="swatch" style={{ background: color }} /> {label.toLowerCase()}{" "}
                </span>
              ))}
              (USDA SSURGO, mapped at 1:24,000).
            </p>
          )}
        </Collapsible>
        {showScores && <Legend />}
      </div>
    </>
  );
}
