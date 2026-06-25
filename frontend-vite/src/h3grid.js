import { cellToBoundary } from 'h3-js';
import { state } from './state.js';
import { API_URL } from './config.js';
import { closeStream, openStream } from './api.js';
import { renderCards, updateVesselCounts } from './ui.js';
import { syncMarkers } from './markers.js';

export const H3_RESOLUTION = 7;  // ~5 km² per hex

let _gridLayer = null;
const _selectedCells = new Set();
const _cellPolygons  = {};

// -----------------------------------------------------------------------
// Public API
// -----------------------------------------------------------------------

/**
 * Fetch hex cells from the API for the current map viewport and render
 * them as clickable Leaflet polygons. Clears any previous grid first.
 */
export async function renderHexGrid() {
  if (!state.map) return;
  clearHexGrid();

  const b = state.map.getBounds();
  const qs = new URLSearchParams({
    min_lat: b.getSouth().toFixed(4),
    max_lat: b.getNorth().toFixed(4),
    min_lon: b.getWest().toFixed(4),
    max_lon: b.getEast().toFixed(4),
    resolution: H3_RESOLUTION,
  });

  let cells = [];
  try {
    const res = await fetch(`${API_URL}/api/h3/cells?${qs}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cells = data.features;
  } catch (e) {
    console.warn('H3 grid fetch failed:', e);
    return;
  }

  _gridLayer = L.layerGroup().addTo(state.map);

  for (const cell of cells) {
    const latlngs = cell.boundary;          // already [[lat,lng],…] from API
    const poly = L.polygon(latlngs, _cellStyle(cell.vessel_count, false))
      .addTo(_gridLayer);
    poly._h3id = cell.cell_id;
    poly._vesselCount = cell.vessel_count || 0;
    poly.on('click', () => _toggleCell(cell.cell_id, poly));
    _cellPolygons[cell.cell_id] = poly;
  }
}

/** Remove all hex polygons and reset selection. */
export function clearHexGrid() {
  if (_gridLayer) { _gridLayer.remove(); _gridLayer = null; }
  _selectedCells.clear();
  Object.keys(_cellPolygons).forEach(k => delete _cellPolygons[k]);
  _updateBadge();
}

/** Currently selected H3 cell IDs as a sorted array. */
export function getSelectedCells() {
  return [..._selectedCells].sort();
}

export function hasSelection() {
  return _selectedCells.size > 0;
}

/**
 * Load vessels for the current hex selection and open an SSE stream
 * covering the bounding box of all selected cells.
 */
export async function loadFromHexSelection() {
  if (!hasSelection()) return;

  const ids = getSelectedCells().join(',');
  try {
    const res = await fetch(`${API_URL}/api/vessels/hex?h3_ids=${ids}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.vesselsCache = data.vessels;
    syncMarkers(data.vessels);
    renderCards();
    updateVesselCounts();
  } catch (e) {
    console.warn('Hex vessel fetch failed:', e);
  }

  const bbox = _selectedBbox();
  if (bbox) {
    closeStream();
    openStream(bbox);
  }
}

// -----------------------------------------------------------------------
// Internal helpers
// -----------------------------------------------------------------------

function _toggleCell(cellId, poly) {
  if (_selectedCells.has(cellId)) {
    _selectedCells.delete(cellId);
    poly.setStyle(_cellStyle(poly._vesselCount, false));
  } else {
    _selectedCells.add(cellId);
    poly.setStyle(_cellStyle(poly._vesselCount, true));
  }
  _updateBadge();
}

function _cellStyle(vesselCount, selected) {
  const occupied = (vesselCount || 0) > 0;
  return {
    fillColor:   selected   ? '#e67e22'
               : occupied   ? '#1abc9c'
               :              '#2c3e50',
    fillOpacity: selected   ? 0.55 : occupied ? 0.35 : 0.08,
    color:       selected   ? '#e67e22' : '#1abc9c',
    weight:      selected   ? 2    : 0.8,
    opacity:     selected   ? 0.9  : 0.4,
  };
}

function _updateBadge() {
  const badge = document.getElementById('hex-selection-count');
  const btn   = document.getElementById('hex-search-btn');
  if (badge) badge.textContent = _selectedCells.size > 0
    ? `${_selectedCells.size} cells selected` : '';
  if (btn) btn.disabled = _selectedCells.size === 0;
}

/** Bounding box that encloses all selected cells — used to open the SSE stream. */
function _selectedBbox() {
  if (!_selectedCells.size) return null;
  let minLat =  90, maxLat = -90;
  let minLon = 180, maxLon = -180;
  for (const cellId of _selectedCells) {
    for (const [lat, lng] of cellToBoundary(cellId)) {
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
      if (lng < minLon) minLon = lng;
      if (lng > maxLon) maxLon = lng;
    }
  }
  return { min_lat: minLat, max_lat: maxLat, min_lon: minLon, max_lon: maxLon };
}
