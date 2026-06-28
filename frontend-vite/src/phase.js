/**
 * FSM Phase Controller — Global Level-Select ↔ Isolated Region Mode
 *
 * GLOBAL phase: map is a passive visual menu. No data loads. Region polygons
 *   are rendered; hovering highlights them; clicking enters REGION phase.
 *
 * REGION phase: map is locked to the selected region's bounds and zoom range.
 *   A single API/SSE query loads only vessels inside that bbox. An "Exit"
 *   button cuts the connection and returns to GLOBAL phase.
 */

import { state } from './state.js';
import { REGIONS } from './regions.js';
import { loadData } from './api.js';
import { clearMarkers } from './markers.js';

// ─── FSM state ────────────────────────────────────────────────────────────────

let _phase = 'GLOBAL';   // 'GLOBAL' | 'REGION'
let _activeRegion = null;

// Leaflet layers owned by the phase controller
let _polygonLayer = null;
let _activePolygon = null;
let _exitBtn = null;
let _regionHeader = null;

export function currentPhase() { return _phase; }
export function activeRegion()  { return _activeRegion; }

// ─── Phase 1 init ────────────────────────────────────────────────────────────

export function initGlobalPhase() {
  const map = state.map;

  // World view, no zoom/pan constraints
  map.setMaxBounds(null);
  map.setMinZoom(2);
  map.setMaxZoom(6);
  map.setView([20, 10], 2, { animate: true, duration: 0.8 });

  // Disable scroll zoom — user can only navigate by clicking regions
  map.scrollWheelZoom.disable();
  map.doubleClickZoom.disable();
  map.dragging.disable();

  _buildPolygons();
  _showGlobalUI();

  _phase = 'GLOBAL';
  _activeRegion = null;
}

function _buildPolygons() {
  if (_polygonLayer) _polygonLayer.clearLayers();
  else _polygonLayer = L.layerGroup().addTo(state.map);

  REGIONS.forEach(region => {
    const latlngs = region.polygon.map(([lat, lon]) => L.latLng(lat, lon));

    const poly = L.polygon(latlngs, {
      color:       region.color,
      fillColor:   region.color,
      fillOpacity: 0.06,
      weight:      1.5,
      opacity:     0.5,
      className:   'region-poly',
    });

    poly.on('mouseover', () => {
      poly.setStyle({ fillOpacity: 0.22, weight: 2.5, opacity: 0.9 });
      _showRegionTooltip(region);
    });
    poly.on('mouseout', () => {
      if (_activePolygon !== poly) {
        poly.setStyle({ fillOpacity: 0.06, weight: 1.5, opacity: 0.5 });
      }
      _hideRegionTooltip();
    });
    poly.on('click', () => enterRegionPhase(region));

    _polygonLayer.addLayer(poly);
    poly._region = region;
  });
}

// ─── Phase 2 enter ───────────────────────────────────────────────────────────

export function enterRegionPhase(region) {
  if (_phase === 'REGION') exitRegionPhase();

  _phase = 'REGION';
  _activeRegion = region;

  const map = state.map;

  // Fly into region
  map.flyToBounds(region.bounds, { padding: [40, 40], duration: 1.2, maxZoom: region.zoom });

  // After animation completes, lock the map
  map.once('moveend', () => {
    map.setMaxBounds(L.latLngBounds(region.bounds));
    map.setMinZoom(region.zoom - 1);
    map.setMaxZoom(region.zoom + 3);

    // Re-enable navigation within the locked region
    map.scrollWheelZoom.enable();
    map.doubleClickZoom.enable();
    map.dragging.enable();

    // Hide region polygons
    if (_polygonLayer) _polygonLayer.remove();

    // Show region UI
    _showRegionUI(region);

    // Fire the data pipeline
    const [[swLat, swLon], [neLat, neLon]] = region.bounds;
    loadData({
      min_lat: swLat, max_lat: neLat,
      min_lon: swLon, max_lon: neLon,
    });
  });
}

