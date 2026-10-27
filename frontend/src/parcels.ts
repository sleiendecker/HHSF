// The parcel shortlist (PART 23): one row per turbine parcel, ranked by the hydro score, from the
// PostgREST API (/api, docker-compose.yml service `api`; migration 0020). No owner names or addresses
// ever come through it. The filters that whittle it down, the sorting and the CSV happen here, in the browser.

export type PowerGroup = "kw" | "head_only" | "low_flow";
export type Status = "new" | "interested" | "ruled_out" | "visited";

export interface ParcelRow {
  county_geoid: string;
  parcel_id: string;
  county_name: string | null;
  rank: number; // by hydro, every county
  overall_score: number | null;
  hydro_subscore: number | null; // what the list ranks on (see HYDRO_TEXT)
  buildability_subscore: number | null;
  agriculture_subscore: number | null;
  privacy_subscore: number | null;
  logistics_subscore: number | null;
  power_group: PowerGroup;
  tier: string;
  corridor_id: number;
  stream_name: string | null;
  firm_power_w: number | null;
  max_power_w: number | null;
  net_head_ft: number | null;
  stretches: number;
  acres: number | null;
  buildable_acres: number | null;
  drive_minutes: number | null;
  open_acres: number | null;
  open_cropland_acres: number | null;
  prime_acres: number | null;
  buildings_quarter_mile: number | null;
  road_frontage_ft: number | null;
  public_road_ft: number | null;
  turbine_in_sfha: boolean | null;
  subdivision_score: number | null;
  flags: string[];
  bbox: [number, number, number, number];
  status: Status;
  note: string | null;
  status_updated_at: string | null;
}

export interface Factor {
  factor: string;
  reason: string;
  subscore: number;
  weight: number;
}

export interface Part {
  part: string;
  subscore: number;
  weight: number; // after rescaling for any part left out
  contribution: number;
  factors: Factor[];
  missing: string[];
}

export interface Check {
  topic: string;
  warn: boolean;
  text: string;
}

export interface ParcelDetail extends ParcelRow {
  deed_acres: number | null;
  checks: Check[];
  parts: { parts: Part[]; left_out: string[] };
  parcel_geom: GeoJSON.Geometry | null;
  stretch_geom: GeoJSON.Geometry | null;
}

// The score run behind each county's rows, with the overall score's weights (they add up to 1).
export interface ScoreRun {
  aoi_key: string;
  run_id: number;
  profile: string | null;
  finished_at: string | null;
  weights: Record<string, number> | null;
}

const API = `${window.location.origin}/api`;

export const keyOf = (r: { county_geoid: string; parcel_id: string }) => `${r.county_geoid}|${r.parcel_id}`;

export async function fetchParcels(): Promise<ParcelRow[]> {
  const response = await fetch(`${API}/parcels`);
  if (!response.ok) throw new Error(`The shortlist API answered ${response.status}`);
  return response.json();
}

export async function fetchScoreRuns(): Promise<ScoreRun[]> {
  const response = await fetch(`${API}/score_runs`);
  return response.ok ? response.json() : [];
}

// The weights every county was scored with, or null if they differ (or aren't known).
export function commonWeights(runs: ScoreRun[]): Record<string, number> | null {
  const all = runs.map((r) => JSON.stringify(r.weights));
  return runs.length > 0 && runs[0].weights && all.every((w) => w === all[0]) ? runs[0].weights : null;
}

export async function fetchDetail(r: ParcelRow): Promise<ParcelDetail> {
  const q = `county_geoid=eq.${encodeURIComponent(r.county_geoid)}&parcel_id=eq.${encodeURIComponent(r.parcel_id)}`;
  const response = await fetch(`${API}/parcel_detail?${q}`, {
    headers: { Accept: "application/vnd.pgrst.object+json" },
  });
  if (!response.ok) throw new Error(`The shortlist API answered ${response.status}`);
  return response.json();
}

export async function saveResearch(
  r: ParcelRow,
  status: Status,
  note: string | null,
): Promise<{ status: Status; note: string | null; updated_at: string }> {
  const response = await fetch(`${API}/rpc/set_research`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/vnd.pgrst.object+json" },
    body: JSON.stringify({ county_geoid: r.county_geoid, parcel_id: r.parcel_id, status, note }),
  });
  if (!response.ok) throw new Error(`Couldn't save (${response.status})`);
  return response.json();
}

