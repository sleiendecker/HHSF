// The parcel shortlist (PART 23): one row per turbine parcel, ranked by the overall score, from the
// PostgREST API (/api, docker-compose.yml service `api`; migration 0020). No owner names or addresses
// ever come through it. Filtering, sorting and the CSV happen here, in the browser.

export type PowerGroup = "kw" | "head_only" | "low_flow";
export type Status = "new" | "interested" | "ruled_out" | "visited";

export interface ParcelRow {
  county_geoid: string;
  parcel_id: string;
  county_name: string | null;
  rank: number;
  overall_score: number | null;
  hydro_subscore: number | null;
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

// Short "why" badges: what stands out about the parcel, good or bad.
export function badges(r: ParcelRow): { text: string; tone: "good" | "bad" | "plain" }[] {
  const out: { text: string; tone: "good" | "bad" | "plain" }[] = [];
  if (r.power_group === "kw") out.push({ text: power(r.firm_power_w), tone: "good" });
  else if (r.power_group === "low_flow") out.push({ text: `${power(r.firm_power_w)} est.`, tone: "plain" });
  else out.push({ text: `${num(r.net_head_ft)} ft drop`, tone: "plain" });
  if (r.buildable_acres !== null) {
    if (r.buildable_acres >= 5) out.push({ text: `${num(r.buildable_acres)} buildable ac`, tone: "good" });
    else if (r.buildable_acres === 0) out.push({ text: "no buildable ground", tone: "bad" });
  }
  if (r.drive_minutes !== null) out.push({ text: `${num(r.drive_minutes)} min`, tone: r.drive_minutes <= 60 ? "good" : "bad" });
  if (r.open_acres !== null && r.open_acres >= 5) out.push({ text: `${num(r.open_acres)} open ac`, tone: "good" });
  if ((r.privacy_subscore ?? 0) >= 90) out.push({ text: "secluded", tone: "good" });
  return out;
}

// --- Filters and sorting ------------------------------------------------------------

export interface Filters {
  group: "" | PowerGroup;
  minPowerW: number | null;
  maxDriveMin: number | null;
  minBuildableAc: number | null;
  maxParcelAc: number | null;
  county: string;
  status: "" | Status | "open"; // open: everything not ruled out
}

export const NO_FILTERS: Filters = {
  group: "",
  minPowerW: null,
  maxDriveMin: null,
  minBuildableAc: null,
  maxParcelAc: null,
  county: "",
  status: "",
};

export function applyFilters(rows: ParcelRow[], f: Filters): ParcelRow[] {
  return rows.filter(
    (r) =>
      (!f.group || r.power_group === f.group) &&
      (f.minPowerW === null || (r.firm_power_w ?? 0) >= f.minPowerW) &&
      (f.maxDriveMin === null || (r.drive_minutes !== null && r.drive_minutes <= f.maxDriveMin)) &&
      (f.minBuildableAc === null || (r.buildable_acres ?? 0) >= f.minBuildableAc) &&
      (f.maxParcelAc === null || (r.acres !== null && r.acres <= f.maxParcelAc)) &&
      (!f.county || r.county_name === f.county) &&
      (!f.status || (f.status === "open" ? r.status !== "ruled_out" : r.status === f.status)),
  );
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

// Missing values sort last either way; ties keep the overall rank.
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
  ["rank", (r) => r.rank],
  ["county", (r) => r.county_name],
  ["parcel_id", (r) => r.parcel_id],
  ["status", (r) => STATUS_LABEL[r.status]],
  ["note", (r) => r.note],
  ["overall_score", (r) => r.overall_score],
  ["hydro", (r) => r.hydro_subscore],
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
export function toCsv(rows: ParcelRow[]): string {
  const lines = [CSV_COLUMNS.map(([name]) => name).join(",")];
  for (const r of rows) lines.push(CSV_COLUMNS.map(([, get]) => cell(get(r))).join(","));
  return lines.join("\r\n") + "\r\n";
}

export function downloadCsv(rows: ParcelRow[]) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `hhsf-shortlist-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
