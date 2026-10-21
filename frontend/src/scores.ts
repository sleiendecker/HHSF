// The latest hydro scores (Martin source `latest_hydro_scores`): map layers and the click popup.
// Color shows the hydro score, line style shows how much to trust it. See docs/SCORING.md.
import type { ExpressionSpecification, LayerSpecification, MapGeoJSONFeature } from "maplibre-gl";

export const CFS_TO_GPM = 448.831;
const FT_PER_M = 3.28084;
const FT_PER_MI = 5280;

// Score color ramp, low to high (light amber to dark brown). Scored corridors run from
// about 35 (1 kW, almost no head) to 100. Shared with the legend.
export const SCORE_STOPS: [number, string][] = [
  [35, "#fec44f"],
  [55, "#fe9929"],
  [70, "#ec7014"],
  [85, "#cc4c02"],
  [100, "#8c2d04"],
];
export const HEAD_ONLY_COLOR = "#6a51a3";
export const LOW_FLOW_COLOR = "#3f7f9a";
export const FILTERED_COLOR = "#8a8a8a";

const scoreColor: ExpressionSpecification = [
  "interpolate",
  ["linear"],
  ["coalesce", ["get", "hydro_score"], 0],
  ...SCORE_STOPS.flat(),
] as ExpressionSpecification;

// Line width grows with zoom; `scale` makes the more trustworthy tiers stand out, `extra` adds a casing.
function width(scale: number, extra = 0): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], 9, 1.5 * scale + extra, 12, 3 * scale + extra, 15, 6 * scale + extra];
}

// A white outline under the shortlisted tiers, 2 px wider than each tier's line (and its casing) on
// both sides; 1 px for head-only streams, which are many and would otherwise turn into white blobs.
// Only drawn on the satellite and topo basemaps (src/basemaps.ts sets its opacity).
function haloWidth(): ExpressionSpecification {
  const tier = (medium: number, low: number, headOnly: number): ExpressionSpecification =>
    ["match", ["get", "tier"], "medium", medium, "low", low, headOnly];
  const at = (base: number): ExpressionSpecification => ["+", ["*", base, tier(1.6, 1.1, 0.8)], tier(4, 4, 2)];
  return ["interpolate", ["linear"], ["zoom"], 9, at(1.5), 12, at(3), 15, at(6)];
}

const SOURCE = { source: "scores", "source-layer": "latest_hydro_scores" } as const;
const ROUND = { "line-cap": "round", "line-join": "round" } as const;

// Bottom to top: halo, filtered-out, low flow, head only, low, medium. One layer per tier so each gets its own
// line style (MapLibre can't vary dashes per feature).
export const SCORE_LAYERS: LayerSpecification[] = [
  {
    id: "scores-halo",
    type: "line",
    ...SOURCE,
    // Not under low-flow streams: there are hundreds and they'd turn into white blobs.
    filter: ["in", ["get", "tier"], ["literal", ["medium", "low", "head_only"]]],
    layout: ROUND,
    paint: { "line-color": "#fff", "line-opacity": 0, "line-width": haloWidth() },
  },
  {
    id: "scores-filtered",
    type: "line",
    ...SOURCE,
    filter: ["!", ["has", "tier"]],
    layout: ROUND,
    paint: { "line-color": FILTERED_COLOR, "line-opacity": 0.35, "line-width": width(0.6) },
  },
  {
    // Under the power target on a low-confidence flow: listed (D-01), but drawn quietly.
    id: "scores-low-flow",
    type: "line",
    ...SOURCE,
    filter: ["==", ["get", "tier"], "low_flow"],
    layout: ROUND,
    paint: { "line-color": LOW_FLOW_COLOR, "line-opacity": 0.7, "line-width": width(0.6) },
  },
  {
    id: "scores-head-only",
    type: "line",
    ...SOURCE,
    filter: ["==", ["get", "tier"], "head_only"],
    layout: ROUND,
    paint: { "line-color": HEAD_ONLY_COLOR, "line-width": width(0.8), "line-dasharray": [0, 2] },
  },
  {
    id: "scores-low",
    type: "line",
    ...SOURCE,
    filter: ["==", ["get", "tier"], "low"],
    paint: { "line-color": scoreColor, "line-width": width(1.1), "line-dasharray": [2, 1.5] },
  },
  {
    id: "scores-medium-casing",
    type: "line",
    ...SOURCE,
    filter: ["==", ["get", "tier"], "medium"],
    layout: ROUND,
    paint: { "line-color": "#222", "line-width": width(1.6, 2) },
  },
  {
    id: "scores-medium",
    type: "line",
    ...SOURCE,
    filter: ["==", ["get", "tier"], "medium"],
    layout: ROUND,
    paint: { "line-color": scoreColor, "line-width": width(1.6) },
  },
  {
    // Rank numbers ("#3") on the shortlisted streams once zoomed in.
    id: "scores-rank",
    type: "symbol",
    ...SOURCE,
    filter: ["in", ["get", "tier"], ["literal", ["medium", "low"]]],
    minzoom: 11,
    layout: {
      // Upright and allowed on bends: streams wiggle too much for text that follows the line.
      "symbol-placement": "line-center",
      "text-rotation-alignment": "viewport",
      "text-max-angle": 180,
      "text-field": ["concat", "#", ["to-string", ["get", "rank"]]],
      "text-font": ["Noto Sans Bold"],
      "text-size": 12,
    },
    paint: { "text-color": "#222", "text-halo-color": "#fff", "text-halo-width": 2 },
  },
  {
    // Invisible wide lines on top so thin corridors are easy to click.
    id: "scores-hit",
    type: "line",
    ...SOURCE,
    paint: { "line-color": "#000", "line-opacity": 0, "line-width": 14 },
  },
];