// --- Words ----------------------------------------------------------------------

export const GROUP_LABEL: Record<PowerGroup, string> = { kw: "1 kW+", head_only: "Head only", low_flow: "Low flow" };
export const GROUP_TITLE: Record<PowerGroup, string> = {
  kw: "At least 1 kW of firm power on the estimated dry-season flow",
  head_only: "Enough drop, but the basin is too small for any flow estimate",
  low_flow: "Under 1 kW on a regional estimate for a typical basin; a spring could change it",
};

export const STATUSES: Status[] = ["new", "interested", "ruled_out", "visited"];
export const STATUS_LABEL: Record<Status, string> = {
  new: "New",
  interested: "Interested",
  ruled_out: "Ruled out",
  visited: "Visited",
};

// `weight`: FR-7's (docs/SCORING.md), shown until the score runs' own weights load.
export const PARTS: { key: string; column: keyof ParcelRow; label: string; weight: number; what: string }[] = [
  {
    key: "hydro",
    column: "hydro_subscore",
    label: "Hydro",
    weight: 0.4,
    what: "How much power the stretch could make all year (70%) and how much drop it has (30%). Under 1 kW it's small: 1 kW scores 50.",
  },
  {
    key: "buildability",
    column: "buildability_subscore",
    label: "Buildability",
    weight: 0.2,
    what: "Buildable acres on the parcel (gentle, out of the floodplain, soils fit for septic), how far the nearest is from the turbine, and how far a public road is.",
  },
  {
    key: "agriculture",
    column: "agriculture_subscore",
    label: "Agriculture",
    weight: 0.2,
    what: "Prime farmland, cropland soils, the pasture the soils could grow, and cropland that's open (not forest) today.",
  },
  {
    key: "privacy",
    column: "privacy_subscore",
    label: "Privacy",
    weight: 0.1,
    what: "Other buildings within 1/4 mile of the house site, the nearest one, how built-up the area is, and the woods between it and the road.",
  },
  {
    key: "logistics",
    column: "logistics_subscore",
    label: "Logistics",
    weight: 0.1,
    what: "The drive from downtown Chattanooga: 30 minutes scores 100, an hour 70, two hours 0.",
  },
];

// Plain words for the flags (src/hhsf/analysis/shortlist.py, export.py, subdivision.py).
export const FLAG_TEXT: Record<string, string> = {
  low_flow_estimate: "Power is a regional low-flow estimate",
  low_confidence_flow: "1 kW+ on a rough flow estimate",
  no_flow_estimate: "No flow estimate",
  streamstats_drainage_area_mismatch: "StreamStats outlined a different basin",
  streamstats_pour_point_moved: "StreamStats asked a little way upstream",
  below_regression_min_drainage_area: "Basin smaller than the low-flow equations allow",
  missing_drainage_area: "No drainage area",
  turbine_outside_parcel: "Turbine end in a gap between parcels",
  parcel_under_min_acres: "Parcel under 10 acres",
  deed_acres_differ: "Deed and outline acres differ",
  turbine_in_sfha: "Turbine end in the 100-year floodplain",
  stretch_in_sfha: "Part of the stretch in the 100-year floodplain",
  outside_drive_area: "Over an hour's drive",
  no_drive_time: "No drive time",
  nearest_road_not_public: "Nearest road is a trail or private",
  no_buildable_ground: "No buildable ground",
  soils_not_loaded: "Part of the parcel has no soil survey",
  neighbor_within_660_ft: "A building within 660 ft",
  buildings_on_parcel: "Buildings on the parcel",
  no_road_frontage: "No public road frontage",
  stretch_crosses_parcels: "Stretch crosses other parcels",
  no_house_site: "No house site to cut a piece around",
  access_crosses_parcels: "Driveway or wire crosses other parcels",
  long_driveway: "Long driveway",
  no_roads_loaded: "No roads loaded",
};

// --- Formatting -------------------------------------------------------------------

