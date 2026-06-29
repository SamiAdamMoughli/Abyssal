import 'leaflet/dist/leaflet.css';
import './style.css';
import { state } from "./state.js";
import { fmtDate } from "./utils.js";
import { loadData, currentDates } from "./api.js";
import { renderCards } from "./ui.js";
import { initGlobalPhase, currentPhase } from "./phase.js";
import { getVisibleVessels } from "./vessels.js";
import { initAlerts } from "./alerts.js";
import { toggleHeatmap, toggleWeather, toggleCorridors, toggleDarkGaps, refreshStatsBar } from "./overlays.js";
import { initAuth } from "./auth.js";
import { initNav } from "./nav.js";
import { initFleet } from "./views/fleet.js";
import { initAnalytics } from "./views/analytics.js";
import { initGovernance } from "./views/governance.js";

// ==================================================================
// NAV RAIL — init first so view state is set before anything renders
// ==================================================================
state.activeView = 'ops';
initNav();
initFleet();
initAnalytics();
initGovernance();

// ==================================================================
// MAP INIT
// ==================================================================
state.map = L.map("map", { zoomControl: true }).setView([20, 0], 2);
state.markerLayer = L.layerGroup().addTo(state.map);
state.ringLayer = L.layerGroup().addTo(state.map);
state.gapLayer = L.layerGroup().addTo(state.map);
state.mpaLayer = L.layerGroup().addTo(state.map);
state.detailLayer = L.layerGroup().addTo(state.map);

// ==================================================================
// BASE LAYERS
// ==================================================================
const BASE_LAYERS = {
  dark: L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: "&copy; OpenStreetMap &copy; CARTO",
    maxZoom: 19,
  }),
  satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    attribution: "&copy; Esri, Maxar, Earthstar Geographics",
    maxZoom: 19,
  }),
  bathymetric: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}", {
    attribution: "&copy; Esri, GEBCO, NOAA",
    maxZoom: 13,
  }),
  terrain: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}", {
    attribution: "&copy; Esri, USGS, NOAA",
    maxZoom: 13,
  }),
  osm: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }),
  noaa: L.tileLayer("https://tileservice.charts.noaa.gov/tiles/50000_1/{z}/{x}/{y}.png", {
    attribution: "&copy; NOAA",
    maxZoom: 16,
    opacity: 0.9,
  }),
};

let _activeBase = "dark";
BASE_LAYERS.dark.addTo(state.map);

function setBaseLayer(name) {
  if (name === _activeBase || !BASE_LAYERS[name]) return;
  BASE_LAYERS[_activeBase].remove();
  BASE_LAYERS[name].addTo(state.map);
  [state.mpaLayer, state.gapLayer, state.ringLayer, state.markerLayer]
    .forEach(l => l.bringToFront?.());
  _activeBase = name;
}

// Loading overlay
const ov = document.createElement("div");
ov.id = "map-overlay";
ov.className = "map-overlay";
ov.innerHTML = '<div class="spinner"></div><div>CONNECTING TO DATA SOURCE…</div>';
document.getElementById("map").appendChild(ov);

// ==================================================================
// DATE PICKER — default: last 7 days
// ==================================================================
(function initDates() {
  const to = new Date();
  const from = new Date(Date.now() - 7 * 864e5);
  document.getElementById("date-to").value = fmtDate(to);
  document.getElementById("date-from").value = fmtDate(from);
})();

// ==================================================================
// LAYER TOGGLE
// ==================================================================
function applyMode(mode) {
  const showV = mode === "vessels" || mode === "both";
  const showM = mode === "mpas" || mode === "both";
  [state.markerLayer, state.ringLayer].forEach((l) =>
    showV ? state.map.addLayer(l) : state.map.removeLayer(l),
  );
  showM
    ? state.map.addLayer(state.mpaLayer)
    : state.map.removeLayer(state.mpaLayer);
  document
    .querySelectorAll(".toggle-btn")
    .forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
}
document
  .querySelectorAll(".toggle-btn")
  .forEach((btn) =>
    btn.addEventListener("click", () => applyMode(btn.dataset.mode)),
  );

document
  .getElementById("base-layer-select")
  .addEventListener("change", (e) => setBaseLayer(e.target.value));

// ==================================================================
// SEARCH THIS AREA
// ==================================================================
function searchThisArea() {
  const b = state.map.getBounds();
  const d = currentDates();
  loadData({
    min_lat: b.getSouth(),
    max_lat: b.getNorth(),
    min_lon: b.getWest(),
    max_lon: b.getEast(),
    start: d.start,
    end: d.end,
  });
}
document.getElementById("search-btn").addEventListener("click", searchThisArea);

