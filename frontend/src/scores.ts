// The latest hydro scores (Martin source `latest_hydro_scores`): map layers and the click popup.
// Color shows the hydro score, line style shows how much to trust it. See docs/SCORING.md.
import type { ExpressionSpecification, LayerSpecification, MapGeoJSONFeature } from "maplibre-gl";

export const CFS_TO_GPM = 448.831;

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

const SOURCE = { source: "scores", "source-layer": "latest_hydro_scores" } as const;
const ROUND = { "line-cap": "round", "line-join": "round" } as const;

// Bottom to top: filtered-out, head only, low, medium. One layer per tier so each gets its own
// line style (MapLibre can't vary dashes per feature).
export const SCORE_LAYERS: LayerSpecification[] = [
  {
    id: "scores-filtered",
    type: "line",
    ...SOURCE,
    filter: ["!", ["has", "tier"]],
    layout: ROUND,
    paint: { "line-color": FILTERED_COLOR, "line-opacity": 0.35, "line-width": width(0.6) },
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
  below_regression_min_drainage_area: "Basin is smaller than the low-flow equations allow (1.3 sq mi)",
  missing_drainage_area: "No drainage area in NHDPlus",
  missing_mean_flow: "No mean flow in NHDPlus",
  mean_flow_not_q90: "Average flow, not dry-season flow",
};

function flagText(flag: string): string {
  if (FLAG_TEXT[flag]) return FLAG_TEXT[flag];
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

function headline(p: Record<string, unknown>): string {
  if (p.tier === "head_only")
    return `<div class="score-line"><span class="badge head-only">Head only</span> ranked #${p.rank} overall</div>`;
  if (p.tier)
    return (
      `<div class="score-line"><span class="score">${fmt(p.hydro_score)}</span><span class="out-of">/ 100</span>` +
      ` <span>ranked #${p.rank} overall, #${p.tier_rank} of the ${esc(p.tier)}-confidence group</span></div>`
    );
  return `<div class="score-line"><span class="badge filtered">Not on the shortlist</span></div><p>${esc(p.summary)}</p>`;
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
      <dl>
        <dt>Head (net drop)</dt><dd>${withUnit(p.net_head_ft, "ft")}</dd>
        <dt>Firm flow</dt><dd>${flow === undefined ? "—" : `${fmt(flow, 2)} cfs (${fmt(flow * CFS_TO_GPM)} gpm)`}</dd>
        <dt>Firm power</dt><dd>${withUnit(p.firm_power_w, "W")}</dd>
      </dl>
      <p><b>Flow source:</b> ${flowSource(p.flow_method, p.flow_reference_gauge)}.</p>
      ${tier ? `<p><b>${tier.label}:</b> ${tier.detail}</p>` : ""}
      ${flags.length ? `<p><b>Flags:</b></p><ul class="flags">${flags.map((x) => `<li>${esc(flagText(x))}</li>`).join("")}</ul>` : ""}
    </div>`;
}