export function num(value: number | null | undefined, digits = 0): string {
  return typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : "—";
}

export function power(w: number | null): string {
  if (w === null) return "—";
  if (w < 1) return "<1 W";
  return w >= 1000 ? `${(w / 1000).toFixed(1)} kW` : `${Math.round(w)} W`;
}

// What "buildable" means, for every place the list shows it. Keep in step with config/buildability.yaml
// (min_patch_acres, max_slope_pct, septic_excluded) and the floodplain rule in hhsf.analysis.buildable.
export const BUILDABLE_TEXT =
  "Buildable: patches of ½ acre or more, sloped 15% or less, outside the 100-year floodplain, on soils rated " +
  "OK for a septic field. A screen, not a perc test.";

export interface Badge {
  text: string;
  tone: "good" | "bad" | "plain";
  title?: string;
}

// Short "why" badges: what stands out about the parcel, good or bad.
export function badges(r: ParcelRow): Badge[] {
  const out: Badge[] = [];
  if (r.power_group === "kw") out.push({ text: power(r.firm_power_w), tone: "good" });
  else if (r.power_group === "low_flow") out.push({ text: `${power(r.firm_power_w)} est.`, tone: "plain" });
  else out.push({ text: `${num(r.net_head_ft)} ft drop`, tone: "plain" });
  if (r.buildable_acres !== null) {
    if (r.buildable_acres >= 5)
      out.push({ text: `${num(r.buildable_acres)} buildable ac`, tone: "good", title: BUILDABLE_TEXT });
    else if (r.buildable_acres === 0) out.push({ text: "no buildable ground", tone: "bad", title: BUILDABLE_TEXT });
  }
  if (r.drive_minutes !== null) out.push({ text: `${num(r.drive_minutes)} min`, tone: r.drive_minutes <= 60 ? "good" : "bad" });
  if (r.open_acres !== null && r.open_acres >= 5) out.push({ text: `${num(r.open_acres)} open ac`, tone: "good" });
  if ((r.privacy_subscore ?? 0) >= 90) out.push({ text: "secluded", tone: "good" });
  return out;
}

// --- Filters and sorting ------------------------------------------------------------

// How the hydro score ranks every parcel on one scale, for the list and its help.
export const HYDRO_TEXT =
  "The hydro score (0–100) is 70% firm power and 30% net head. 1 kW scores 50 and 3 kW 100; head scores 0 at " +
  "10 ft and 100 at 200 ft. Under 1 kW (low flow) the power curve is extended down to 0 W → 0, as in the overall " +
  "score's hydro part, so every parcel has a comparable score; head-only parcels (no flow estimate) score on " +
  "head alone. Ties go to more power, then more head.";

// A filter whittles the list down. Each is off until ticked; `value` is its threshold (or choice).
interface FilterSpec {
  id: string;
  label: string;
  unit?: string;
  hint: string;
  kind: "min" | "max" | "flag" | "choice";
  value: number | string; // the default threshold
  step?: number;
  pass: (r: ParcelRow, value: number | string) => boolean;
}

const atLeast = (v: number | null, min: number | string) => v !== null && v >= Number(min);
const atMost = (v: number | null, max: number | string) => v !== null && v <= Number(max);

