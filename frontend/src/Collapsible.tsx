import { useState, type ReactNode } from "react";

// Remembered per browser, like the basemap. Storage can be blocked (private windows): then it just isn't remembered.
function saved(key: string): boolean | undefined {
  try {
    const value = localStorage.getItem(key);
    return value === null ? undefined : value === "closed";
  } catch {
    return undefined;
  }
}

function save(key: string, collapsed: boolean) {
  try {
    localStorage.setItem(key, collapsed ? "closed" : "open");
  } catch {
    // Not remembered; nothing else to do.
  }
}

// A box with a title row that folds the rest away. `startCollapsed` applies until the viewer picks.
export function Collapsible(props: {
  id: string;
  className: string;
  title: ReactNode;
  startCollapsed?: boolean;
  children: ReactNode;
}) {
  const key = `hhsf.collapsed.${props.id}`;
  const [collapsed, setCollapsed] = useState(() => saved(key) ?? props.startCollapsed ?? false);
  const toggle = () => {
    setCollapsed(!collapsed);
    save(key, !collapsed);
  };
  return (
    <div className={`${props.className}${collapsed ? " collapsed" : ""}`}>
      <button className="collapse-header" onClick={toggle} aria-expanded={!collapsed}>
        {props.title}
        <span className="chevron" aria-hidden>
          {collapsed ? "▸" : "▾"}
        </span>
      </button>
      {!collapsed && <div className="collapse-body">{props.children}</div>}
    </div>
  );
}