// --- Popup --------------------------------------------------------------------

const TIER_TEXT: Record<string, { label: string; detail: string }> = {
  medium: {
    label: "Medium confidence",
    detail:
      "Flow is a USGS StreamStats estimate inside the range its equations were built for. " +
      "Those equations are still about ±60%, so check the flow on the visit.",
  },
  low: {
    label: "Low confidence",
    detail: "Flow is a rough estimate (see flow source). Ranked below every medium-confidence stream.",
  },
  head_only: {
    label: "Head only",
    detail:
      "The basin is too small for any flow estimate, so there's no power number or hydro score. " +
      "Ranked on drop alone, after every stream with a power estimate. Check the flow on the visit.",
  },
  low_flow: {
    label: "Low flow",
    detail:
      "The power estimate is below the 1 kW target, but the flow behind it is a rough estimate, so the " +
      "stream stays on the list, ranked by power after every other listed stream. A spring could change it.",
  },
};

function flowSource(method: unknown, gauge: unknown): string {
  switch (method) {
    case "streamstats_q90":
      return "USGS StreamStats: the flow the stream beats 90% of days (Q90)";
    case "gauge_drainage_area_ratio": {
      const where = gauge === "03571000" ? "the Whitwell stream gauge" : `USGS stream gauge ${esc(gauge)}`;
      return (
        `Scaled from ${where} by drainage area. Treat it as an upper bound: ` +
        "it runs about 10× StreamStats where both exist"
      );
    }
    case "regional_streamstats_yield":
      return (
        "The typical StreamStats low flow per square mile of the bigger streams in the same river basin, " +
        "scaled to this stream's drainage area. The basin is too small for StreamStats itself, and a " +
        "spring-fed stream can carry far more"
      );
    case "nhdplus_erom":
      return "NHDPlus mean annual flow: an average, not a dry-season flow, so it overstates what a turbine can count on";
    default:
      return "No flow estimate";
  }
}

// Plain words for hydro_corridors.flow_flags (src/hhsf/analysis/hydro.py and run.py).
const FLAG_TEXT: Record<string, string> = {
  streamstats_disabled: "StreamStats wasn't used for this run",
  streamstats_unavailable: "StreamStats was down when this ran",
  streamstats_point_failed: "StreamStats couldn't answer for this stream",
  streamstats_not_used: "StreamStats wasn't used",
  streamstats_drainage_area_mismatch: "StreamStats outlined a different basin than this stream's, so its answer was ignored",
  streamstats_no_estimate: "StreamStats gave no low-flow number",
  streamstats_pour_point_moved:
    "StreamStats was asked a little way up the stream (its first answer outlined the wrong basin), then scaled to the stream's end",
  below_regression_min_drainage_area: "Basin is smaller than the low-flow equations allow (1.3 sq mi)",
  missing_drainage_area: "No drainage area in NHDPlus",
  missing_mean_flow: "No mean flow in NHDPlus",
  mean_flow_not_q90: "Average flow, not dry-season flow",
};

