// The "layers" button under the zoom buttons. It opens a menu to its left with a thumbnail of each
// basemap; picking one calls onChange. Plain DOM, like MapLibre's own controls, so it stacks with them.
// The thumbnails (public/basemaps/) are 128 px screenshots of each map around Dunlap, data layers hidden.
import type { IControl } from "maplibre-gl";
import { BASEMAPS } from "./basemaps";

// Two stacked sheets: the usual "map layers" icon.
const ICON = `
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#333" stroke-width="2"
       stroke-linejoin="round" aria-hidden="true">
    <path d="M12 3 2 8.5l10 5.5 10-5.5z" />
    <path d="m2 13.5 10 5.5 10-5.5" />
  </svg>`;

export class BasemapControl implements IControl {
  private container = document.createElement("div");
  private button = document.createElement("button");
  private menu = document.createElement("div");
  private choices = new Map<string, HTMLButtonElement>();

  constructor(
    public value: string,
    private onChange: (id: string) => void,
  ) {}

  onAdd(): HTMLElement {
    this.container.className = "maplibregl-ctrl basemap-ctrl";
    const group = document.createElement("div");
    group.className = "maplibregl-ctrl-group";
    this.button.type = "button";
    this.button.innerHTML = ICON;
    this.button.setAttribute("aria-haspopup", "true");
    this.button.setAttribute("aria-expanded", "false");
    this.button.addEventListener("click", () => this.setOpen(this.menu.hidden));
    group.append(this.button);

    this.menu.className = "basemap-menu";
    this.menu.hidden = true;
    for (const b of BASEMAPS) {
      const choice = document.createElement("button");
      choice.type = "button";
      choice.innerHTML = `<img src="/basemaps/${b.id}.jpg" alt="" width="64" height="64" /><span>${b.label}</span>`;
      choice.addEventListener("click", () => {
        this.select(b.id);
        this.setOpen(false);
        this.button.focus();
      });
      this.choices.set(b.id, choice);
      this.menu.append(choice);
    }
    this.container.append(group, this.menu);
    this.container.addEventListener("keydown", this.onKeyDown);
    document.addEventListener("pointerdown", this.onPointerDown);
    this.select(this.value, false);
    return this.container;
  }

  onRemove() {
    document.removeEventListener("pointerdown", this.onPointerDown);
    this.container.remove();
  }

  private select(id: string, notify = true) {
    this.value = id;
    const label = BASEMAPS.find((b) => b.id === id)?.label ?? id;
    this.button.title = `Map: ${label}`;
    this.button.setAttribute("aria-label", `Change map (now ${label})`);
    for (const [choiceId, choice] of this.choices) choice.setAttribute("aria-pressed", String(choiceId === id));
    if (notify) this.onChange(id);
  }

  private setOpen(open: boolean) {
    this.menu.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    if (open) this.choices.get(this.value)?.focus();
  }

  // Close on Escape or on a click anywhere else.
  private onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape" && !this.menu.hidden) {
      this.setOpen(false);
      this.button.focus();
    }
  };

  private onPointerDown = (e: PointerEvent) => {
    if (!this.menu.hidden && !this.container.contains(e.target as Node)) this.setOpen(false);
  };
}