export const FILTERS: FilterSpec[] = [
  {
    id: "kw",
    label: "1 kW+ only",
    kind: "flag",
    value: 0,
    hint: "Only parcels with a stretch making 1 kW or more on the estimated dry-season flow.",
    pass: (r) => r.power_group === "kw",
  },
  {
    id: "power",
    label: "Power at least",
    unit: "W",
    kind: "min",
    value: 500,
    step: 50,
    hint: "Firm power of the parcel's best stretch. Under 1 kW it's a regional estimate that can't see springs.",
    pass: (r, v) => atLeast(r.firm_power_w, v),
  },
  {
    id: "buildable",
    label: "Buildable land at least",
    unit: "ac",
    kind: "min",
    value: 5,
    hint: BUILDABLE_TEXT,
    pass: (r, v) => atLeast(r.buildable_acres, v),
  },
  {
    id: "drive",
    label: "Drive at most",
    unit: "min",
    kind: "max",
    value: 60,
    step: 5,
    hint: "By car from downtown Chattanooga to the turbine end, no traffic.",
    pass: (r, v) => atMost(r.drive_minutes, v),
  },
  {
    id: "minAcres",
    label: "Parcel at least",
    unit: "ac",
    kind: "min",
    value: 10,
    hint: "Measured from the parcel outline.",
    pass: (r, v) => atLeast(r.acres, v),
  },
  {
    id: "maxAcres",
    label: "Parcel at most",
    unit: "ac",
    kind: "max",
    value: 1000,
    step: 50,
    hint: "Hides the huge timber and conservation tracts.",
    pass: (r, v) => atMost(r.acres, v),
  },
  {
    id: "privacy",
    label: "Buildings within ¼ mile at most",
    kind: "max",
    value: 2,
    hint: "Other buildings (400 sq ft or more, barns too) within 1/4 mile of the house site, not on the parcel.",
    pass: (r, v) => atMost(r.buildings_quarter_mile, v),
  },
  {
    id: "frontage",
    label: "Has public road frontage",
    kind: "flag",
    value: 0,
    hint: "At least 50 ft of public road on or along the parcel (Census roads), so a piece could be cut with access.",
    pass: (r) => atLeast(r.road_frontage_ft, 50),
  },
  {
    id: "road",
    label: "Public road within",
    unit: "ft",
    kind: "max",
    value: 1320,
    step: 100,
    hint: "Straight-line distance from the turbine end to the nearest public road.",
    pass: (r, v) => atMost(r.public_road_ft, v),
  },
  {
    id: "flood",
    label: "Turbine end out of the 100-year floodplain",
    kind: "flag",
    value: 0,
    hint: "FEMA's map at the turbine end. Small creeks are mostly unmapped; unmapped counts as outside.",
    pass: (r) => r.turbine_in_sfha !== true,
  },
  {
    id: "prime",
    label: "Prime farmland at least",
    unit: "ac",
    kind: "min",
    value: 5,
    hint: "Prime farmland and farmland of statewide importance on the parcel (USDA soil survey), wooded or not.",
    pass: (r, v) => atLeast(r.prime_acres, v),
  },
  {
    id: "openCrop",
    label: "Open cropland at least",
    unit: "ac",
    kind: "min",
    value: 5,
    hint: "Field, pasture or crops today (NLCD 2021) on cropland soils (class 1–3).",
    pass: (r, v) => atLeast(r.open_cropland_acres, v),
  },
  {
    id: "county",
    label: "County",
    kind: "choice",
    value: "",
    hint: "The parcel's own county.",
    pass: (r, v) => !v || r.county_name === v,
  },
  {
    id: "status",
    label: "Status",
    kind: "choice",
    value: "open",
    hint: "Your research status. Not ruled out: everything but Ruled out.",
    pass: (r, v) => !v || (v === "open" ? r.status !== "ruled_out" : r.status === v),
  },
];

export type Filters = Record<string, { on: boolean; value: number | string }>;

export const DEFAULT_FILTERS: Filters = Object.fromEntries(FILTERS.map((f) => [f.id, { on: false, value: f.value }]));

const FILTERS_KEY = "hhsf.shortlist.filters";

// Remembered per browser. Storage can be blocked (private windows): then it starts from the defaults.
export function savedFilters(): Filters {
  try {
    const saved = JSON.parse(localStorage.getItem(FILTERS_KEY) ?? "{}");
    return Object.fromEntries(
      FILTERS.map((f) => {
        const s = saved[f.id];
        const ok = s && typeof s.on === "boolean" && typeof s.value === typeof f.value;
        return [f.id, ok ? { on: s.on, value: s.value } : DEFAULT_FILTERS[f.id]];
      }),
    );
  } catch {
    return DEFAULT_FILTERS;
  }
}

export function saveFilters(filters: Filters) {
  try {
    localStorage.setItem(FILTERS_KEY, JSON.stringify(filters));
  } catch {
    // Not remembered.
  }
}

export function activeFilters(filters: Filters): number {
  return FILTERS.filter((f) => filters[f.id]?.on).length;
}

