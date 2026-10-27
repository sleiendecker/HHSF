import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  DEFAULT_FILTERS,
  BUILDABLE_TEXT,
  FILTERS,
  FLAG_TEXT,
  GROUP_LABEL,
  GROUP_TITLE,
  HYDRO_TEXT,
  PARTS,
  STATUSES,
  STATUS_LABEL,
  activeFilters,
  applyFilters,
  badges,
  commonWeights,
  downloadCsv,
  fetchDetail,
  fetchParcels,
  fetchScoreRuns,
  keyOf,
  num,
  power,
  saveFilters,
  saveResearch,
  savedFilters,
  sortRows,
  type Filters,
  type ParcelDetail,
  type ParcelRow,
  type ScoreRun,
  type Sort,
  type SortKey,
  type Status,
} from "./parcels";

const ROW_HEIGHT = 30;
const HYDRO_SORT: Sort = { key: "hydro_subscore", desc: true };

interface Column {
  key: SortKey;
  label: string;
  title: string;
  // `position`: the row's place in the filtered list, by hydro.
  render: (r: ParcelRow, position: number | undefined) => ReactNode;
  className?: string;
  desc?: boolean; // first click sorts high to low
}

const COLUMNS: Column[] = [
  { key: "rank", label: "#", title: "Place in this list, by hydro score", render: (_, i) => i, className: "num" },
  {
    key: "status",
    label: "Status",
    title: "Your research status",
    render: (r) => <span className={`status ${r.status}`}>{STATUS_LABEL[r.status]}</span>,
  },
  {
    key: "parcel",
    label: "Parcel",
    title: "County and parcel ID",
    render: (r) => (
      <>
        <span className="muted">{r.county_name}</span> {r.parcel_id}
      </>
    ),
    className: "parcel",
  },
  {
    key: "hydro_subscore",
    label: "Hydro",
    title: "Hydro score, 0–100: 70% power, 30% head (what the list is ranked on)",
    render: (r) => num(r.hydro_subscore),
    className: "num strong",
    desc: true,
  },
  {
    key: "power_group",
    label: "Power",
    title: "Power group",
    render: (r) => (
      <span className={`group ${r.power_group}`} title={GROUP_TITLE[r.power_group]}>
        {GROUP_LABEL[r.power_group]}
      </span>
    ),
  },
  { key: "badges" as SortKey, label: "Why", title: "What stands out", render: (r) => <Badges row={r} />, className: "why" },
  { key: "firm_power_w", label: "W", title: "Firm power of the best stretch, W", render: (r) => num(r.firm_power_w), className: "num", desc: true },
  { key: "net_head_ft", label: "Head", title: "Net head of the best stretch, ft", render: (r) => num(r.net_head_ft), className: "num", desc: true },
  { key: "stream_name", label: "Stream", title: "Best stream", render: (r) => r.stream_name ?? <span className="muted">unnamed</span>, className: "stream" },
  { key: "buildable_acres", label: "Build ac", title: BUILDABLE_TEXT, render: (r) => num(r.buildable_acres), className: "num", desc: true },
  { key: "drive_minutes", label: "Min", title: "Drive from downtown Chattanooga, minutes", render: (r) => num(r.drive_minutes), className: "num" },
  { key: "acres", label: "Acres", title: "Parcel acres", render: (r) => num(r.acres), className: "num", desc: true },
  { key: "buildings_quarter_mile", label: "Bldgs", title: "Other buildings within 1/4 mile of the house site", render: (r) => num(r.buildings_quarter_mile), className: "num" },
  { key: "open_cropland_acres", label: "Open crop ac", title: "Open cropland acres (field, pasture or crops on class 1–3 soils)", render: (r) => num(r.open_cropland_acres), className: "num", desc: true },
  { key: "stretches", label: "Str.", title: "Stretches on the parcel", render: (r) => r.stretches, className: "num", desc: true },
  { key: "overall_score", label: "Overall", title: "Overall score, 0–100 (FR-7; not the ranking)", render: (r) => num(r.overall_score), className: "num secondary", desc: true },
  { key: "subdivision_score", label: "Split", title: "Subdivision feasibility, 0–100", render: (r) => num(r.subdivision_score), className: "num secondary", desc: true },
];

