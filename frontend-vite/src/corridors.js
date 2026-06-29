/**
 * corridors.js — Structural risk corridor overlays.
 *
 * Two layers sourced from the analytics engine:
 *
 *   1. H3 Corridor Heatmap  (/corridors/h3)
 *      H3 res-5 cells coloured by corridor_score (density × persistence).
 *      Uses a fixed colour ramp so opacity doesn't fight the base map.
 *
 *   2. Dark Transit Vectors  (/corridors/dark-gaps)
 *      Polylines connecting departure → arrival H3 centroids for vessels
 *      that went dark.  Implausible gaps (implied speed > 30 kn) are drawn
 *      in red; plausible gaps in amber.
 *
 * Both layers are viewport-independent — they always show the global picture
 * for the requested time window, not just the current bbox.  They are
 * refreshed once on enable and not re-fetched on pan/zoom.
 */

import { state } from "./state.js";

const ANALYTICS_URL = window.__ENV?.ANALYTICS_URL ?? "http://localhost:8001";

// ── Corridor score colour ramp ─────────────────────────────────────────────
// Four-stop ramp: low (teal) → mid (amber) → high (orange) → critical (red).
// Scores come back in [0, ~50]; we normalise against the max in the payload.
const RAMP = [
  { t: 0.0,  hex: "#1abc9c" },
  { t: 0.35, hex: "#f39c12" },
  { t: 0.65, hex: "#e67e22" },
  { t: 1.0,  hex: "#e74c3c" },
];

function _scoreColor(norm) {
  // Find the two ramp stops that straddle `norm` and lerp.
  let lo = RAMP[0], hi = RAMP[RAMP.length - 1];
  for (let i = 0; i < RAMP.length - 1; i++) {
    if (norm >= RAMP[i].t && norm <= RAMP[i + 1].t) {
      lo = RAMP[i];
      hi = RAMP[i + 1];
      break;
    }
  }
  const span = hi.t - lo.t || 1;
  const f = (norm - lo.t) / span;

  const r = (c) => parseInt(c.slice(1, 3), 16);
  const g = (c) => parseInt(c.slice(3, 5), 16);
  const b = (c) => parseInt(c.slice(5, 7), 16);
  const lerp = (a, b) => Math.round(a + (b - a) * f);

  const rv = lerp(r(lo.hex), r(hi.hex));
  const gv = lerp(g(lo.hex), g(hi.hex));
  const bv = lerp(b(lo.hex), b(hi.hex));
  return `rgb(${rv},${gv},${bv})`;
}

// ── H3 corridor choropleth ─────────────────────────────────────────────────

let _corridorLayer = null;
let _corridorActive = false;

export async function toggleCorridors(enable) {
  if (enable === _corridorActive) return;
  _corridorActive = enable;

  if (!enable) {
    _corridorLayer?.remove();
    _corridorLayer = null;
    return;
  }

  await _loadCorridors();
}

async function _loadCorridors() {
  let data;
  try {
    const res = await fetch(`${ANALYTICS_URL}/corridors/h3?weeks=8&limit=500`);
    if (!res.ok) return;
    data = await res.json();
  } catch {
    return;
  }

  const cells = data.cells ?? [];
  if (!cells.length) return;

  const maxScore = Math.max(...cells.map((c) => c.corridor_score), 1);

  if (_corridorLayer) _corridorLayer.remove();
  _corridorLayer = L.layerGroup().addTo(state.map);

  for (const cell of cells) {
    const norm = cell.corridor_score / maxScore;
    const color = _scoreColor(norm);
    const opacity = 0.15 + norm * 0.5; // 0.15 → 0.65

    if (cell.lat == null || cell.lon == null) continue;

    const circle = L.circleMarker([cell.lat, cell.lon], {
      radius: 6 + norm * 10,
      color,
      weight: 1,
      opacity: 0.8,
      fillColor: color,
      fillOpacity: opacity,
    }).addTo(_corridorLayer);

    circle.bindTooltip(_corridorTooltip(cell), { sticky: true, opacity: 0.92 });
  }
}

function _corridorTooltip(c) {
  return `
    <div class="corridor-tip">
      <b>Risk Corridor</b><br>
      Score: <b>${c.corridor_score.toFixed(2)}</b> &nbsp;·&nbsp; ${c.persistence_weeks}w persistence<br>
      ${c.vessel_count} vessels &nbsp;·&nbsp; ${c.high_risk_count} high-risk &nbsp;·&nbsp; ${c.dark_vessel_count} dark<br>
      ${c.dominant_flag ? `Flag: <b>${c.dominant_flag}</b>` : ""}
      ${c.dominant_vessel_type ? `&nbsp;·&nbsp; Type: ${c.dominant_vessel_type}` : ""}
    </div>`;
}

// ── Dark transit vectors ───────────────────────────────────────────────────

let _darkGapLayer = null;
let _darkGapActive = false;

export async function toggleDarkGaps(enable) {
  if (enable === _darkGapActive) return;
  _darkGapActive = enable;

  if (!enable) {
    _darkGapLayer?.remove();
    _darkGapLayer = null;
    return;
  }

  await _loadDarkGaps();
}

async function _loadDarkGaps() {
  let data;
  try {
    const res = await fetch(
      `${ANALYTICS_URL}/corridors/dark-gaps?limit=200`,
    );
    if (!res.ok) return;
    data = await res.json();
  } catch {
    return;
  }

  const gaps = data.gaps ?? [];
  if (!gaps.length) return;

  if (_darkGapLayer) _darkGapLayer.remove();
  _darkGapLayer = L.layerGroup().addTo(state.map);

  for (const gap of gaps) {
    const props = gap.properties ?? gap;
    const fromPt = props.from_lat != null ? [props.from_lat, props.from_lon] : null;
    const toPt   = props.to_lat   != null ? [props.to_lat,   props.to_lon]   : null;
    if (!fromPt || !toPt) continue;

    const implausible = props.implausible ?? false;
    const color = implausible ? "#e74c3c" : "#f39c12";
    const dashArray = implausible ? null : "6 5";

    const line = L.polyline([fromPt, toPt], {
      color,
      weight: implausible ? 2 : 1.5,
      opacity: 0.75,
      dashArray,
    }).addTo(_darkGapLayer);

    line.bindTooltip(_darkGapTooltip(props), { sticky: true, opacity: 0.92 });
  }
}


function _darkGapTooltip(p) {
  const speed = p.implied_speed_kn != null ? `${p.implied_speed_kn.toFixed(1)} kn` : "—";
  const flag = p.implausible
    ? `<span style="color:#e74c3c">⚠ IMPLAUSIBLE SPEED</span><br>`
    : "";
  return `
    <div class="corridor-tip">
      <b>Dark Transit</b><br>
      ${flag}
      MMSI: <b>${p.mmsi ?? "—"}</b><br>
      Gap: <b>${(p.gap_hours ?? 0).toFixed(1)}h</b> &nbsp;·&nbsp;
      ${(p.displacement_nm ?? 0).toFixed(0)} nm &nbsp;·&nbsp; ${speed}
    </div>`;
}
