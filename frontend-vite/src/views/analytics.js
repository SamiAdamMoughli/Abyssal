/**
 * analytics.js — Spatial Analytics & Trend Explorer view.
 *
 * Owns a second Leaflet map instance (#analytics-map) showing historical
 * density heatmaps rather than live vessel positions.
 *
 * Three right-panel sections:
 *   1. H3 density summary — top cells by accumulated vessel-hours
 *   2. MPA dwell — vessels with most time inside protected areas
 *   3. V2V encounter matrix — all recorded transship/bunkering encounters
 *
 * Temporal playback bar animates vessel positions day-by-day over the
 * selected date range using the /api/vessels endpoint filtered by date.
 */

import 'leaflet/dist/leaflet.css';
import 'leaflet.heat';
import { API_URL } from '../config.js';
import { state } from '../state.js';

const ANALYTICS_URL = window.__ENV?.ANALYTICS_URL ?? 'http://localhost:8001';

let _map = null;
let _heatLayer = null;
let _playbackTimer = null;
let _playbackDays = [];
let _playbackIdx = 0;
let _initialized = false;

// ---------------------------------------------------------------------------
// Public init
// ---------------------------------------------------------------------------

export function initAnalytics() {
  document.addEventListener('viewchange', ({ detail }) => {
    if (detail.view !== 'analytics') return;

    if (!_initialized) {
      _bootMap();
      _setDefaultDates();
      _wireControls();
      _initialized = true;
    } else {
      // Leaflet needs a size refresh every time the view is shown
      requestAnimationFrame(() => _map?.invalidateSize());
    }
  });
}

// ---------------------------------------------------------------------------
// Map bootstrap
// ---------------------------------------------------------------------------

function _bootMap() {
  const container = document.getElementById('analytics-map');
  if (!container || _map) return;

  _map = L.map(container, { zoomControl: true }).setView([0, 0], 3);

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap &copy; CARTO',
    maxZoom: 19,
  }).addTo(_map);
}

function _setDefaultDates() {
  const to   = new Date();
  const from = new Date(Date.now() - 30 * 864e5);
  const fmt  = d => d.toISOString().slice(0, 10);
  const aFrom = document.getElementById('analytics-from');
  const aTo   = document.getElementById('analytics-to');
  if (aFrom && !aFrom.value) aFrom.value = fmt(from);
  if (aTo   && !aTo.value)   aTo.value   = fmt(to);
}

// ---------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------

let _controlsWired = false;
function _wireControls() {
  if (_controlsWired) return;
  _controlsWired = true;

  document.getElementById('analytics-run')?.addEventListener('click', _runAnalysis);

  // Playback
  document.getElementById('pb-play')?.addEventListener('click', _startPlayback);
  document.getElementById('pb-pause')?.addEventListener('click', _pausePlayback);
  document.getElementById('pb-reset')?.addEventListener('click', _resetPlayback);

  document.getElementById('pb-slider')?.addEventListener('input', e => {
    _pausePlayback();
    _playbackIdx = parseInt(e.target.value, 10);
    _renderPlaybackFrame(_playbackIdx);
  });
}

// ---------------------------------------------------------------------------
// Analysis runner
// ---------------------------------------------------------------------------

async function _runAnalysis() {
  const from = document.getElementById('analytics-from')?.value;
  const to   = document.getElementById('analytics-to')?.value;
  if (!from || !to) return;

  _setBodyLoading('density-body');
  _setBodyLoading('mpa-dwell-body');
  _setBodyLoading('encounter-body');

  // Build day list for playback
  _playbackDays = _dayRange(from, to);
  const slider = document.getElementById('pb-slider');
  if (slider) {
    slider.max   = Math.max(_playbackDays.length - 1, 0);
    slider.value = 0;
  }
  _resetPlayback();

  await Promise.all([
    _loadHeatmap(from, to),
    _loadMpaDwell(from, to),
    _loadEncounters(from, to),
  ]);
}

// ---------------------------------------------------------------------------
// Heatmap
// ---------------------------------------------------------------------------