function Badges({ row }: { row: ParcelRow }) {
  return (
    <>
      {badges(row).map((b) => (
        <span key={b.text} className={`why-badge ${b.tone}`} title={b.title}>
          {b.text}
        </span>
      ))}
    </>
  );
}

function FilterBar(props: { filters: Filters; counties: string[]; onChange: (f: Filters) => void }) {
  const { filters } = props;
  const set = (id: string, patch: Partial<Filters[string]>) =>
    props.onChange({ ...filters, [id]: { ...filters[id], ...patch } });
  return (
    <div className="filters">
      {FILTERS.map((f) => {
        const state = filters[f.id];
        const choices: [string, string][] =
          f.id === "county"
            ? [["", "All"], ...props.counties.map((c): [string, string] => [c, c])]
            : f.id === "status"
              ? [["open", "Not ruled out"], ...STATUSES.map((s): [string, string] => [s, STATUS_LABEL[s]])]
              : [];
        return (
          <div key={f.id} className={`filter${state.on ? " on" : ""}`} title={f.hint}>
            <label>
              <input type="checkbox" checked={state.on} onChange={(e) => set(f.id, { on: e.target.checked })} />
              {f.label}
            </label>
            {(f.kind === "min" || f.kind === "max") && (
              <span className="threshold">
                <input
                  type="number"
                  min={0}
                  step={f.step ?? 1}
                  value={state.value}
                  aria-label={`${f.label} (${f.unit ?? "count"})`}
                  // Changing the threshold turns the filter on.
                  onChange={(e) => e.target.value !== "" && set(f.id, { value: Number(e.target.value), on: true })}
                />
                {f.unit}
              </span>
            )}
            {f.kind === "choice" && (
              <select
                value={String(state.value)}
                aria-label={f.label}
                onChange={(e) => set(f.id, { value: e.target.value, on: true })}
              >
                {choices.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function Shortlist(props: { onSelect: (detail: ParcelDetail | null, row: ParcelRow | null) => void }) {
  const [rows, setRows] = useState<ParcelRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(savedFilters);
  const [sort, setSort] = useState<Sort>(HYDRO_SORT);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ParcelDetail | null>(null);
  const [help, setHelp] = useState(false);
  const [runs, setRuns] = useState<ScoreRun[]>([]);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(400);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchParcels()
      .then(setRows)
      .catch((e) => setError(String(e.message ?? e)));
    fetchScoreRuns()
      .then(setRuns)
      .catch(() => setRuns([]));
  }, []);

  useEffect(() => saveFilters(filters), [filters]);

  useEffect(() => {
    const el = body.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setHeight(el.clientHeight));
    observer.observe(el);
    return () => observer.disconnect();
  }, [rows !== null]);

  const counties = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.county_name ?? ""))].filter(Boolean).sort(),
    [rows],
  );
  const passing = useMemo(() => applyFilters(rows ?? [], filters), [rows, filters]);
  // Each passing parcel's place by hydro (the stored rank is the hydro order across every county).
  const position = useMemo(
    () => new Map([...passing].sort((a, b) => a.rank - b.rank).map((r, i) => [keyOf(r), i + 1])),
    [passing],
  );
  const shown = useMemo(() => sortRows(passing, sort), [passing, sort]);
  const kwRows = useMemo(() => passing.filter((r) => r.power_group === "kw").sort((a, b) => a.rank - b.rank), [passing]);

  // Keep the selected row in view, also once the detail card has taken its share of the height.
  useEffect(() => {
    const el = body.current;
    const i = shown.findIndex((r) => keyOf(r) === selected);
    if (!el || i < 0) return;
    const top = (i + 1) * ROW_HEIGHT; // under the sticky header
    if (top < el.scrollTop + ROW_HEIGHT || top + ROW_HEIGHT > el.scrollTop + el.clientHeight)
      el.scrollTop = Math.max(0, top - 2 * ROW_HEIGHT);
  }, [selected, height]);

  async function select(r: ParcelRow) {
    const key = keyOf(r);
    if (key === selected) return;
    setSelected(key);
    setDetail(null);
    props.onSelect(null, r);
    try {
      const d = await fetchDetail(r);
      setDetail(d);
      props.onSelect(d, r);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  }

  function updated(r: ParcelRow, status: Status, note: string | null) {
    setRows((all) => (all ?? []).map((x) => (keyOf(x) === keyOf(r) ? { ...x, status, note } : x)));
    setDetail((d) => (d && keyOf(d) === keyOf(r) ? { ...d, status, note } : d));
  }

  function sortBy(c: Column) {
    if (c.key === ("badges" as SortKey)) return;
    setSort(sort.key === c.key ? { key: c.key, desc: !sort.desc } : { key: c.key, desc: c.desc ?? false });
  }

  if (error && !rows)
    return (
      <div className="shortlist-message">
        <strong>The parcel list didn't load.</strong> {error}. Start the API with <code>make up</code>; if the list is
        empty, run <code>uv run hhsf db refresh-map</code>.
      </div>
    );
  if (!rows) return <div className="shortlist-message">Loading the parcel list…</div>;
  if (rows.length === 0)
    return (
      <div className="shortlist-message">
        No parcels yet. Run <code>make score</code> for a county, or <code>uv run hhsf db refresh-map</code> after
        upgrading.
      </div>
    );

  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 10);
  const last = Math.min(shown.length, Math.ceil((scrollTop + height) / ROW_HEIGHT) + 10);
  const selectedRow = rows.find((r) => keyOf(r) === selected) ?? null;
  const active = activeFilters(filters);

  return (
    <div className="shortlist">
      <div className="shortlist-head">
        <div className="title-row">
          <h2>Parcels to look into</h2>
          <button className="link" onClick={() => setHelp(true)}>
            How is it ranked?
          </button>
        </div>
        <FilterBar filters={filters} counties={counties} onChange={setFilters} />
        <div className="title-row count-row">
          <span>
            <strong>
              {passing.length.toLocaleString()} of {rows.length.toLocaleString()} parcels
            </strong>
            {active > 0 && (
              <>
                {" · "}
                <button className="link" onClick={() => setFilters(DEFAULT_FILTERS)}>
                  clear {active} filter{active > 1 ? "s" : ""}
                </button>
              </>
            )}
            <span className="muted">
              {" "}
              · ranked by hydro score
              {kwRows.length > 0 && (
                <>
                  ; 1 kW+ at{" "}
                  {kwRows.map((r, i) => (
                    <span key={keyOf(r)}>
                      {i > 0 && ", "}
                      <button className="link" onClick={() => select(r)}>
                        #{position.get(keyOf(r))}
                      </button>
                    </span>
                  ))}
                </>
              )}
            </span>
          </span>
          <button
            className="small"
            onClick={() => downloadCsv(shown, position)}
            title="The parcels shown, as CSV. No owner names."
          >
            Export this list
          </button>
        </div>
      </div>
      <div className="table-wrap" ref={body} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}>
        {shown.length === 0 && <p className="shortlist-message">No parcel passes every filter. Untick one or loosen it.</p>}
        <table className="table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  title={c.title}
                  className={`${c.className ?? ""}${sort.key === c.key ? " sorted" : ""}`}
                  onClick={() => sortBy(c)}
                >
                  {c.label}
                  {sort.key === c.key && (sort.desc ? " ▾" : " ▴")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr style={{ height: first * ROW_HEIGHT }} />
            {shown.slice(first, last).map((r) => (
              <tr
                key={keyOf(r)}
                className={keyOf(r) === selected ? "selected" : r.status === "ruled_out" ? "ruled-out" : undefined}
                style={{ height: ROW_HEIGHT }}
                onClick={() => select(r)}
              >
                {COLUMNS.map((c) => (
                  <td key={c.key} className={c.className}>
                    {c.render(r, position.get(keyOf(r)))}
                  </td>
                ))}
              </tr>
            ))}
            <tr style={{ height: (shown.length - last) * ROW_HEIGHT }} />
          </tbody>
        </table>
      </div>
      {selectedRow && (
        <DetailCard
          row={selectedRow}
          position={position.get(keyOf(selectedRow))}
          detail={detail}
          onClose={() => {
            setSelected(null);
            setDetail(null);
            props.onSelect(null, null);
          }}
          onSaved={updated}
        />
      )}
      {help && <Help runs={runs} onClose={() => setHelp(false)} />}
    </div>
  );
}

function DetailCard(props: {
  row: ParcelRow;
  position: number | undefined;
  detail: ParcelDetail | null;
  onClose: () => void;
  onSaved: (r: ParcelRow, status: Status, note: string | null) => void;
}) {
  const { row, detail } = props;
  const [note, setNote] = useState(row.note ?? "");
  const [saving, setSaving] = useState<string | null>(null);
  useEffect(() => setNote(row.note ?? ""), [keyOf(row)]);

  async function save(status: Status, text: string) {
    setSaving("Saving…");
    try {
      const saved = await saveResearch(row, status, text);
      props.onSaved(row, saved.status, saved.note);
      setSaving("Saved");
    } catch (e) {
      setSaving(String((e as Error).message ?? e));
    }
  }

  const hydro = detail?.parts.parts.find((p) => p.part === "hydro");
  return (
    <div className="detail">
      <div className="title-row">
        <h3>
          {props.position ? `#${props.position} · ` : ""}
          {row.county_name} parcel {row.parcel_id}
        </h3>
        <button className="close" onClick={props.onClose} aria-label="Close">
          ×
        </button>
      </div>
      <p className="muted">
        <span className={`group ${row.power_group}`} title={GROUP_TITLE[row.power_group]}>
          {GROUP_LABEL[row.power_group]}
        </span>{" "}
        {row.stream_name ?? "Unnamed stream"}: {power(row.firm_power_w)} firm, {num(row.net_head_ft)} ft net head
        {row.stretches > 1 && ` (best of ${row.stretches} stretches on the parcel; up to ${power(row.max_power_w)})`}.{" "}
        {num(row.acres)} acres{detail?.deed_acres ? ` (deed ${num(detail.deed_acres)})` : ""},{" "}
        <span className="tip" title={BUILDABLE_TEXT}>
          {num(row.buildable_acres)} buildable
        </span>
        , {num(row.open_acres)} open; {num(row.drive_minutes)} min from downtown Chattanooga. #{row.rank} of every
        parcel by hydro.
      </p>

      <div className="research">
        <div className="status-buttons" role="group" aria-label="Research status">
          {STATUSES.map((s) => (
            <button key={s} className={`status ${s}${row.status === s ? " on" : ""}`} onClick={() => save(s, note)}>
              {STATUS_LABEL[s]}
            </button>
          ))}
          {saving && <span className="muted">{saving}</span>}
        </div>
        <textarea
          placeholder="Note (saved for this parcel, kept across re-runs)"
          value={note}
          rows={2}
          onChange={(e) => {
            setNote(e.target.value);
            setSaving(null);
          }}
          onBlur={() => note !== (row.note ?? "") && save(row.status, note)}
        />
      </div>

      <h4>
        Hydro score {num(row.hydro_subscore, 1)} <span className="muted">of 100</span>
      </h4>
      <div className="breakdown">
        <div className="bar">
          <div className="fill hydro" style={{ width: `${Math.max(0, Math.min(100, row.hydro_subscore ?? 0))}%` }} />
        </div>
        {hydro && (
          <ul>
            {hydro.factors.map((f) => (
              <li key={f.factor}>
                {f.reason} <span className="muted">(counts {num(f.weight * 100)}%)</span>
              </li>
            ))}
          </ul>
        )}
        {row.power_group === "low_flow" && (
          <p className="muted">Under 1 kW: scored on the power curve extended below the target (0 W → 0, 1 kW → 50).</p>
        )}
      </div>

      {!detail ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <h4>Check on the visit</h4>
          <ul className="checks">
            {detail.checks.map((c) => (
              <li key={c.topic} className={c.warn ? "warn" : undefined}>
                <strong>{c.topic}.</strong> {c.text}
              </li>
            ))}
          </ul>
          {row.flags.length > 0 && (
            <p className="flags muted">Flags: {row.flags.map((f) => FLAG_TEXT[f] ?? f).join(" · ")}</p>
          )}
          <details className="overall">
            <summary>
              <h4>
                Overall score {num(row.overall_score, 1)} <span className="muted">of 100 (not the ranking)</span>
              </h4>
            </summary>
            <Breakdown detail={detail} />
          </details>
        </>
      )}
    </div>
  );
}

function Breakdown({ detail }: { detail: ParcelDetail }) {
  const parts = detail.parts.parts;
  return (
    <div className="breakdown">
      {PARTS.map((p) => {
        const part = parts.find((x) => x.part === p.key);
        if (!part)
          return (
            <div key={p.key} className="part-row left-out">
              <div className="part-label">
                <strong>{p.label}</strong> <span className="muted">left out: no data. The other parts count more.</span>
              </div>
            </div>
          );
        return (
          <details key={p.key} className="part-row">
            <summary>
              <div className="part-label">
                <strong>{p.label}</strong>{" "}
                <span>
                  {num(part.subscore)} of 100, counts {num(part.weight * 100)}% → adds{" "}
                  <strong>{num(part.contribution, 1)}</strong> points
                </span>
              </div>
              <div className="bar" title={`${num(part.subscore)} of 100`}>
                <div className={`fill ${p.key}`} style={{ width: `${Math.max(0, Math.min(100, part.subscore))}%` }} />
              </div>
            </summary>
            <ul>
              {part.factors.map((f) => (
                <li key={f.factor}>
                  {f.reason} <span className="muted">(weight {num(f.weight * 100)}% of {p.label.toLowerCase()})</span>
                </li>
              ))}
              {part.missing.length > 0 && <li className="muted">No data for: {part.missing.join(", ")}.</li>}
            </ul>
          </details>
        );
      })}
      <p className="muted sum">
        The FR-7 score: {parts.map((x) => num(x.contribution, 1)).join(" + ")} = {num(detail.overall_score, 1)}. Click a
        part for its reasons.
      </p>
    </div>
  );
}

function Help({ runs, onClose }: { runs: ScoreRun[]; onClose: () => void }) {
  const weights = commonWeights(runs);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="How is it ranked?">
        <div className="title-row">
          <h3>How the list works</h3>
          <button className="close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <p>
          One row per turbine parcel (the parcel under the bottom of a stream's steepest stretch), across every analyzed
          county. Tick filters to whittle it down; what's left is ranked by <strong>hydro score</strong>.
        </p>
        <p>
          <strong>Hydro score.</strong> {HYDRO_TEXT} A parcel with several stretches is listed with its best one, taking
          1 kW+ stretches first.
        </p>
        <table className="explain">
          <tbody>
            {FILTERS.map((f) => (
              <tr key={f.id}>
                <th>{f.label}</th>
                <td>{f.hint}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">
          A parcel with no number for a ticked filter (not computed, no route) is left out. Filters are remembered in
          this browser. Click a column header to sort on it instead; click Hydro to go back.
        </p>
        <p className="muted">
          The overall score (FR-7) is still shown, but doesn't rank the list: it weighs hydro
          {weights
            ? ` ${num(weights.hydro * 100)}%, buildability ${num(weights.buildability * 100)}%, agriculture ${num(weights.agriculture * 100)}%, privacy ${num(weights.privacy * 100)}% and logistics ${num(weights.logistics * 100)}%`
            : ", buildability, agriculture, privacy and logistics"}
          . Its breakdown is at the bottom of each parcel's card (docs/SCORING.md, "The overall score").
        </p>
      </div>
    </div>
  );
}