function flagText(flag: string): string {
  if (FLAG_TEXT[flag]) return FLAG_TEXT[flag];
  if (flag.startsWith("regional_yield_basin:"))
    return `Low flow per square mile borrowed from river basin (HUC) ${flag.slice("regional_yield_basin:".length)}`;
  if (flag.startsWith("out_of_range:"))
    return `Basin is outside the range the equations were built for (${flag.slice("out_of_range:".length)})`;
  const ratio = flag.match(/^area_ratio_outside_([\d.]+)_([\d.]+)$/);
  if (ratio) return `Basin size is far from the gauge's (outside ${ratio[1]}–${ratio[2]}× its area), so the scaling is shakier`;
  return flag;
}

interface Factor {
  factor: string;
  value: number;
  subscore: number;
  weight: number;
  contribution: number;
}

function factorLine(f: Factor): string {
  const what = f.factor === "power" ? `${fmt(f.value)} W firm power` : `${fmt(f.value)} ft net head`;
  return (
    `<li><b>${f.factor === "power" ? "Power" : "Head"}:</b> ${what} scores ${fmt(f.subscore)} of 100` +
    (f.weight < 1 ? `, counts ${fmt(f.weight * 100)}% → <b>${fmt(f.contribution, 1)}</b> points` : "") +
    "</li>"
  );
}

function fmt(value: unknown, digits = 0): string {
  return typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : "—";
}

function withUnit(value: unknown, unit: string): string {
  return typeof value === "number" ? `${fmt(value)} ${unit}` : "—";
}

