/**
 * governance.js — System Governance & Pipeline Monitor view.
 *
 * Three sections:
 *   1. Ingestion health grid — one card per service (VesselX gateway, spatial
 *      worker, brain/Celery, PostGIS, Redis, analytics engine). Polls /health
 *      on the API and /status on the analytics engine.
 *
 *   2. Rule Engine Orchestrator — lists the 11 VesselX brain rules with their
 *      tier classification and an enable/disable toggle (toggle state is
 *      local-only in this version; a real implementation would PATCH the
 *      brain's config endpoint).
 *
 *   3. Database & Cache Telemetry — health bars for PostGIS query latency,
 *      Redis memory, alert queue depth, and vessel cache age.
 */

import { API_URL } from '../config.js';
import { state } from '../state.js';

// Analytics engine shares the main API until a separate service is deployed
const ANALYTICS_URL = window.__ENV?.ANALYTICS_URL ?? API_URL;

// 11 brain rules with tier and default-enabled state
const RULES = [
  { id: 'fishing_in_mpa',           label: 'fishing_in_mpa',           tier: 1, enabled: true  },
  { id: 'iuu_blacklist',             label: 'iuu_blacklist',             tier: 1, enabled: true  },
  { id: 'spoofing_detected',         label: 'spoofing_detected',         tier: 1, enabled: true  },
  { id: 'high_risk_score',           label: 'high_risk_score ≥ 0.75',   tier: 1, enabled: true  },
  { id: 'mpa_incursion',             label: 'mpa_incursion',             tier: 2, enabled: true  },
  { id: 'ais_gap',                   label: 'ais_gap',                   tier: 2, enabled: true  },
  { id: 'rendezvous_transship_risk', label: 'rendezvous_transship_risk', tier: 2, enabled: true  },
  { id: 'dark_vessel_candidate',     label: 'dark_vessel_candidate',     tier: 2, enabled: true  },
  { id: 'mpa_skirting',              label: 'mpa_skirting',              tier: 3, enabled: true  },
  { id: 'loitering_open_ocean',      label: 'loitering_open_ocean',      tier: 3, enabled: true  },
  { id: 'extended_time_in_zone',     label: 'extended_time_in_zone',     tier: 3, enabled: false },
];

// Toggle state (session-local; survives view switches)
const _ruleState = Object.fromEntries(RULES.map(r => [r.id, r.enabled]));

let _pollInterval = null;
let _initialized  = false;

// ---------------------------------------------------------------------------
// Public init
// ---------------------------------------------------------------------------

export function initGovernance() {
  document.addEventListener('viewchange', ({ detail }) => {
    if (detail.view !== 'governance') return;

    if (!_initialized) {
      _renderRules();
      _wireRefreshBtn();
      _initialized = true;
    }

    _startPolling();
  });

  // Stop polling when leaving governance to avoid background churn
  document.addEventListener('viewchange', ({ detail }) => {
    if (detail.view !== 'governance') _stopPolling();
  });
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

function _startPolling() {
  _poll();
  _pollInterval = setInterval(_poll, 15_000);
}

function _stopPolling() {
  clearInterval(_pollInterval);
  _pollInterval = null;
}

async function _poll() {
  const [apiHealth, analyticsHealth] = await Promise.all([
    _fetchHealth(`${API_URL}/health`),
    _fetchHealth(`${ANALYTICS_URL}/health`),
  ]);

  _renderHealthGrid(apiHealth, analyticsHealth);
  _renderTelemetry(apiHealth, analyticsHealth);

  const sub = document.getElementById('gov-last-check');
  if (sub) {
    const t = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    sub.textContent = `Last checked: ${t}`;
  }
}

async function _fetchHealth(url) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) return { status: 'down', latency: null };
    const t0 = Date.now();
    const data = await res.json();
    return { status: 'healthy', latency: Date.now() - t0, ...data };
  } catch {
    return { status: 'down', latency: null };
  }
}

// ---------------------------------------------------------------------------
// Health grid
// ---------------------------------------------------------------------------

function _renderHealthGrid(apiHealth, analyticsHealth) {
  const grid = document.getElementById('gov-health-grid');
  if (!grid) return;

  const wsStatus = state.alertsSocket?.readyState === WebSocket.OPEN ? 'healthy' : 'down';
  const alertCount = (state.alertsCache || []).length;

  const services = [
    {
      name: 'SPYHOP API',
      status: apiHealth.status,
      metric: apiHealth.latency != null ? `${apiHealth.latency}ms response` : 'unreachable',
    },
    {
      name: 'POSTGIS',
      status: apiHealth.db ?? apiHealth.status,
      metric: apiHealth.db_latency_ms != null ? `${apiHealth.db_latency_ms}ms query` : _statusText(apiHealth.db ?? apiHealth.status),
    },
    {
      name: 'REDIS',
      status: apiHealth.redis ?? apiHealth.status,
      metric: apiHealth.redis_memory ?? _statusText(apiHealth.redis ?? apiHealth.status),
    },
    {
      name: 'CELERY WORKER',
      status: apiHealth.celery ?? 'unknown',
      metric: apiHealth.celery_tasks_pending != null ? `${apiHealth.celery_tasks_pending} queued` : 'status unknown',
    },
    {
      name: 'ALERT WEBSOCKET',
      status: wsStatus,
      metric: `${alertCount} alerts cached`,
    },
    {
      name: 'ANALYTICS ENGINE',
      status: analyticsHealth.status,
      metric: analyticsHealth.latency != null ? `${analyticsHealth.latency}ms response` : 'unreachable',
    },
    {
      name: 'VESSELX GATEWAY',
      status: apiHealth.vesselx_gateway ?? 'unknown',
      metric: apiHealth.ais_feed_lag != null ? `${apiHealth.ais_feed_lag}s feed lag` : 'status unknown',
    },
    {
      name: 'BRAIN EVALUATOR',
      status: apiHealth.brain ?? 'unknown',
      metric: apiHealth.rules_active != null ? `${apiHealth.rules_active} rules active` : 'status unknown',
    },
  ];

  grid.innerHTML = services.map(s => _healthCard(s)).join('');
}