// ─── Phase 1 return ──────────────────────────────────────────────────────────

export function exitRegionPhase() {
  const map = state.map;

  // Kill live data connection
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }

  // Clear vessel data from memory and map
  clearMarkers();
  state.vesselsCache = [];

  // Remove region UI
  _hideRegionUI();

  // Unlock map
  map.setMaxBounds(null);
  map.scrollWheelZoom.disable();
  map.doubleClickZoom.disable();
  map.dragging.disable();

  // Fly back out
  map.flyTo([20, 10], 2, { animate: true, duration: 1.0 });

  map.once('moveend', () => {
    map.setMinZoom(2);
    map.setMaxZoom(6);
    if (_polygonLayer) _polygonLayer.addTo(map);
    _showGlobalUI();
  });

  _phase = 'GLOBAL';
  _activeRegion = null;
}

// ─── UI helpers ──────────────────────────────────────────────────────────────

let _tooltip = null;

function _showRegionTooltip(region) {
  if (_tooltip) _tooltip.remove();
  _tooltip = document.createElement('div');
  _tooltip.className = 'region-tooltip';
  _tooltip.innerHTML = `
    <div class="rt-name">${region.name}</div>
    <div class="rt-sub">${region.sub}</div>
    <div class="rt-vessels"><span class="rt-dot"></span>${region.vessels} active vessels</div>
    <div class="rt-cta">CLICK TO ENTER</div>
  `;
  document.getElementById('map').appendChild(_tooltip);
}

function _hideRegionTooltip() {
  if (_tooltip) { _tooltip.remove(); _tooltip = null; }
}

function _showGlobalUI() {
  document.getElementById('map').classList.add('phase-global');
  document.getElementById('map').classList.remove('phase-region');

  // Show global header overlay
  if (!_regionHeader) {
    _regionHeader = document.createElement('div');
    _regionHeader.id = 'global-header';
    _regionHeader.innerHTML = `
      <div class="gh-title">ABYSSAL MARITIME INTELLIGENCE</div>
      <div class="gh-sub">SELECT A REGION TO BEGIN MONITORING</div>
    `;
    document.getElementById('map').appendChild(_regionHeader);
  } else {
    _regionHeader.style.display = 'block';
  }

  // Hide ops sidebar controls that only matter in region mode
  document.querySelector('.sidebar')?.classList.add('phase-global-hidden');
  document.getElementById('search-btn')?.classList.remove('show');
}

function _hideGlobalUI() {
  document.getElementById('map').classList.remove('phase-global');
  document.getElementById('map').classList.add('phase-region');
  if (_regionHeader) _regionHeader.style.display = 'none';
  document.querySelector('.sidebar')?.classList.remove('phase-global-hidden');
}

function _showRegionUI(region) {
  _hideGlobalUI();

  // Exit button
  if (_exitBtn) _exitBtn.remove();
  _exitBtn = document.createElement('button');
  _exitBtn.id = 'region-exit-btn';
  _exitBtn.innerHTML = `← EXIT ${region.name.toUpperCase()}`;
  _exitBtn.addEventListener('click', exitRegionPhase);
  document.getElementById('map').appendChild(_exitBtn);

  // Region name chip
  const chip = document.createElement('div');
  chip.id = 'region-chip';
  chip.style.borderColor = region.color;
  chip.innerHTML = `
    <span class="rc-dot" style="background:${region.color}"></span>
    <span class="rc-name">${region.name}</span>
    <span class="rc-live">LIVE</span>
  `;
  document.getElementById('map').appendChild(chip);
  chip._cleanup = () => chip.remove();
  _exitBtn._chip = chip;

  // Update sidebar title
  const sub = document.getElementById('sidebar-sub');
  if (sub) sub.textContent = `Monitoring ${region.name}`;
}

function _hideRegionUI() {
  _exitBtn?._chip?.remove();
  _exitBtn?.remove();
  _exitBtn = null;
}