function esc(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function feet(m: unknown): number | undefined {
  return typeof m === "number" ? m * FT_PER_M : undefined;
}

// "850 ft" or "1.4 mi".
function distance(m: unknown): string {
  const ft = feet(m);
  if (ft === undefined) return "—";
  return ft < FT_PER_MI ? `${fmt(Math.round(ft / 10) * 10)} ft` : `${fmt(ft / FT_PER_MI, 1)} mi`;
}

// Which part of the stream the corridor is (hydro_corridors.head_method, src/hhsf/analysis/stretch.py).
function stretchText(p: Record<string, unknown>): string {
  if (p.head_method !== "dem_3dep_steepest_stretch")
    return (
      `<b>Whole stream reach</b> (${distance(p.corridor_length_m)}): this run didn't use the elevation model, ` +
      "so head assumes a penstock as long as the whole reach, which overstates long reaches."
    );
  const whole = typeof p.corridor_length_m === "number" && typeof p.reach_length_m === "number" &&
    p.corridor_length_m >= p.reach_length_m - 1;
  if (whole)
    return (
      `<b>The whole stream reach</b> (${distance(p.reach_length_m)}): it's shorter than the longest penstock ` +
      "allowed, so one pipe could cover all of it."
    );
  const reachDrop = feet(p.reach_drop_m);
  return (
    `<b>The steepest ${distance(p.corridor_length_m)}</b> of a ${distance(p.reach_length_m)} stream reach` +
    (reachDrop === undefined ? "" : ` that drops ${fmt(reachDrop)} ft in all`) +
    ". Only that stretch is drawn: the intake would go at its upstream end, the turbine at its downstream end."
  );
}

// Each county (AOI) is ranked on its own, so a rank says which county's list it's on.
function rankedIn(p: Record<string, unknown>): string {
  return `ranked #${p.rank} ${p.aoi_name ? `in ${esc(p.aoi_name)}` : "overall"}`;
}

function headline(p: Record<string, unknown>): string {
  if (p.tier === "head_only")
    return `<div class="score-line"><span class="badge head-only">Head only</span> ${rankedIn(p)}</div>`;
  if (p.tier === "low_flow")
    return (
      `<div class="score-line"><span class="badge low-flow">Low flow</span> ${rankedIn(p)};` +
      ` under the 1 kW target</div>`
    );
  if (p.tier)
    return (
      `<div class="score-line"><span class="score">${fmt(p.hydro_score)}</span><span class="out-of">/ 100</span>` +
      ` <span>${rankedIn(p)}; #${p.tier_rank} of the ${esc(p.tier)}-confidence group</span></div>`
    );
  return `<div class="score-line"><span class="badge filtered">Not on the shortlist</span></div><p>${esc(p.summary)}</p>`;
}

// The parcel at the bottom of the stretch (migration 0010). Owners are only in the local exports.
function parcelText(p: Record<string, unknown>): string {
  if (typeof p.turbine_parcel_id !== "string") return "No parcel loaded here (outside the loaded counties)";
  const deed = typeof p.turbine_parcel_deed_acres === "number" ? `, deed ${fmt(p.turbine_parcel_deed_acres, 1)}` : "";
  const away =
    typeof p.turbine_parcel_distance_m === "number" && p.turbine_parcel_distance_m > 0
      ? `; nearest one, ${distance(p.turbine_parcel_distance_m)} away`
      : "";
  return `${esc(p.turbine_parcel_id)}: ${fmt(p.turbine_parcel_acres, 1)} acres${deed}${away}`;
}

// FEMA's zone at the bottom of the stretch (migration 0011), as src/hhsf/analysis/export.py labels it.
export function floodZoneLabel(zone: string, subtype: unknown): string {
  if (typeof subtype !== "string" || !subtype) return zone;
  if (subtype.includes("FLOODWAY")) return `${zone} floodway`;
  if (subtype.includes("0.2 PCT")) return `${zone} (0.2% annual chance)`;
  if (subtype.includes("MINIMAL")) return `${zone} (minimal hazard)`;
  return `${zone} (${subtype.toLowerCase()})`;
}

function floodText(p: Record<string, unknown>): string {
  if (typeof p.turbine_flood_zone !== "string") return "Not on a FEMA flood map (or flood zones not loaded)";
  const label = esc(floodZoneLabel(p.turbine_flood_zone, p.turbine_flood_subtype));
  return p.turbine_in_sfha === true ? `<b>${label}</b>: in the 100-year floodplain` : label;
}

function stretchFloodText(p: Record<string, unknown>): string {
  if (typeof p.stretch_sfha_m !== "number") return "—";
  return p.stretch_sfha_m > 0 ? `${distance(p.stretch_sfha_m)} in the 100-year floodplain` : "None in the 100-year floodplain";
}

export function scorePopupHtml(f: MapGeoJSONFeature): string {
  const p = f.properties as Record<string, unknown>;
  let factors: Factor[] = [];
  try {
    factors = JSON.parse(String(p.factors ?? "[]"));
  } catch {
    // Older view without factors: show the numbers without the breakdown.
  }
  const flags = String(p.flow_flags ?? "")
    .split(",")
    .filter(Boolean);
  const tier = TIER_TEXT[String(p.tier)];
  const flow = typeof p.firm_flow_cfs === "number" ? p.firm_flow_cfs : undefined;

  return `
    <div class="popup">
      <strong>${esc(p.stream_name ?? "Unnamed stream")}</strong>
      ${headline(p)}
      ${factors.length ? `<ul class="factors">${factors.map(factorLine).join("")}</ul>` : ""}
      <p>${stretchText(p)}</p>
      <dl>
        <dt>Head (net drop)</dt><dd>${withUnit(p.net_head_ft, "ft")}</dd>
        <dt>Firm flow</dt><dd>${flow === undefined ? "—" : `${fmt(flow, 2)} cfs (${fmt(flow * CFS_TO_GPM)} gpm)`}</dd>
        <dt>Firm power</dt><dd>${withUnit(p.firm_power_w, "W")}</dd>
        <dt>Turbine parcel</dt><dd>${parcelText(p)}</dd>
        <dt>Parcels crossed</dt><dd>${fmt(p.parcels_crossed)}</dd>
        <dt>Turbine flood zone</dt><dd>${floodText(p)}</dd>
        <dt>Stretch flooding</dt><dd>${stretchFloodText(p)}</dd>
      </dl>
      <p><b>Flow source:</b> ${flowSource(p.flow_method, p.flow_reference_gauge)}.</p>
      ${tier ? `<p><b>${tier.label}:</b> ${tier.detail}</p>` : ""}
      ${flags.length ? `<p><b>Flags:</b></p><ul class="flags">${flags.map((x) => `<li>${esc(flagText(x))}</li>`).join("")}</ul>` : ""}
    </div>`;
}