function _healthCard({ name, status, metric }) {
  const cls = _statusClass(status);
  const label = _statusLabel(status);
  return `
    <div class="ghc ${cls}">
      <div class="ghc-name">${name}</div>
      <div class="ghc-status">
        <span class="dot"></span>
        ${label}
      </div>
      <div class="ghc-metric">${_esc(metric)}</div>
    </div>`;
}

function _statusClass(s) {
  if (!s || s === 'unknown') return 'unknown';
  if (s === 'healthy' || s === 'ok' || s === 'connected') return 'healthy';
  if (s === 'degraded' || s === 'slow') return 'degraded';
  return 'down';
}

function _statusLabel(s) {
  const map = { healthy: 'HEALTHY', ok: 'HEALTHY', connected: 'HEALTHY', degraded: 'DEGRADED', slow: 'DEGRADED', down: 'DOWN', unknown: 'UNKNOWN' };
  return map[s] ?? 'UNKNOWN';
}

function _statusText(s) {
  return _statusLabel(s).toLowerCase();
}

// ---------------------------------------------------------------------------
// Rule engine list
// ---------------------------------------------------------------------------

function _renderRules() {
  const list = document.getElementById('gov-rules-list');
  if (!list) return;

  list.innerHTML = RULES.map(r => {
    const tierCls = `t${r.tier}`;
    const tierLabel = `TIER ${r.tier}`;
    const checked = _ruleState[r.id] ? 'checked' : '';
    return `
      <div class="gov-rule-row tier${r.tier}">
        <div class="gov-rule-name">${r.label}</div>
        <span class="gov-rule-tier ${tierCls}">${tierLabel}</span>
        <label class="gov-rule-toggle">
          <input type="checkbox" ${checked} data-rule="${r.id}" />
          <span class="gov-rule-toggle-track"></span>
        </label>
      </div>`;
  }).join('');

  list.querySelectorAll('input[data-rule]').forEach(cb => {
    cb.addEventListener('change', () => {
      _ruleState[cb.dataset.rule] = cb.checked;
    });
  });
}

// ---------------------------------------------------------------------------
// Telemetry bars
// ---------------------------------------------------------------------------

function _renderTelemetry(apiHealth, analyticsHealth) {
  const el = document.getElementById('gov-telemetry');
  if (!el) return;

  const dbLatency   = apiHealth.db_latency_ms ?? null;
  const redisMemPct = apiHealth.redis_memory_pct ?? null;
  const queueDepth  = apiHealth.alert_queue_depth ?? (state.alertsCache?.length ?? 0);
  const cacheAge    = state.vesselsCache?.length
    ? Math.round((Date.now() - (state._lastFetch ?? Date.now())) / 1000)
    : null;

  const rows = [
    {
      label: 'PostGIS Query Latency',
      value: dbLatency != null ? `${dbLatency}ms` : '—',
      pct: dbLatency != null ? Math.min(100, Math.round((dbLatency / 200) * 100)) : 0,
      cls: dbLatency == null ? 'ok' : dbLatency < 50 ? 'ok' : dbLatency < 100 ? 'warn' : 'danger',
    },
    {
      label: 'Redis Memory Usage',
      value: redisMemPct != null ? `${redisMemPct}%` : '—',
      pct: redisMemPct ?? 0,
      cls: redisMemPct == null ? 'ok' : redisMemPct < 60 ? 'ok' : redisMemPct < 85 ? 'warn' : 'danger',
    },
    {
      label: 'Alert Queue Depth',
      value: `${queueDepth} alerts`,
      pct: Math.min(100, Math.round((queueDepth / 50) * 100)),
      cls: queueDepth < 20 ? 'ok' : queueDepth < 40 ? 'warn' : 'danger',
    },
    {
      label: 'Vessel Cache Age',
      value: cacheAge != null ? `${cacheAge}s` : '—',
      pct: cacheAge != null ? Math.min(100, Math.round((cacheAge / 600) * 100)) : 0,
      cls: cacheAge == null ? 'ok' : cacheAge < 120 ? 'ok' : cacheAge < 300 ? 'warn' : 'danger',
    },
    {
      label: 'Analytics Engine',
      value: analyticsHealth.status === 'healthy' ? 'ONLINE' : 'OFFLINE',
      pct: analyticsHealth.status === 'healthy' ? 100 : 0,
      cls: analyticsHealth.status === 'healthy' ? 'ok' : 'danger',
    },
  ];

  el.innerHTML = rows.map(r => `
    <div class="gov-telem-row">
      <div class="gov-telem-head">
        <span class="gov-telem-label">${r.label}</span>
        <span class="gov-telem-value">${r.value}</span>
      </div>
      <div class="gov-telem-bar-wrap">
        <div class="gov-telem-bar ${r.cls}" style="width:${r.pct}%"></div>
      </div>
    </div>`).join('');
}

// ---------------------------------------------------------------------------
// Refresh button
// ---------------------------------------------------------------------------

function _wireRefreshBtn() {
  document.getElementById('gov-refresh-btn')?.addEventListener('click', _poll);
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function _esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