export function applyFilters(rows: ParcelRow[], filters: Filters): ParcelRow[] {
  const on = FILTERS.filter((f) => filters[f.id]?.on);
  return rows.filter((r) => on.every((f) => f.pass(r, filters[f.id].value)));
}

export type SortKey = keyof ParcelRow | "parcel";
export interface Sort {
  key: SortKey;
  desc: boolean;
}

const GROUP_ORDER: Record<PowerGroup, number> = { kw: 0, head_only: 1, low_flow: 2 };
const STATUS_ORDER: Record<Status, number> = { interested: 0, visited: 1, new: 2, ruled_out: 3 };

function sortValue(r: ParcelRow, key: SortKey): number | string | null {
  if (key === "parcel") return `${r.county_name} ${r.parcel_id}`;
  if (key === "power_group") return GROUP_ORDER[r.power_group];
  if (key === "status") return STATUS_ORDER[r.status];
  const v = r[key];
  return typeof v === "number" || typeof v === "string" ? v : null;
}

// Missing values sort last either way; ties keep the hydro rank (then power, then head).
export function sortRows(rows: ParcelRow[], s: Sort): ParcelRow[] {
  return [...rows].sort((a, b) => {
    const x = sortValue(a, s.key);
    const y = sortValue(b, s.key);
    if (x === y) return a.rank - b.rank;
    if (x === null) return 1;
    if (y === null) return -1;
    const order = x < y ? -1 : 1;
    return s.desc ? -order : order;
  });
}

// --- CSV --------------------------------------------------------------------------

const CSV_COLUMNS: [string, (r: ParcelRow) => unknown][] = [
  ["hydro_rank", (r) => r.rank],
  ["county", (r) => r.county_name],
  ["parcel_id", (r) => r.parcel_id],
  ["status", (r) => STATUS_LABEL[r.status]],
  ["note", (r) => r.note],
  ["hydro_score", (r) => r.hydro_subscore],
  ["overall_score", (r) => r.overall_score],
  ["buildability", (r) => r.buildability_subscore],
  ["agriculture", (r) => r.agriculture_subscore],
  ["privacy", (r) => r.privacy_subscore],
  ["logistics", (r) => r.logistics_subscore],
  ["power_group", (r) => GROUP_LABEL[r.power_group]],
  ["stream", (r) => r.stream_name],
  ["firm_power_w", (r) => r.firm_power_w],
  ["max_power_on_parcel_w", (r) => r.max_power_w],
  ["net_head_ft", (r) => r.net_head_ft],
  ["stretches", (r) => r.stretches],
  ["acres", (r) => r.acres],
  ["buildable_acres", (r) => r.buildable_acres],
  ["drive_minutes", (r) => r.drive_minutes],
  ["open_acres", (r) => r.open_acres],
  ["open_cropland_acres", (r) => r.open_cropland_acres],
  ["prime_acres", (r) => r.prime_acres],
  ["buildings_quarter_mile", (r) => r.buildings_quarter_mile],
  ["road_frontage_ft", (r) => r.road_frontage_ft],
  ["public_road_ft", (r) => r.public_road_ft],
  ["turbine_in_100yr_floodplain", (r) => (r.turbine_in_sfha === null ? "" : r.turbine_in_sfha ? "yes" : "no")],
  ["subdivision_score", (r) => r.subdivision_score],
  ["flags", (r) => r.flags.join(";")],
  ["lon", (r) => ((r.bbox[0] + r.bbox[2]) / 2).toFixed(5)],
  ["lat", (r) => ((r.bbox[1] + r.bbox[3]) / 2).toFixed(5)],
];

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

// No owner names: the API never has them.
// `position`: each row's place in the filtered list, by hydro.
export function toCsv(rows: ParcelRow[], position: Map<string, number>): string {
  const lines = ["position," + CSV_COLUMNS.map(([name]) => name).join(",")];
  for (const r of rows)
    lines.push([position.get(keyOf(r)), ...CSV_COLUMNS.map(([, get]) => get(r))].map(cell).join(","));
  return lines.join("\r\n") + "\r\n";
}

export function downloadCsv(rows: ParcelRow[], position: Map<string, number>) {
  const blob = new Blob([toCsv(rows, position)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `hhsf-shortlist-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