// ==================================================================
// SIDEBAR CONTROLS
// ==================================================================
document.getElementById("search-input").addEventListener("input", (e) => {
  state.currentFilter = e.target.value.trim();
  renderCards();
  document.getElementById("sidebar-sub").textContent =
    `Showing ${getVisibleVessels().length} of ${state.vesselsCache.length} vessels`;
});

document.getElementById("sort-select").addEventListener("change", (e) => {
  state.currentSort = e.target.value;
  renderCards();
  document.getElementById("sidebar-sub").textContent =
    `Showing ${getVisibleVessels().length} of ${state.vesselsCache.length} vessels`;
});

// ==================================================================
// ALERT PANEL TOGGLES
// ==================================================================
const alertToggleBtn = document.querySelector(".alert-toggle-btn");
const alertPanel =
  document.getElementById("alert-panel") ||
  document.querySelector(".alert-panel");
const alertCloseBtn = document.querySelector(".alert-panel-close");

if (alertToggleBtn && alertPanel) {
  alertToggleBtn.addEventListener("click", () => {
    const isHidden = alertPanel.hasAttribute("hidden");
    if (isHidden) {
      alertPanel.removeAttribute("hidden");
      alertToggleBtn.classList.add("active");
    } else {
      alertPanel.setAttribute("hidden", "");
      alertToggleBtn.classList.remove("active");
    }
  });
}

if (alertCloseBtn && alertPanel && alertToggleBtn) {
  alertCloseBtn.addEventListener("click", () => {
    alertPanel.setAttribute("hidden", "");
    alertToggleBtn.classList.remove("active");
  });
}

document.getElementById("cat-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".cat-btn");
  if (!btn) return;
  const cat = btn.dataset.cat;
  state.currentCatFilter =
    cat === state.currentCatFilter && cat !== "all" ? "all" : cat;
  document
    .querySelectorAll(".cat-btn")
    .forEach((b) =>
      b.classList.toggle(
        "active",
        b.dataset.cat === state.currentCatFilter ||
          (state.currentCatFilter === "all" && b.dataset.cat === "all"),
      ),
    );
  renderCards();
  document.getElementById("sidebar-sub").textContent =
    `Showing ${getVisibleVessels().length} of ${state.vesselsCache.length} vessels`;
});

// ==================================================================
// MAP MOVE — show "Search this area" button
// ==================================================================
function maybeShowButton() {
  if (state.ready && currentPhase() === 'REGION')
    document.getElementById("search-btn").classList.add("show");
}
state.map.on("moveend", maybeShowButton);

// ==================================================================
// STARTUP — enter global level-select phase
// ==================================================================
(async () => {
  initGlobalPhase();
  state.ready = true;

  await initAlerts();
  initAuth();
  await refreshStatsBar();

  _updateNavDot();
  setInterval(_updateNavDot, 5000);

  document.getElementById("qf-strip")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".qf-btn");
    if (!btn) return;
    btn.classList.toggle("active");
    _applyQuickFilters();
  });

  document.getElementById("overlay-heatmap")?.addEventListener("change", (e) => {
    toggleHeatmap(e.target.checked);
  });
  document.getElementById("overlay-weather")?.addEventListener("change", (e) => {
    toggleWeather(e.target.checked);
  });
  document.getElementById("overlay-corridors")?.addEventListener("change", (e) => {
    toggleCorridors(e.target.checked);
  });
  document.getElementById("overlay-dark-gaps")?.addEventListener("change", (e) => {
    toggleDarkGaps(e.target.checked);
  });
})();

// ==================================================================
// QUICK FILTER HELPERS
// ==================================================================
function _applyQuickFilters() {
  const active = [...document.querySelectorAll(".qf-btn.active")].map(b => b.dataset.qf);
  state._quickFilters = active;
  renderCards();
}

// ==================================================================
// NAV RAIL STATUS DOT
// ==================================================================
function _updateNavDot() {
  const dot = document.getElementById("nav-status-dot");
  if (!dot) return;
  const wsOk = state.alertsSocket?.readyState === WebSocket.OPEN;
  dot.className = "nav-status-dot " + (wsOk ? "online" : "offline");
  dot.title = wsOk ? "All systems connected" : "WebSocket disconnected";
}
