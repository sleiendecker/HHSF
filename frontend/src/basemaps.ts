// The map types under the data: the OpenFreeMap style the map loads with, plus USGS National Map
// imagery and topo drawn as raster layers on top of it. Switching only changes layer visibility:
// map.setStyle() would wipe the counties, streams and score layers. See config/data_sources.yaml.
import type maplibregl from "maplibre-gl";

export const LIGHT_STYLE = "https://tiles.openfreemap.org/styles/positron";

const USGS = "https://basemap.nationalmap.gov/arcgis/rest/services";
const USGS_CREDIT = '<a href="https://www.usgs.gov/programs/national-geospatial-program/national-map" target="_blank">USGS The National Map</a>';

export interface Basemap {
  id: string;
  label: string;
  // USGS tile service drawn over the light style; none for the light style itself.
  service?: string;
  attribution?: string;
  // Keep the light style's place and road names on top (the topo map has its own).
  labels: boolean;
  // How strongly to outline streams so they stand out from the background (0 = no outline).
  halo: number;
  boundaryColor: string;
  parcelColor: string;
}

export const BASEMAPS: Basemap[] = [
  { id: "light", label: "Light", labels: true, halo: 0, boundaryColor: "#555", parcelColor: "#c2549d" },
  {
    id: "satellite",
    label: "Satellite",
    service: "USGSImageryOnly",
    attribution: `${USGS_CREDIT}: Orthoimagery`,
    labels: true,
    halo: 0.9,
    boundaryColor: "#fff",
    parcelColor: "#ffd27f",
  },
  {
    id: "topo",
    label: "Topo",
    service: "USGSTopo",
    attribution: `${USGS_CREDIT}: US Topo`,
    labels: false,
    halo: 0.8,
    boundaryColor: "#555",
    parcelColor: "#c2549d",
  },
];

const STORAGE_KEY = "hhsf.basemap";

export function savedBasemap(): string {
  try {
    const id = localStorage.getItem(STORAGE_KEY);
    if (id && BASEMAPS.some((b) => b.id === id)) return id;
  } catch {
    // Storage blocked (private window, settings): start on the light map.
  }
  return BASEMAPS[0].id;
}

export function saveBasemap(id: string) {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Not remembered next time; nothing else to do.
  }
}

// Ids of the light style's own layers, recorded before any of ours are added.
let styleLayers: { id: string; label: boolean }[] = [];

// Call once on load, before adding the data layers. The USGS layers go just under the light style's
// labels, so on satellite the names still draw over the imagery.
export function addBasemapLayers(map: maplibregl.Map) {
  const layers = map.getStyle().layers;
  styleLayers = layers.map((l) => ({ id: l.id, label: l.type === "symbol" }));
  const firstLabel = layers.find((l) => l.type === "symbol")?.id;
  for (const b of BASEMAPS) {
    if (!b.service) continue;
    map.addSource(`basemap-${b.id}`, {
      type: "raster",
      tiles: [`${USGS}/${b.service}/MapServer/tile/{z}/{y}/{x}`],
      tileSize: 256,
      // USGS caches stop at zoom 16; MapLibre stretches those tiles past it.
      maxzoom: 16,
      attribution: b.attribution,
    });
    map.addLayer(
      { id: `basemap-${b.id}`, type: "raster", source: `basemap-${b.id}`, layout: { visibility: "none" } },
      firstLabel,
    );
  }
}

// Shows one basemap. Hidden layers drop their source's credit, so the attribution follows along.
export function showBasemap(map: maplibregl.Map, id: string) {
  const basemap = BASEMAPS.find((b) => b.id === id) ?? BASEMAPS[0];
  const show = (on: boolean) => (on ? "visible" : "none");
  for (const b of BASEMAPS) {
    if (b.service) map.setLayoutProperty(`basemap-${b.id}`, "visibility", show(b === basemap));
  }
  // The light style is hidden under the USGS maps (except its labels where wanted), so its credit goes too.
  for (const l of styleLayers) {
    map.setLayoutProperty(l.id, "visibility", show(!basemap.service || (l.label && basemap.labels)));
  }
  for (const halo of ["streams-halo", "scores-halo"]) map.setPaintProperty(halo, "line-opacity", basemap.halo);
  map.setPaintProperty("county-outline", "line-color", basemap.boundaryColor);
  map.setPaintProperty("parcel-outline", "line-color", basemap.parcelColor);
}