async function _loadHeatmap(from, to) {
  const densityBody = document.getElementById('density-body');
  try {
    const b = _map.getBounds();
    const url = `${ANALYTICS_URL}/heatmap?min_lat=${b.getSouth()}&max_lat=${b.getNorth()}&min_lon=${b.getWest()}&max_lon=${b.getEast()}&start=${from}&end=${to}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { points, top_cells } = await res.json();

    if (_heatLayer) { _heatLayer.remove(); _heatLayer = null; }
    if (points?.length) {
      _heatLayer = L.heatLayer(points, {
        radius: 22, blur: 18, maxZoom: 12,
        gradient: { 0.2: '#0ff', 0.5: '#ff0', 0.8: '#f80', 1.0: '#f00' },
      }).addTo(_map);
    }

    if (densityBody) {
      densityBody.innerHTML = (top_cells || []).length
        ? (top_cells || []).slice(0, 8).map(c => `
            <div class="ap-dwell-row">
              <div class="ap-dwell-name" title="${c.h3_index}">${c.h3_index?.slice(0, 10)}…</div>
              <div class="ap-dwell-bar-wrap"><div class="ap-dwell-bar" style="width:${Math.round((c.vessel_hours / (top_cells[0].vessel_hours || 1)) * 100)}%"></div></div>
              <div class="ap-dwell-hrs">${Math.round(c.vessel_hours)}h</div>
            </div>`).join('')
        : '<div class="ap-empty">No density data returned for this period</div>';
    }
  } catch {
    if (densityBody) densityBody.innerHTML = '<div class="ap-empty">Analytics engine offline — heatmap unavailable</div>';
  }
}

// ---------------------------------------------------------------------------
// MPA dwell
// ---------------------------------------------------------------------------

async function _loadMpaDwell(from, to) {
  const body = document.getElementById('mpa-dwell-body');
  try {
    const res = await fetch(`${ANALYTICS_URL}/mpa/dwell?start=${from}&end=${to}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { offenders } = await res.json();

    const top = (offenders || []).slice(0, 10);
    const max = top[0]?.dwell_hours || 1;

    if (body) {
      body.innerHTML = top.length
        ? top.map(o => `
            <div class="ap-dwell-row">
              <div class="ap-dwell-name">${_esc(o.vessel_name || o.mmsi)}</div>
              <div class="ap-dwell-bar-wrap"><div class="ap-dwell-bar" style="width:${Math.round((o.dwell_hours / max) * 100)}%"></div></div>
              <div class="ap-dwell-hrs">${Math.round(o.dwell_hours)}h</div>
            </div>`).join('')
        : '<div class="ap-empty">No MPA dwell events in this period</div>';
    }
  } catch {
    // Fall back to client-side calc from state.vesselsCache
    _mpaDwellFromCache(body);
  }
}

function _mpaDwellFromCache(body) {
  if (!body) return;
  const inMpa = (state.vesselsCache || [])
    .filter(v => v.in_protected_area)
    .sort((a, b) => (b.risk_score ?? 0) - (a.risk_score ?? 0))
    .slice(0, 10);

  body.innerHTML = inMpa.length
    ? inMpa.map(v => `
        <div class="ap-dwell-row">
          <div class="ap-dwell-name">${_esc(v.name)} <span style="color:var(--ss-w40);font-size:9px">${v.mmsi}</span></div>
          <div class="ap-dwell-bar-wrap"><div class="ap-dwell-bar" style="width:${Math.round(v.risk_score * 100)}%"></div></div>
          <div class="ap-dwell-hrs" style="color:var(--ss-red)">LIVE</div>
        </div>`).join('')
    : '<div class="ap-empty">No vessels currently inside MPAs</div>';
}

// ---------------------------------------------------------------------------
// Encounter matrix
// ---------------------------------------------------------------------------

