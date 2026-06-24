import 'leaflet/dist/leaflet.css';
import './style.css';
import L from 'leaflet';

import { state } from './state.js';
import { fmtDate } from './utils.js';
import { setupOnboarding, hideOnboarding } from './onboarding.js';
import { loadData, currentDates } from './api.js';
import { renderCards } from './ui.js';
import { getVisibleVessels } from './vessels.js';

// ==================================================================
// MAP INIT
// ==================================================================
state.map         = L.map('map', { zoomControl: true }).setView([-0.5, -90.5], 7);
state.markerLayer = L.layerGroup().addTo(state.map);
state.ringLayer   = L.layerGroup().addTo(state.map);
state.gapLayer    = L.layerGroup().addTo(state.map);
state.mpaLayer    = L.layerGroup().addTo(state.map);

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap &copy; CARTO',
  maxZoom: 19,
}).addTo(state.map);

// Loading overlay (created dynamically so the HTML stays clean)
const ov = document.createElement('div');
ov.id = 'map-overlay';
ov.className = 'map-overlay';
ov.innerHTML = '<div class="spinner"></div><div>CONNECTING TO DATA SOURCE…</div>';
document.getElementById('map').appendChild(ov);

// ==================================================================
// DATE PICKER — default: last 7 days
// ==================================================================
(function initDates() {
  const to   = new Date();
  const from = new Date(Date.now() - 7 * 864e5);
  document.getElementById('date-to').value   = fmtDate(to);
  document.getElementById('date-from').value = fmtDate(from);
})();

// ==================================================================
// LAYER TOGGLE
// ==================================================================
function applyMode(mode) {
  const showV = mode === 'vessels' || mode === 'both';
  const showM = mode === 'mpas'    || mode === 'both';
  [state.markerLayer, state.ringLayer].forEach(l =>
    showV ? state.map.addLayer(l) : state.map.removeLayer(l));
  showM ? state.map.addLayer(state.mpaLayer) : state.map.removeLayer(state.mpaLayer);
  document.querySelectorAll('.toggle-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === mode));
}
document.querySelectorAll('.toggle-btn').forEach(btn =>
  btn.addEventListener('click', () => applyMode(btn.dataset.mode)));

// ==================================================================
// SEARCH THIS AREA
// ==================================================================
function searchThisArea() {
  const b = state.map.getBounds();
  const d = currentDates();
  loadData({
    min_lat: b.getSouth(), max_lat: b.getNorth(),
    min_lon: b.getWest(), max_lon: b.getEast(),
    start: d.start, end: d.end,
  });
}
document.getElementById('search-btn').addEventListener('click', searchThisArea);

// ==================================================================
// SIDEBAR CONTROLS
// ==================================================================
document.getElementById('search-input').addEventListener('input', e => {
  state.currentFilter = e.target.value.trim();
  renderCards();
  document.getElementById('sidebar-sub').textContent =
    `Showing ${getVisibleVessels().length} of ${state.vesselsCache.length} vessels`;
});

document.getElementById('sort-select').addEventListener('change', e => {
  state.currentSort = e.target.value;
  renderCards();
  document.getElementById('sidebar-sub').textContent =
    `Showing ${getVisibleVessels().length} of ${state.vesselsCache.length} vessels`;
});

document.getElementById('cat-filters').addEventListener('click', e => {
  const btn = e.target.closest('.cat-btn');
  if (!btn) return;
  const cat = btn.dataset.cat;
  state.currentCatFilter = (cat === state.currentCatFilter && cat !== 'all') ? 'all' : cat;
  document.querySelectorAll('.cat-btn').forEach(b =>
    b.classList.toggle('active',
      b.dataset.cat === state.currentCatFilter ||
      (state.currentCatFilter === 'all' && b.dataset.cat === 'all')));
  renderCards();
  document.getElementById('sidebar-sub').textContent =
    `Showing ${getVisibleVessels().length} of ${state.vesselsCache.length} vessels`;
});

// ==================================================================
// MAP MOVE — show "Search this area" button + area warning
// ==================================================================
function maybeShowButton() {
  if (state.ready) document.getElementById('search-btn').classList.add('show');
}
function updateAreaWarning() {
  const b    = state.map.getBounds();
  const span = Math.max(b.getNorth() - b.getSouth(), b.getEast() - b.getWest());
  document.getElementById('area-warn').style.display = span > 15 ? 'block' : 'none';
}
state.map.on('moveend', () => { maybeShowButton(); updateAreaWarning(); });

// ==================================================================
// ONBOARDING + SHARED LINK HANDLING
// ==================================================================
setupOnboarding();

const h = new URLSearchParams(location.hash.slice(1));
if (h.get('bbox')) {
  const [minLon, minLat, maxLon, maxLat] = h.get('bbox').split(',').map(Number);
  if (h.get('start')) document.getElementById('date-from').value = h.get('start');
  if (h.get('end'))   document.getElementById('date-to').value   = h.get('end');
  state.map.fitBounds([[minLat, minLon], [maxLat, maxLon]]);
  hideOnboarding();
  loadData({ min_lat: minLat, max_lat: maxLat, min_lon: minLon, max_lon: maxLon,
    start: h.get('start'), end: h.get('end') }).then(() => { state.ready = true; });
}
