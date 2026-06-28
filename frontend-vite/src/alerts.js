/**
 * alerts.js — Real-time brain alert feed + hex flash integration.
 *
 * Opens a WebSocket to /ws/alerts (served by the spatial engine on port 8000).
 * Each incoming frame is one AlertFinding JSON object. On receipt:
 *   1. Prepend the alert to the sidebar list (max MAX_ALERTS shown).
 *   2. Flash the corresponding H3 hex cell on the map.
 *   3. Pulse the alert badge count in the topbar.
 *
 * Reconnects automatically with exponential backoff if the WS drops.
 */

import { API_URL } from './config.js';
import { state } from './state.js';
import { flashCell } from './h3grid.js';

const MAX_ALERTS = 50;
const WS_URL = API_URL.replace(/^http/, 'ws') + '/ws/alerts';

const SEVERITY_ICON = {
  critical: '🔴',
  alert:    '🟠',
  warning:  '🟡',
  info:     '🔵',
};

const SEVERITY_ORDER = { critical: 0, alert: 1, warning: 2, info: 3 };

let _reconnectDelay = 2000;
let _ws = null;

// ---------------------------------------------------------------------------
// Public
// ---------------------------------------------------------------------------

export function initAlerts() {
  state.alertsCache = [];
  _connect();
  _renderAlerts();
}

export function getAlertCount() {
  return state.alertsCache.length;
}

// ---------------------------------------------------------------------------
// WebSocket lifecycle
// ---------------------------------------------------------------------------

function _connect() {
  if (_ws) { _ws.close(); _ws = null; }

  _ws = new WebSocket(WS_URL);

  _ws.onopen = () => {
    _reconnectDelay = 2000;
    console.info('[alerts] WS connected');
  };

  _ws.onmessage = (e) => {
    let alert;
    try { alert = JSON.parse(e.data); } catch { return; }
    _onAlert(alert);
  };

  _ws.onclose = () => {
    console.warn(`[alerts] WS closed — reconnect in ${_reconnectDelay}ms`);
    setTimeout(() => {
      _reconnectDelay = Math.min(_reconnectDelay * 2, 30_000);
      _connect();
    }, _reconnectDelay);
  };

  _ws.onerror = () => _ws.close();

  state.alertsSocket = _ws;
}

// ---------------------------------------------------------------------------
// Alert handling
// ---------------------------------------------------------------------------

function _onAlert(alert) {
  // Prepend; cap list length.
  state.alertsCache.unshift(alert);
  if (state.alertsCache.length > MAX_ALERTS) {
    state.alertsCache.length = MAX_ALERTS;
  }

  _renderAlerts();
  _updateBadge();

  // Flash the map hex.
  if (alert.h3_index) {
    flashCell(alert.h3_index, alert.severity);
  }

  // Also pan to the vessel if it's a critical alert and the map isn't already nearby.
  if (alert.severity === 'critical' && alert.lat != null && alert.lon != null) {
    const center = state.map.getCenter();
    const dist = Math.hypot(center.lat - alert.lat, center.lng - alert.lon);
    if (dist > 2) {
      state.map.flyTo([alert.lat, alert.lon], Math.max(state.map.getZoom(), 8), {
        duration: 1.2,
      });
    }
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function _renderAlerts() {
  const panel = document.getElementById('alert-list');
  if (!panel) return;

  if (!state.alertsCache.length) {
    panel.innerHTML = '<div class="alert-empty">No alerts — all clear.</div>';
    return;
  }

  panel.innerHTML = state.alertsCache.map(_alertCard).join('');

  // Click-to-focus vessel on map.
  panel.querySelectorAll('[data-mmsi]').forEach(card => {
    card.addEventListener('click', () => {
      const mmsi = card.dataset.mmsi;
      const entry = state.markersByMmsi?.[mmsi];
      if (entry) {
        state.map.flyTo(entry.marker.getLatLng(), Math.max(state.map.getZoom(), 9), { duration: 0.6 });
        entry.marker.openPopup();
      }
    });
  });
}

function _alertCard(a) {
  const icon = SEVERITY_ICON[a.severity] ?? '⚪';
  const age  = _relativeTime(a.triggered_at);
  const mmsiAttr = a.mmsi ? `data-mmsi="${a.mmsi}"` : '';
  const coords = (a.lat != null && a.lon != null)
    ? `<span class="alert-coords">${a.lat.toFixed(3)}°, ${a.lon.toFixed(3)}°</span>`
    : '';

  return `
    <div class="alert-card alert-${a.severity}" ${mmsiAttr}>
      <div class="alert-header">
        <span class="alert-icon">${icon}</span>
        <span class="alert-label">${a.rule_label}</span>
        <span class="alert-age">${age}</span>
      </div>
      <div class="alert-body">
        ${a.mmsi ? `<span class="alert-mmsi">MMSI ${a.mmsi}</span>` : ''}
        ${coords}
      </div>
      <div class="alert-msg">${a.message}</div>
    </div>`;
}

function _updateBadge() {
  const badge = document.getElementById('alert-badge');
  if (!badge) return;
  const critCount = state.alertsCache.filter(a => a.severity === 'critical').length;
  badge.textContent = critCount || state.alertsCache.length || '';
  badge.style.display = state.alertsCache.length ? 'flex' : 'none';
  badge.classList.toggle('alert-badge-critical', critCount > 0);
}

function _relativeTime(isoStr) {
  if (!isoStr) return '';
  const diff = Date.now() - new Date(isoStr).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60)   return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}
