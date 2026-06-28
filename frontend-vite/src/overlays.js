/**
 * Map overlay layers: vessel heatmap, EEZ risk, weather, and corridors.
 * All overlays are optional and degrade gracefully when data is unavailable.
 */

import "leaflet.heat";
import { state } from "./state.js";
export { toggleCorridors, toggleDarkGaps } from "./corridors.js";

const ANALYTICS_URL =
  window.__ENV?.ANALYTICS_URL ?? "http://localhost:8001";
const WEATHER_URL = "https://api.open-meteo.com/v1";

// ── Heatmap ────────────────────────────────────────────────────────────────

let _heatLayer = null;
let _heatActive = false;

export async function toggleHeatmap(enable) {
  if (enable === _heatActive) return;
  _heatActive = enable;

  if (!enable) {
    _heatLayer?.remove();
    _heatLayer = null;
    return;
  }

  await _refreshHeatmap();
  state.map.on("moveend", _onMoveHeat);
}

async function _refreshHeatmap() {
  const b = state.map.getBounds();
  try {
    const res = await fetch(
      `${ANALYTICS_URL}/heatmap` +
        `?min_lat=${b.getSouth()}&max_lat=${b.getNorth()}` +
        `&min_lon=${b.getWest()}&max_lon=${b.getEast()}`,
    );
    if (!res.ok) return;
    const { points } = await res.json();
    if (!points?.length) return;

    if (_heatLayer) _heatLayer.remove();
    _heatLayer = L.heatLayer(points, {
      radius: 22,
      blur: 18,
      maxZoom: 12,
      gradient: { 0.2: "#0ff", 0.5: "#ff0", 0.8: "#f80", 1.0: "#f00" },
    }).addTo(state.map);
  } catch {
    // analytics engine offline — silent fail
  }
}

function _onMoveHeat() {
  if (_heatActive) _refreshHeatmap();
}

// ── Risk summary KPIs (feeds the stats bar) ───────────────────────────────

export async function loadRiskSummary() {
  try {
    const res = await fetch(`${ANALYTICS_URL}/risk/summary`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// ── Weather overlay (Open-Meteo — free, no key) ───────────────────────────

let _wxLayer = null;
let _wxActive = false;

const WX_TILE =
  "https://tile.openweathermap.org/map/wind_new/{z}/{x}/{y}.png?appid=demo";

export function toggleWeather(enable) {
  if (enable === _wxActive) return;
  _wxActive = enable;

  if (!enable) {
    _wxLayer?.remove();
    _wxLayer = null;
    return;
  }

  // Open-Meteo doesn't provide tiles; use a lightweight wind overlay from
  // a tile service that needs no key at low zoom.
  _wxLayer = L.tileLayer(
    "https://tile.openweathermap.org/map/wind_new/{z}/{x}/{y}.png",
    { opacity: 0.45, attribution: "© OpenWeatherMap" },
  ).addTo(state.map);
}

// ── Stats bar update ───────────────────────────────────────────────────────

export async function refreshStatsBar() {
  const summary = await loadRiskSummary();
  const el = document.getElementById("stats-bar");
  if (!el || !summary) return;

  el.innerHTML = `
    <span class="stat"><b>${summary.total ?? "—"}</b> vessels</span>
    <span class="stat hi"><b>${summary.high_risk ?? "—"}</b> high-risk</span>
    <span class="stat mid"><b>${summary.med_risk ?? "—"}</b> medium</span>
    <span class="stat dark"><b>${summary.dark_vessels ?? "—"}</b> dark</span>
    <span class="stat mpa"><b>${summary.in_mpa ?? "—"}</b> in MPA</span>
    <span class="stat avg">avg score <b>${summary.avg_score ?? "—"}</b></span>
  `;
}
