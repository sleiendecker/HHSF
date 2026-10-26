import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  FLAG_TEXT,
  GROUP_LABEL,
  GROUP_TITLE,
  NO_FILTERS,
  PARTS,
  STATUSES,
  STATUS_LABEL,
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
  saveResearch,
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

interface Column {
  key: SortKey;
  label: string;
  title: string;
  render: (r: ParcelRow) => ReactNode;
  className?: string;
  desc?: boolean; // first click sorts high to low
}

const COLUMNS: Column[] = [
  { key: "rank", label: "#", title: "Rank by overall score, every county", render: (r) => r.rank, className: "num" },
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
  { key: "overall_score", label: "Overall", title: "Overall score, 0–100", render: (r) => num(r.overall_score), className: "num strong", desc: true },
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
  ...PARTS.map(
    (p): Column => ({
      key: p.column,
      label: p.label.slice(0, 1),
      title: `${p.label} part of the overall score, 0–100`,
      render: (r) => num(r[p.column] as number | null),
      className: "num part",
      desc: true,
    }),
  ),
  { key: "stretches", label: "Str.", title: "Stretches on the parcel", render: (r) => r.stretches, className: "num", desc: true },
  { key: "acres", label: "Acres", title: "Parcel acres", render: (r) => num(r.acres), className: "num", desc: true },
  { key: "buildable_acres", label: "Build ac", title: "Buildable acres", render: (r) => num(r.buildable_acres), className: "num", desc: true },
  { key: "drive_minutes", label: "Min", title: "Drive from downtown Chattanooga, minutes", render: (r) => num(r.drive_minutes), className: "num" },
  { key: "open_acres", label: "Open ac", title: "Open acres (field, pasture, crops)", render: (r) => num(r.open_acres), className: "num", desc: true },
  { key: "subdivision_score", label: "Split", title: "Subdivision feasibility, 0–100 (not in the overall score)", render: (r) => num(r.subdivision_score), className: "num", desc: true },
];

function Badges({ row }: { row: ParcelRow }) {
  return (
    <>
      {badges(row).map((b) => (
        <span key={b.text} className={`why-badge ${b.tone}`}>
          {b.text}
        </span>
      ))}
    </>
  );
}

function NumberFilter(props: { label: string; value: number | null; onChange: (v: number | null) => void; step?: number }) {
  return (
    <label className="filter">
      {props.label}
      <input
        type="number"
        min={0}
        step={props.step ?? 1}
        value={props.value ?? ""}
        onChange={(e) => props.onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
    </label>
  );
}

export function Shortlist(props: { onSelect: (detail: ParcelDetail | null, row: ParcelRow | null) => void }) {
  const [rows, setRows] = useState<ParcelRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [sort, setSort] = useState<Sort>({ key: "rank", desc: false });
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ParcelDetail | null>(null);
  const [explain, setExplain] = useState(false);
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
  const shown = useMemo(() => sortRows(applyFilters(rows ?? [], filters), sort), [rows, filters, sort]);
  const kwRows = useMemo(() => (rows ?? []).filter((r) => r.power_group === "kw"), [rows]);
  const set = (patch: Partial<Filters>) => setFilters({ ...filters, ...patch });

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
        <strong>The shortlist didn't load.</strong> {error}. Start the API with <code>make up</code>; if the list is
        empty, run <code>uv run hhsf db refresh-map</code>.
      </div>
    );
  if (!rows) return <div className="shortlist-message">Loading the shortlist…</div>;
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

  return (
    <div className="shortlist">
      <div className="shortlist-head">
        <div className="title-row">
          <h2>Parcels to look into</h2>
          <button className="link" onClick={() => setExplain(true)}>
            What's in the overall score?
          </button>
        </div>
        <p className="muted">
          {rows.length.toLocaleString()} turbine parcels in {counties.length} counties, ranked by overall score. 1 kW+
          parcels rank{" "}
          {kwRows.map((r, i) => (
            <span key={keyOf(r)}>
              {i > 0 && ", "}
              <button
                className="link"
                onClick={() => select(r)}
              >
                #{r.rank}
              </button>
            </span>
          ))}
          : land and access can outweigh power.
        </p>
        <div className="filters">
          <label className="filter">
            Power
            <select value={filters.group} onChange={(e) => set({ group: e.target.value as Filters["group"] })}>
              <option value="">Any</option>
              <option value="kw">1 kW+ only</option>
              <option value="head_only">Head only</option>
              <option value="low_flow">Low flow</option>
            </select>
          </label>
          <NumberFilter label="Min W" value={filters.minPowerW} step={50} onChange={(v) => set({ minPowerW: v })} />
          <NumberFilter label="Max min" value={filters.maxDriveMin} step={5} onChange={(v) => set({ maxDriveMin: v })} />
          <NumberFilter label="Min build ac" value={filters.minBuildableAc} onChange={(v) => set({ minBuildableAc: v })} />
          <NumberFilter label="Max acres" value={filters.maxParcelAc} step={50} onChange={(v) => set({ maxParcelAc: v })} />
          <label className="filter">
            County
            <select value={filters.county} onChange={(e) => set({ county: e.target.value })}>
              <option value="">All</option>
              {counties.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className="filter">
            Status
            <select value={filters.status} onChange={(e) => set({ status: e.target.value as Filters["status"] })}>
              <option value="">Any</option>
              <option value="open">Not ruled out</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="title-row">
          <span className="muted">
            Showing {shown.length.toLocaleString()} of {rows.length.toLocaleString()}
            {JSON.stringify(filters) !== JSON.stringify(NO_FILTERS) && (
              <>
                {" · "}
                <button className="link" onClick={() => setFilters(NO_FILTERS)}>
                  clear filters
                </button>
              </>
            )}
          </span>
          <button className="small" onClick={() => downloadCsv(shown)} title="The rows shown, as CSV. No owner names.">
            Export this list
          </button>
        </div>
      </div>
      <div className="table-wrap" ref={body} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}>
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
                    {c.render(r)}
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
          detail={detail}
          onClose={() => {
            setSelected(null);
            setDetail(null);
            props.onSelect(null, null);
          }}
          onSaved={updated}
        />
      )}
      {explain && <Explainer runs={runs} onClose={() => setExplain(false)} />}
    </div>
  );
}

