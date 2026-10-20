import { BASEMAPS } from "./basemaps";

// Picks the map under the data: light for reading the scores, satellite for roads, clearings and
// houses, topo for contours.
export function BasemapSwitcher({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  return (
    <fieldset className="basemaps">
      <legend>Map</legend>
      {BASEMAPS.map((b) => (
        <label key={b.id}>
          <input type="radio" name="basemap" value={b.id} checked={value === b.id} onChange={() => onChange(b.id)} />
          {b.label}
        </label>
      ))}
    </fieldset>
  );
}
