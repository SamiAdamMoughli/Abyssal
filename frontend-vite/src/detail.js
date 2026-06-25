/**
 * Vessel detail panel — slides in from the right on marker click.
 * Fetches /api/vessels/{mmsi}/detail and renders the full fused profile.
 */

import { API_URL } from './config.js';

const panel   = () => document.getElementById('detail-panel');
const content = () => document.getElementById('detail-content');

export function openDetail(mmsi) {
  const p = panel();
  p.classList.add('open');
  content().innerHTML = '<div class="detail-loading">◈ Loading vessel profile…</div>';
  _fetchAndRender(mmsi);
}

export function closeDetail() {
  panel().classList.remove('open');
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
}

// -----------------------------------------------------------------------
// Renderers
// -----------------------------------------------------------------------

function _renderDetail(d) {
  const id   = d.identity   || {};
  const nav  = d.live_navigation || {};
  const hist = d.historical_anomalies || {};
  const score = d.calculated_risk_score ?? null;

  const riskCls = score === null ? 'unknown'
                : score >= 70   ? 'hi'
                : score >= 35   ? 'mid'
                :                 'lo';

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
