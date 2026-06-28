import { cellToBoundary, cellToParent } from "h3-js";
import { state } from "./state.js";
import { API_URL } from "./config.js";
import { closeStream, openStream } from "./api.js";
import { renderCards, updateVesselCounts } from "./ui.js";
import { syncMarkers } from "./markers.js";

// Dynamic resolution: targets ~30 visible cells at any zoom level.
// Each step down in zoom ~halves the number of visible cells, so we coarsen
// the resolution by 1 every 2 zoom levels to compensate.
export function getResolution() {
  const z = state.map?.getZoom() ?? 5;
  if (z <= 2) return 1; // ~600K km² — continent slabs
  if (z <= 4) return 2; // ~87K km²  — country scale
  if (z <= 6) return 3; // ~12K km²  — large region
  if (z <= 8) return 4; // ~1.8K km² — patrol zone
  if (z <= 10) return 5; // ~250 km²  — coastal detail
  return 6; // ~36 km²   — port precision
}

// For backward compat with flashCell's cellToParent call.
export const H3_RESOLUTION = 4;

let _gridLayer = null;
const _selectedCells = new Set();
const _cellPolygons = {};

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

  const res = getResolution();
  const b = state.map.getBounds();
  const qs = new URLSearchParams({
    min_lat: b.getSouth().toFixed(4),
    max_lat: b.getNorth().toFixed(4),
    min_lon: b.getWest().toFixed(4),
    max_lon: b.getEast().toFixed(4),
    resolution: res,
  });

  let cells = [];
  try {
    const res = await fetch(`${API_URL}/api/h3/cells?${qs}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cells = data.features;
  } catch (e) {
    console.warn("H3 grid fetch failed:", e);
    return;
  }

  _gridLayer = L.layerGroup().addTo(state.map);

  for (const cell of cells) {
    const latlngs = cell.boundary; // already [[lat,lng],…] from API
    const poly = L.polygon(latlngs, _cellStyle(cell.vessel_count, false)).addTo(
      _gridLayer,
    );
    poly._h3id = cell.cell_id;
    poly._vesselCount = cell.vessel_count || 0;
    poly.on("click", () => _toggleCell(cell.cell_id, poly));
    _cellPolygons[cell.cell_id] = poly;
  }
}

/** Remove all hex polygons and reset selection. */
export function clearHexGrid() {
  if (_gridLayer) {
    _gridLayer.remove();
    _gridLayer = null;
  }
  _selectedCells.clear();
  Object.keys(_cellPolygons).forEach((k) => delete _cellPolygons[k]);
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

  const ids = getSelectedCells().join(",");
  try {
    const res = await fetch(`${API_URL}/api/vessels/hex?h3_ids=${ids}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.vesselsCache = data.vessels;
    syncMarkers(data.vessels);
    renderCards();
    updateVesselCounts();
  } catch (e) {
    console.warn("Hex vessel fetch failed:", e);
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
    fillColor: selected ? "#e67e22" : occupied ? "#1abc9c" : "#2c3e50",
    fillOpacity: selected ? 0.55 : occupied ? 0.35 : 0.08,
    color: selected ? "#e67e22" : "#1abc9c",
    weight: selected ? 2 : 0.8,
    opacity: selected ? 0.9 : 0.4,
  };
}

function _updateBadge() {
  const badge = document.getElementById("hex-selection-count");
  const btn = document.getElementById("hex-search-btn");
  if (badge)
    badge.textContent =
      _selectedCells.size > 0 ? `${_selectedCells.size} cells selected` : "";
  if (btn) btn.disabled = _selectedCells.size === 0;
}

/**
 * Flash a hex cell to signal an inbound alert, then revert to its resting style.
 * @param {string} cellId  - H3 cell ID (must be currently rendered on the grid).
 * @param {string} severity - 'critical' | 'alert' | 'warning' | 'info'
 */
export function flashCell(cellId, severity = "alert") {
  // Alert h3_index may be stored at a finer resolution than the display grid.
  // Walk up to the display resolution so we always find a rendered polygon.
  let lookupId = cellId;
  if (!_cellPolygons[lookupId]) {
    try {
      lookupId = cellToParent(cellId, getResolution());
    } catch {
      return;
    }
  }
  const poly = _cellPolygons[lookupId];
  if (!poly) return;

  const flashColor =
    severity === "critical"
      ? "#ff1053"
      : severity === "alert"
        ? "#F45700"
        : severity === "warning"
          ? "#ffaa00"
          : "#32d6ff";

  const originalStyle = _cellStyle(
    poly._vesselCount,
    _selectedCells.has(cellId),
  );

  // Three-pulse flash — set → revert → set → revert → set → revert
  const pulseOn = {
    fillColor: flashColor,
    fillOpacity: 0.75,
    color: flashColor,
    weight: 3,
    opacity: 1,
  };
  const pulseOff = {
    ...originalStyle,
    fillOpacity: originalStyle.fillOpacity * 0.4,
  };

  poly.setStyle(pulseOn);
  setTimeout(() => poly.setStyle(pulseOff), 200);
  setTimeout(() => poly.setStyle(pulseOn), 400);
  setTimeout(() => poly.setStyle(pulseOff), 600);
  setTimeout(() => poly.setStyle(pulseOn), 800);
  setTimeout(() => poly.setStyle(originalStyle), 3000);
}

/** Bounding box that encloses all selected cells — used to open the SSE stream. */
function _selectedBbox() {
  if (!_selectedCells.size) return null;
  let minLat = 90,
    maxLat = -90;
  let minLon = 180,
    maxLon = -180;
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
