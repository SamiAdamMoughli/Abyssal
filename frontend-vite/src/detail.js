/**
 * Vessel detail panel — slides in from the right on marker click.
 * Fetches /api/vessels/{mmsi}/detail and renders the full fused profile.
 */

import L from 'leaflet';
import { API_URL } from './config.js';
import { state } from './state.js';
import { css } from './utils.js';

export { API_URL };

const panel   = () => document.getElementById('detail-panel');
const content = () => document.getElementById('detail-content');

export function openDetail(mmsi) {
  const p = panel();
  p.classList.add('open');
  content().innerHTML = '<div class="detail-loading">◈ Loading vessel profile…</div>';
  _clearDetailOverlay();
  _fetchAndRender(mmsi);
}

export function closeDetail() {
  panel().classList.remove('open');
  _clearDetailOverlay();
}

function _clearDetailOverlay() {
  if (state.detailLayer) state.detailLayer.clearLayers();
}

async function _fetchAndRender(mmsi) {
  let d;
  try {
    const r = await fetch(`${API_URL}/api/vessels/${mmsi}/detail`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    d = await r.json();
  } catch (e) {
    content().innerHTML = `<div class="detail-error">Failed to load profile: ${e.message}</div>`;
    return;
  }
  content().innerHTML = _renderDetail(d);
  _plotDetailOverlay(d);
}

function _plotDetailOverlay(d) {
  if (!state.detailLayer || !state.map) return;
  const nav  = d.live_navigation || {};
  const hist = d.historical_anomalies || {};

  // Fly to vessel position
  if (nav.lat != null && nav.lon != null) {
    state.map.flyTo([nav.lat, nav.lon], Math.max(state.map.getZoom(), 6), { duration: 1.2 });
  }

  // Loitering zones — amber circles at each event location
  (hist.loitering_detail || []).forEach((ev, i) => {
    if (ev.lat == null || ev.lon == null) return;
    const hours = ev.hours ?? 0;
    // radius grows with duration, min 20 km
    const radiusM = Math.max(20000, hours * 2000);
    L.circle([ev.lat, ev.lon], {
      radius: radiusM,
      color: css('--ss-amber'),
      weight: 1.5,
      fillOpacity: 0.08,
      dashArray: '4 6',
      className: 'detail-loiter-zone',
    }).bindTooltip(
      `Loitering #${i + 1}<br>${_fmtDate(ev.start)} – ${_fmtDate(ev.end)}<br>${hours != null ? hours.toFixed(1) + ' h' : ''}`,
      { sticky: true, className: 'dp-map-tip' }
    ).addTo(state.detailLayer);

    // Crosshair marker at centre
    L.circleMarker([ev.lat, ev.lon], {
      radius: 4,
      color: css('--ss-amber'),
      fillColor: css('--ss-amber'),
      fillOpacity: 0.6,
      weight: 1,
    }).addTo(state.detailLayer);
  });

  // Destination geocoded point
  if (nav.destination_coords?.lat != null) {
    const { lat, lon, country } = nav.destination_coords;
    L.circleMarker([lat, lon], {
      radius: 6,
      color: css('--ss-teal'),
      fillColor: css('--ss-teal'),
      fillOpacity: 0.5,
      weight: 1.5,
      dashArray: '3 3',
    }).bindTooltip(
      `Destination: ${_esc(nav.destination_raw || '')}${country ? ` (${country})` : ''}`,
      { sticky: true, className: 'dp-map-tip' }
    ).addTo(state.detailLayer);
  }
}

// -----------------------------------------------------------------------
// Renderers — exported so fleet.js can reuse without re-fetching
// -----------------------------------------------------------------------

export function renderVesselProfile(d) {
  return _renderDetail(d);
}

export async function fetchVesselProfile(mmsi) {
  const r = await fetch(`${API_URL}/api/vessels/${mmsi}/detail`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function _renderDetail(d) {
  const id   = d.identity   || {};
  const nav  = d.live_navigation || {};
  const hist = d.historical_anomalies || {};
  const gfw  = d.gfw_registry || {};
  const score = d.calculated_risk_score ?? null;

  const riskCls = score === null ? 'unknown'
                : score >= 70   ? 'hi'
                : score >= 35   ? 'mid'
                :                 'lo';

  const loiteringCards = _renderLoiteringDetail(hist.loitering_detail);
  const gapCards       = _renderGapDetail(hist.gap_detail);
  const encounterCards = _renderEncounterDetail(hist.encounter_detail);

  return `
    <div class="dp-header">
      <div class="dp-score ${riskCls}">${score !== null ? Math.round(score) : '?'}</div>
      <div class="dp-title">
        <div class="dp-name">${_esc(d.name)}</div>
        <div class="dp-mmsi">MMSI ${d.mmsi}${d.imo ? ` · IMO ${d.imo}` : ''}</div>
      </div>
      <button class="dp-close" onclick="document.getElementById('detail-panel').classList.remove('open')">✕</button>
    </div>

    ${hist.sanction_status && hist.sanction_status !== 'CLEAN' ? `
      <div class="dp-alert dp-alert--sanctions">⚠ SANCTIONS: ${_esc(hist.sanction_status)}</div>` : ''}
    ${hist.iuu_status && hist.iuu_status !== 'CLEAN' ? `
      <div class="dp-alert dp-alert--iuu">⚠ IUU BLACKLIST: ${_esc(hist.iuu_status)}</div>` : ''}

    <div class="dp-section">
      <div class="dp-section-title">IDENTITY</div>
      <div class="dp-grid">
        ${_row('Flag',       id.flag)}
        ${_row('Type',       id.type)}
        ${_row('Length',     id.length_m   ? `${id.length_m} m`  : null)}
        ${_row('Tonnage',    id.tonnage_gt ? `${id.tonnage_gt} GT` : null)}
        ${_row('Callsign',   id.callsign)}
        ${_row('Built',      id.built_year)}
        ${_row('Owner',      id.owner)}
      </div>
    </div>

    ${_renderGFWBlock(gfw, id)}

    <div class="dp-section">
      <div class="dp-section-title">NAVIGATION</div>
      <div class="dp-grid">
        ${_row('Position',   nav.lat != null ? `${nav.lat.toFixed(4)}° ${nav.lon.toFixed(4)}°` : null)}
        ${_row('Speed',      nav.speed_knots != null ? `${nav.speed_knots.toFixed(1)} kn` : null)}
        ${_row('Heading',    nav.heading     != null ? `${Math.round(nav.heading)}°`       : null)}
        ${_row('Destination', nav.destination_raw)}
        ${nav.destination_coords ? _row('Dest. coords',
            `${nav.destination_coords.lat.toFixed(3)}°, ${nav.destination_coords.lon.toFixed(3)}°
             (${nav.destination_coords.country || ''})`) : ''}
        ${_row('Days since port', nav.days_since_port != null ? nav.days_since_port.toFixed(1) : null)}
        ${_row('Dist. to port',   nav.distance_to_port_nm != null ? `${nav.distance_to_port_nm.toFixed(0)} nm` : null)}
      </div>
    </div>

    <div class="dp-section">
      <div class="dp-section-title">90-DAY ANOMALY HISTORY</div>
      <div class="dp-stats">
        ${_stat(hist.loitering_events_90d, 'LOITERING')}
        ${_stat(hist.gap_events_90d,       'AIS GAPS')}
        ${_stat(hist.encounter_events_90d, 'ENCOUNTERS')}
        ${_stat((hist.port_visits_90d || []).length, 'PORT CALLS')}
      </div>
      ${hist.last_encounter_mmsi ? `
        <div class="dp-meta">Last encounter with MMSI <b>${_esc(hist.last_encounter_mmsi)}</b></div>` : ''}
      ${hist.in_protected_area ? `
        <div class="dp-meta dp-meta--warn">⚠ Currently inside a Marine Protected Area</div>` : ''}
      ${hist.nearest_mpa_nm != null ? `
        <div class="dp-meta">Nearest MPA: ${hist.nearest_mpa_nm.toFixed(0)} nm</div>` : ''}
    </div>

    ${loiteringCards ? `
    <div class="dp-section">
      <div class="dp-section-title">LOITERING EVENTS</div>
      ${loiteringCards}
    </div>` : ''}

    ${gapCards ? `
    <div class="dp-section">
      <div class="dp-section-title">AIS DARK PERIODS</div>
      ${gapCards}
    </div>` : ''}

    ${encounterCards ? `
    <div class="dp-section">
      <div class="dp-section-title">VESSEL ENCOUNTERS</div>
      ${encounterCards}
    </div>` : ''}

    ${(hist.port_visits_90d || []).length ? `
    <div class="dp-section">
      <div class="dp-section-title">RECENT PORT CALLS</div>
      ${hist.port_visits_90d.map(v => `
        <div class="dp-port-row">
          <span class="dp-port-flag">${v.country || '—'}</span>
          <span class="dp-port-name">${_esc(v.port_name || 'Unknown port')}</span>
          <span class="dp-port-date">${_fmtDate(v.start)}</span>
        </div>`).join('')}
    </div>` : ''}

    ${d.top_reason || (d.reasons || []).length ? `
    <div class="dp-section">
      <div class="dp-section-title">RISK FACTORS</div>
      ${d.top_reason ? `<div class="dp-reason dp-reason--top">${_esc(d.top_reason)}</div>` : ''}
      ${(d.reasons || []).filter(r => r !== d.top_reason).map(r =>
        `<div class="dp-reason">${_esc(r)}</div>`).join('')}
    </div>` : ''}

    <div class="dp-footer">Source: ${_esc(d.data_source || '—')} · Updated ${_fmtDate(d.updated_at)}</div>
  `;
}

function _renderLoiteringDetail(events) {
  if (!events || !events.length) return '';
  return events.map(ev => `
    <div class="dp-event-card dp-event-card--loiter">
      <div class="dp-event-header">
        <span class="dp-event-type">LOITERING</span>
        <span class="dp-event-dur">${ev.hours != null ? ev.hours.toFixed(1) + ' h' : '—'}</span>
      </div>
      <div class="dp-event-dates">${_fmtDate(ev.start)} → ${_fmtDate(ev.end)}</div>
      ${ev.lat != null ? `<div class="dp-event-pos">${ev.lat.toFixed(3)}°, ${ev.lon.toFixed(3)}°
        <button class="dp-fly-btn" data-lat="${ev.lat}" data-lon="${ev.lon}">↗ Map</button>
      </div>` : ''}
    </div>`).join('');
}

function _renderGapDetail(events) {
  if (!events || !events.length) return '';
  return events.map(ev => `
    <div class="dp-event-card dp-event-card--gap">
      <div class="dp-event-header">
        <span class="dp-event-type">AIS DARK</span>
        <span class="dp-event-dur">${ev.hours != null ? ev.hours.toFixed(1) + ' h' : '—'}</span>
      </div>
      <div class="dp-event-dates">${_fmtDate(ev.start)} → ${_fmtDate(ev.end)}</div>
    </div>`).join('');
}

function _renderEncounterDetail(events) {
  if (!events || !events.length) return '';
  return events.map(ev => `
    <div class="dp-event-card dp-event-card--enc">
      <div class="dp-event-header">
        <span class="dp-event-type">ENCOUNTER</span>
        <span class="dp-event-dur">${ev.hours != null ? ev.hours.toFixed(1) + ' h' : '—'}</span>
      </div>
      <div class="dp-event-dates">${_fmtDate(ev.start)} → ${_fmtDate(ev.end)}</div>
      ${ev.other_mmsi ? `<div class="dp-event-meta">With MMSI ${_esc(ev.other_mmsi)}${ev.other_flag ? ` (${_esc(ev.other_flag)})` : ''}</div>` : ''}
    </div>`).join('');
}

// Delegated handler for "↗ Map" buttons rendered inside the detail panel.
content().addEventListener('click', (e) => {
  const btn = e.target.closest('.dp-fly-btn');
  if (!btn) return;
  const lat = parseFloat(btn.dataset.lat);
  const lon = parseFloat(btn.dataset.lon);
  if (!isNaN(lat) && !isNaN(lon) && state.map) {
    state.map.flyTo([lat, lon], 8, { duration: 1.2 });
  }
});

function _renderGFWBlock(gfw, identity) {
  const hasAny = gfw && Object.values(gfw).some(v => v != null);
  if (!hasAny) return '';

  const pct = (gfw.fishing_hours != null && gfw.active_hours > 0)
    ? ((gfw.fishing_hours / gfw.active_hours) * 100).toFixed(0) + '%'
    : null;

  const flagMismatch = gfw.flag && identity?.flag && gfw.flag !== identity.flag;

  return `
    <div class="dp-section dp-section--gfw">
      <div class="dp-section-title">
        GFW REGISTRY
        <span class="dp-gfw-badge">fishing-vessels-v3</span>
      </div>
      <div class="dp-grid">
        ${_row('Gear type',   gfw.geartype)}
        ${_row('GFW flag',    gfw.flag ? (flagMismatch
          ? `<span class="dp-flag-warn">${_esc(gfw.flag)} ⚠ AIS flag mismatch</span>`
          : _esc(gfw.flag)) : null)}
        ${_row('Length',      gfw.length_m   ? `${gfw.length_m.toFixed(0)} m`  : null)}
        ${_row('Tonnage',     gfw.tonnage_gt ? `${gfw.tonnage_gt.toFixed(0)} GT` : null)}
        ${_row('Engine',      gfw.engine_kw  ? `${gfw.engine_kw.toFixed(0)} kW` : null)}
        ${_row('Fishing hrs', gfw.fishing_hours != null ? `${gfw.fishing_hours.toFixed(0)} h${pct ? ` (${pct} of active)` : ''}` : null)}
        ${_row('Active hrs',  gfw.active_hours  != null ? `${gfw.active_hours.toFixed(0)} h` : null)}
        ${_row('Self-reported fishing', gfw.self_reported_fishing != null ? (gfw.self_reported_fishing ? 'Yes' : 'No') : null)}
        ${_row('Registries',  gfw.registries)}
      </div>
    </div>`;
}

function _row(label, value) {
  if (value == null || value === '' || value === 'null') return '';
  return `<div class="dp-row-label">${label}</div><div class="dp-row-value">${_esc(String(value))}</div>`;
}

function _stat(n, label) {
  const val = n ?? 0;
  const hot = val > 0;
  return `<div class="dp-stat ${hot ? 'dp-stat--hot' : ''}">
    <div class="dp-stat-n">${val}</div>
    <div class="dp-stat-l">${label}</div>
  </div>`;
}

function _fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch { return '—'; }
}

function _esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