function DetailCard(props: {
  row: ParcelRow;
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

  return (
    <div className="detail">
      <div className="title-row">
        <h3>
          #{row.rank} · {row.county_name} parcel {row.parcel_id}
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
        {num(row.acres)} acres{detail?.deed_acres ? ` (deed ${num(detail.deed_acres)})` : ""}, {num(row.buildable_acres)}{" "}
        buildable, {num(row.open_acres)} open; {num(row.drive_minutes)} min from downtown Chattanooga.
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
        Overall score {num(row.overall_score, 1)} <span className="muted">of 100</span>
      </h4>
      {!detail ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <Breakdown detail={detail} />
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
        The points add up to the overall score: {parts.map((x) => num(x.contribution, 1)).join(" + ")} ={" "}
        {num(detail.overall_score, 1)}. Click a part for its reasons.
      </p>
    </div>
  );
}

function Explainer({ runs, onClose }: { runs: ScoreRun[]; onClose: () => void }) {
  const weights = commonWeights(runs);
  const weight = (key: string, fallback: number) => (weights ? (weights[key] ?? 0) : fallback);
  const profiles = [...new Set(runs.map((r) => r.profile))].join(", ");
  const scored = runs
    .map((r) => r.finished_at)
    .filter((t): t is string => !!t)
    .sort()
    .pop();
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="What's in the overall score?">
        <div className="title-row">
          <h3>What's in the overall score?</h3>
          <button className="close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <p>
          Each parcel's best stretch of stream gets a score from 0 to 100 for five things, and the overall score is
          their weighted sum (FR-7). Every score is on a fixed scale, not relative to the other parcels.
        </p>
        <table className="explain">
          <tbody>
            {PARTS.map((p) => (
              <tr key={p.key}>
                <th>
                  {p.label} <span className="muted">{num(weight(p.key, p.weight) * 100)}%</span>
                </th>
                <td>{p.what}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          <strong>Why the 1 kW+ parcels aren't at the top.</strong> Hydro is {num(weight("hydro", 0.4) * 100)}% of the
          score; the rest is land and access. A parcel whose stream makes only 50 W can have great buildable ground, farmland, privacy and a short
          drive, and outscore a 1 kW site on a steep, wooded tract. The power group column and the "1 kW+ only" filter
          keep that visible. Low-flow power is a regional estimate for a typical basin and can't see springs.
        </p>
        <p>
          <strong>Missing data is left out, not counted as zero.</strong> If a part has no data, the other parts' weights
          are scaled up to fill in. A measured nothing (no buildable ground) is a zero.
        </p>
        <p>
          <strong>One row per parcel.</strong> A parcel is represented by its best stretch: the highest overall score
          among its stretches in its best power group, so a 1 kW stretch is never hidden behind a slightly higher low-flow
          one. Subdivision feasibility is shown but isn't in the score.
        </p>
        {runs.length > 0 && (
          <p className="muted">
            {weights
              ? `These weights are the ones the latest score runs used`
              : `The counties were scored with different weights; each parcel's card shows its own`}{" "}
            (profile {profiles}
            {scored && `, scored ${new Date(scored).toLocaleDateString()}`}).
          </p>
        )}
        <p className="muted">The full curves and weights: docs/SCORING.md, "The overall score".</p>
      </div>
    </div>
  );
}
