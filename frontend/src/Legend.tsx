import { FILTERED_COLOR, HEAD_ONLY_COLOR, LOW_FLOW_COLOR, SCORE_STOPS } from "./scores";

const ramp = `linear-gradient(to right, ${SCORE_STOPS.map(([, c]) => c).join(", ")})`;
const mid = SCORE_STOPS[2][1];

// A short line drawn the way the map draws that group.
function Swatch({ color, dash, width, casing, opacity }: Swatch) {
  return (
    <svg width="36" height="12" aria-hidden>
      {casing && <line x1="3" y1="6" x2="33" y2="6" stroke="#222" strokeWidth={width + 2} strokeLinecap="round" />}
      <line
        x1="3"
        y1="6"
        x2="33"
        y2="6"
        stroke={color}
        strokeWidth={width}
        strokeDasharray={dash}
        strokeLinecap={dash === "0 6" ? "round" : "butt"}
        opacity={opacity}
      />
    </svg>
  );
}
interface Swatch {
  color: string;
  width: number;
  dash?: string;
  casing?: boolean;
  opacity?: number;
}

export function Legend() {
  return (
    <div className="legend">
      <h2>Hydro score</h2>
      <div className="ramp" style={{ background: ramp }} />
      <div className="ramp-labels">
        <span>{SCORE_STOPS[0][0]}: just makes 1 kW</span>
        <span>100</span>
      </div>
      <p className="hint">Darker = more power and drop. 70% power, 30% head.</p>

      <h2>How sure we are</h2>
      <ul>
        <li>
          <Swatch color={mid} width={5} casing />
          <span>
            <b>Medium confidence</b>: flow from USGS StreamStats
          </span>
        </li>
        <li>
          <Swatch color={mid} width={3.5} dash="6 4" />
          <span>
            <b>Low confidence</b>: rough flow estimate
          </span>
        </li>
        <li>
          <Swatch color={HEAD_ONLY_COLOR} width={3} dash="0 6" />
          <span>
            <b>Head only</b>: too small to estimate flow; check on a visit
          </span>
        </li>
        <li>
          <Swatch color={LOW_FLOW_COLOR} width={2} opacity={0.7} />
          <span>
            <b>Low flow</b>: under 1 kW on a rough flow estimate; listed last
          </span>
        </li>
        <li>
          <Swatch color={FILTERED_COLOR} width={2} opacity={0.5} />
          <span>Filtered out: under 1 kW on a StreamStats flow, or too little drop</span>
        </li>
      </ul>
      <p className="hint">Every medium stream ranks above every low one. Zoom in for rank numbers.</p>
    </div>
  );
}
