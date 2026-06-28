/**
 * fleet.js — Intelligence & Fleet Registry view.
 *
 * Left pane: dense, searchable/sortable data table from state.vesselsCache.
 * Right pane: full vessel dossier rendered via renderVesselProfile (shared
 *             with the ops-view detail drawer).
 *
 * Initialised lazily on first 'viewchange' → fleet event so it never runs
 * when the operator stays on the ops screen.
 */

import { state } from '../state.js';
import { riskClass, flagEmoji } from '../utils.js';
import { renderVesselProfile, fetchVesselProfile } from '../detail.js';

let _activeRow = null;

// ---------------------------------------------------------------------------
// Public init — called once from main.js
// ---------------------------------------------------------------------------

export function initFleet() {
  document.addEventListener('viewchange', ({ detail }) => {
    if (detail.view !== 'fleet') return;
    _renderTable();
    _wireControls();
  });
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

function _renderTable() {
  const vessels = _filtered(_sorted(state.vesselsCache));
  const sub = document.getElementById('fleet-sub');
  if (sub) sub.textContent = `${vessels.length} vessels · click a row to open dossier`;

  const tbody = document.getElementById('fleet-tbody');
  if (!tbody) return;

  if (!vessels.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="fleet-empty">No vessels match current filters.</td></tr>';
    return;
  }

  tbody.innerHTML = vessels.map(v => {
    const cls = riskClass(v.risk_score);
    const statusBadges = _statusBadges(v);
    return `
      <tr data-mmsi="${v.mmsi}" class="${_activeRow === v.mmsi ? 'selected' : ''}">
        <td>
          <div class="ft-name">${_esc(v.name)}</div>
          <div class="ft-mmsi">MMSI ${v.mmsi}${v.imo ? ` · IMO ${v.imo}` : ''}</div>
        </td>
        <td class="ft-mmsi">${v.mmsi}</td>
        <td>${flagEmoji(v.flag)} ${_esc(v.flag || '—')}</td>
        <td style="font-size:11px;color:var(--ss-w60)">${_esc(v.vessel_type || '—')}</td>
        <td class="ft-score ${cls}">${(v.risk_score ?? 0).toFixed(1)}</td>
        <td>${statusBadges || '<span style="color:var(--ss-w40);font-size:10px">CLEAN</span>'}</td>
      </tr>`;
  }).join('');

  tbody.querySelectorAll('tr[data-mmsi]').forEach(row => {
    row.addEventListener('click', () => _selectVessel(row.dataset.mmsi, row));
  });
}

function _statusBadges(v) {
  const parts = [];
  if (v.in_protected_area) parts.push('<span class="ft-status-badge mpa">IN MPA</span>');
  if (v.iuu_blacklisted)   parts.push('<span class="ft-status-badge iuu">IUU</span>');
  if (v.dark_vessel)       parts.push('<span class="ft-status-badge dark">DARK</span>');
  return parts.join(' ');
}

// ---------------------------------------------------------------------------
// Dossier loading
// ---------------------------------------------------------------------------

async function _selectVessel(mmsi, row) {
  // Highlight selected row
  document.querySelectorAll('#fleet-tbody tr').forEach(r => r.classList.remove('selected'));
  row.classList.add('selected');
  _activeRow = mmsi;

  const dossier = document.getElementById('fleet-dossier');
  if (!dossier) return;

  dossier.innerHTML = '<div style="padding:40px;color:var(--ss-w40);font-size:12px;text-align:center">◈ Loading vessel profile…</div>';

  try {
    const data = await fetchVesselProfile(mmsi);
    dossier.innerHTML = renderVesselProfile(data);
    // Wire the close button to deselect rather than close the drawer
    const closeBtn = dossier.querySelector('.dp-close');
    if (closeBtn) {
      closeBtn.onclick = () => {
        dossier.innerHTML = `<div class="dossier-placeholder">
          <div class="dp-placeholder-icon">⬡</div>
          <div class="dp-placeholder-text">Select a vessel to view its full intelligence dossier</div>
        </div>`;
        document.querySelectorAll('#fleet-tbody tr').forEach(r => r.classList.remove('selected'));
        _activeRow = null;
      };
    }
  } catch (e) {
    dossier.innerHTML = `<div style="padding:24px;color:var(--ss-red);font-size:12px">Failed to load profile: ${_esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Sort + filter helpers
// ---------------------------------------------------------------------------

function _sorted(vessels) {
  const key = document.getElementById('fleet-sort')?.value ?? 'score_desc';
  return [...vessels].sort((a, b) => {
    switch (key) {
      case 'score_desc': return (b.risk_score ?? 0) - (a.risk_score ?? 0);
      case 'score_asc':  return (a.risk_score ?? 0) - (b.risk_score ?? 0);
      case 'name_asc':   return (a.name ?? '').localeCompare(b.name ?? '');
      case 'flag_asc':   return (a.flag ?? '').localeCompare(b.flag ?? '');
      case 'mpa_first':  return (b.in_protected_area ? 1 : 0) - (a.in_protected_area ? 1 : 0);
      default: return 0;
    }
  });
}

function _filtered(vessels) {
  const q    = (document.getElementById('fleet-search')?.value ?? '').toLowerCase().trim();
  const type = document.getElementById('fleet-filter-type')?.value ?? 'all';

  return vessels.filter(v => {
    if (type !== 'all') {
      const vtype = String(v.vessel_type || '').toLowerCase();
      if (!vtype.includes(type)) return false;
    }
    if (q) {
      const haystack = `${v.name} ${v.mmsi} ${v.imo} ${v.flag}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
}

// ---------------------------------------------------------------------------
// Control wiring — only once per view activation
// ---------------------------------------------------------------------------

let _wired = false;
function _wireControls() {
  if (_wired) return;
  _wired = true;

  ['fleet-search', 'fleet-sort', 'fleet-filter-type'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', _renderTable);
    document.getElementById(id)?.addEventListener('change', _renderTable);
  });
}

function _esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
