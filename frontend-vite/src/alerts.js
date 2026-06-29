/**
 * alerts.js — Real-time brain alert feed + hex flash integration.
 *
 * On init:
 *   1. Hydrate sidebar from GET /api/alerts (persisted, includes ACK state).
 *   2. Open WebSocket to /ws/alerts for live incoming findings.
 *
 * Each incoming WS frame is one AlertFinding JSON object. On receipt:
 *   1. Prepend the alert to the sidebar list (max MAX_ALERTS shown).
 *   2. Flash the corresponding H3 hex cell on the map.
 *   3. Pulse the alert badge count in the topbar.
 *
 * Reconnects automatically with exponential backoff if the WS drops.
 */

import { API_URL } from './config.js';
import { state } from './state.js';

const MAX_ALERTS = 50;
const WS_URL = API_URL.replace(/^http/, 'ws') + '/ws/alerts';

const SEVERITY_ICON = {
  critical: '🔴',
  alert:    '🟠',
  warning:  '🟡',
  info:     '🔵',
};

let _reconnectDelay = 2000;
let _ws = null;

// ---------------------------------------------------------------------------
// Public
// ---------------------------------------------------------------------------

export async function initAlerts() {
  state.alertsCache = [];
  await _hydrate();
  _connect();
  _renderAlerts();
}

export function getAlertCount() {
  return state.alertsCache.filter(a => !a.acknowledged).length;
}

// ---------------------------------------------------------------------------
// Hydration — load persisted alerts from DB on startup
// ---------------------------------------------------------------------------

async function _hydrate() {
  try {
    const res = await fetch(`${API_URL}/api/alerts?limit=50&status=all`);
    if (!res.ok) return;
    const data = await res.json();
    if (Array.isArray(data.alerts)) {
      // DB returns newest-first; unshift to match sidebar order expectation
      state.alertsCache = data.alerts.slice(0, MAX_ALERTS);
    }
  } catch {
    // Backend may not be ready; WS will catch up
  }
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
  // Skip if already in cache (dedup by alert_id); ignore messages without one
  if (!alert.alert_id || state.alertsCache.some(a => a.alert_id === alert.alert_id)) return;

  state.alertsCache.unshift(alert);
  if (state.alertsCache.length > MAX_ALERTS) {
    state.alertsCache.length = MAX_ALERTS;
  }

  _renderAlerts();
  _updateBadge();

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
// Acknowledge
// ---------------------------------------------------------------------------

async function _acknowledge(alertId, cardEl) {
  cardEl.classList.add('alert-acking');
  try {
    const res = await fetch(`${API_URL}/api/alerts/${alertId}/acknowledge`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // Update cache entry in-place
    const entry = state.alertsCache.find(a => a.alert_id === alertId);
    if (entry) {
      entry.acknowledged = true;
      entry.acknowledged_by = data.acknowledged_by;
      entry.acknowledged_at = data.acknowledged_at;
    }

    _renderAlerts();
    _updateBadge();
  } catch (err) {
    console.warn('[alerts] ack failed:', err);
    cardEl.classList.remove('alert-acking');
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

  // Click card → fly to vessel
  panel.querySelectorAll('[data-mmsi]').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.alert-ack-btn')) return;
      const mmsi = card.dataset.mmsi;
      const entry = state.markersByMmsi?.[mmsi];
      if (entry) {
        state.map.flyTo(entry.marker.getLatLng(), Math.max(state.map.getZoom(), 9), { duration: 0.6 });
        entry.marker.openPopup();
      }
    });
  });

  // ACK button clicks
  panel.querySelectorAll('.alert-ack-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const card = btn.closest('.alert-card');
      _acknowledge(btn.dataset.alertId, card);
    });
  });
}

function _alertCard(a) {
  const icon    = SEVERITY_ICON[a.severity] ?? '⚪';
  const age     = _relativeTime(a.triggered_at);
  const mmsiAttr = a.mmsi ? `data-mmsi="${a.mmsi}"` : '';
  const coords  = (a.lat != null && a.lon != null)
    ? `<span class="alert-coords">${a.lat.toFixed(3)}°, ${a.lon.toFixed(3)}°</span>`
    : '';

  const ackState = a.acknowledged
    ? `<span class="alert-acked-label" title="Acknowledged by ${_esc(a.acknowledged_by)}">✓ ACK</span>`
    : `<button class="alert-ack-btn" data-alert-id="${_esc(a.alert_id)}" title="Acknowledge this alert">ACK</button>`;

  return `
    <div class="alert-card alert-${a.severity}${a.acknowledged ? ' alert-acknowledged' : ''}" ${mmsiAttr}>
      <div class="alert-header">
        <span class="alert-icon">${icon}</span>
        <span class="alert-label">${a.rule_label}</span>
        <span class="alert-age">${age}</span>
        ${ackState}
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
  const unacked = state.alertsCache.filter(a => !a.acknowledged);
  const critCount = unacked.filter(a => a.severity === 'critical').length;
  const count = unacked.length;
  badge.textContent = count || '';
  badge.style.display = count ? 'flex' : 'none';
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