async function _loadEncounters(from, to) {
  const body = document.getElementById('encounter-body');
  try {
    const res = await fetch(`${API_URL}/api/encounters?start=${from}&end=${to}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { encounters } = await res.json();
    _renderEncounters(body, encounters || []);
  } catch {
    _encountersFromCache(body);
  }
}

function _encountersFromCache(body) {
  if (!body) return;
  const vessels = (state.vesselsCache || []).filter(v => v.encounter_events_90d > 0);
  if (!vessels.length) {
    body.innerHTML = '<div class="ap-empty">No V2V encounters recorded in cached data</div>';
    return;
  }
  // Synthesize encounter rows from vessel cache (no partner data available)
  body.innerHTML = vessels.slice(0, 12).map(v => `
    <div class="enc-row">
      <div class="enc-vessel">${_esc(v.name)}</div>
      <div class="enc-meta">
        <span class="enc-type-badge proximity">PROXIMITY</span>
        <span class="enc-date">${v.encounter_events_90d}× in 90d</span>
      </div>
      <div class="enc-vessel right" style="color:var(--ss-w40)">partner unknown</div>
    </div>`).join('');
}

function _renderEncounters(body, encounters) {
  if (!body) return;
  if (!encounters.length) {
    body.innerHTML = '<div class="ap-empty">No V2V encounters for selected period</div>';
    return;
  }

  const TYPE_CLASS = {
    transshipment: 'transship',
    bunkering: 'bunkering',
    proximity: 'proximity',
  };

  body.innerHTML = encounters.map(e => {
    const cls = TYPE_CLASS[e.type] ?? 'proximity';
    const date = e.timestamp ? new Date(e.timestamp).toLocaleDateString('en-GB', { day:'2-digit', month:'short' }) : '—';
    return `
      <div class="enc-row">
        <div class="enc-vessel">${_esc(e.vessel_a_name || e.mmsi_a)}</div>
        <div class="enc-meta">
          <span class="enc-type-badge ${cls}">${_esc(e.type || 'PROXIMITY').toUpperCase()}</span>
          <span class="enc-date">${date} · ${e.duration_min ?? '?'}m</span>
        </div>
        <div class="enc-vessel right">${_esc(e.vessel_b_name || e.mmsi_b)}</div>
      </div>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Temporal playback
// ---------------------------------------------------------------------------

function _startPlayback() {
  if (!_playbackDays.length) return;
  document.getElementById('pb-play').disabled  = true;
  document.getElementById('pb-pause').disabled = false;

  const speed  = parseInt(document.getElementById('pb-speed-select')?.value ?? '12', 10);
  const delay  = Math.max(100, Math.round(1000 / speed));

  _playbackTimer = setInterval(() => {
    if (_playbackIdx >= _playbackDays.length - 1) {
      _pausePlayback();
      return;
    }
    _playbackIdx++;
    const slider = document.getElementById('pb-slider');
    if (slider) slider.value = _playbackIdx;
    _renderPlaybackFrame(_playbackIdx);
  }, delay);
}

function _pausePlayback() {
  clearInterval(_playbackTimer);
  _playbackTimer = null;
  const play  = document.getElementById('pb-play');
  const pause = document.getElementById('pb-pause');
  if (play)  play.disabled  = false;
  if (pause) pause.disabled = true;
}

function _resetPlayback() {
  _pausePlayback();
  _playbackIdx = 0;
  const slider = document.getElementById('pb-slider');
  if (slider) slider.value = 0;
  _renderPlaybackFrame(0);
}

function _renderPlaybackFrame(idx) {
  const label = document.getElementById('pb-date-label');
  if (label) label.textContent = _playbackDays[idx] ?? '—';
  // Future: filter vessel points to this date and re-render on the analytics map
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function _dayRange(from, to) {
  const days = [];
  const cur  = new Date(from);
  const end  = new Date(to);
  while (cur <= end) {
    days.push(cur.toISOString().slice(0, 10));
    cur.setDate(cur.getDate() + 1);
  }
  return days;
}

function _setBodyLoading(id) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = '<div class="ap-empty" style="color:var(--ss-cyan)">◈ Loading…</div>';
}

function _esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
