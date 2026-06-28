import { state } from './state.js';
import { riskClass, flagEmoji } from './utils.js';
import { subtypeLabel, getVisibleVessels } from './vessels.js';
import {
  behaviorBadge, spatialBadges, trajectoryBadge, encounterBadge,
  gapBadge, spoofingBadge, weatherBadge, contextBadges,
} from './badges.js';

export function showSkeletons() {
  document.getElementById('cards').innerHTML = '<div class="skeleton"></div>'.repeat(4);
}

export function renderCards() {
  const c = document.getElementById('cards');
  c.innerHTML = '';
  const visible = getVisibleVessels();
  if (!visible.length) {
    c.innerHTML = '<div class="sub" style="padding:8px;color:var(--ss-w40)">No vessels match the current search or area.</div>';
    return;
  }
  visible.forEach((v, i) => {
    const cls = riskClass(v.risk_score);
    const badges = (v.reasons || []).map(r => `<span class="rule-badge">${r.label}</span>`).join('');
    const type = String(v.vessel_type || 'unknown').toLowerCase();
    const card = document.createElement('div');
    card.className = `card ${cls} fade-in`;
    card.style.animationDelay = `${Math.min(i * 30, 300)}ms`;
    card.onclick = () => focusVessel(v.mmsi);
    card.innerHTML = `
      <div class="card-row1">
        <span class="card-name">${v.name}</span>
        <span class="badge badge-${cls}">${Math.round(v.risk_score ?? 0)}</span>
      </div>
      <div class="card-row2">
        <span>${flagEmoji(v.flag)} ${v.flag}</span><span class="sep">/</span>
        <span>${v.speed_knots.toFixed(1)} kn</span><span class="sep">/</span>
        <span>${subtypeLabel(type)}</span>
      </div>
      ${badges ? `<div class="rule-badges">${badges}</div>` : ''}
      <div style="display:flex;gap:5px;margin-top:${badges ? '4' : '8'}px;flex-wrap:wrap">
        ${behaviorBadge(v)}${spatialBadges(v)}${trajectoryBadge(v)}
        ${encounterBadge(v)}${gapBadge(v)}${spoofingBadge(v)}
        ${weatherBadge(v)}${contextBadges(v)}
        ${v.in_protected_area ? `<span class="mpa-flag" style="margin:0">🛡 IN MPA</span>` : ''}
      </div>`;
    c.appendChild(card);
  });
}

export function focusVessel(mmsi) {
  const e = state.markersByMmsi[mmsi];
  if (!e) return;
  state.map.flyTo(e.marker.getLatLng(), Math.max(state.map.getZoom(), 9), { duration: 0.5 });
  e.marker.openPopup();
}

export function setStatus(src) {
  const p = document.getElementById('status-pill');
  const t = document.getElementById('status-text');
  if (src === 'gfw') { p.className = 'pill live'; t.textContent = 'LIVE'; }
  else               { p.className = 'pill syn';  t.textContent = 'SYNTHETIC'; }
}

export function showError(msg) {
  const b = document.getElementById('error-banner');
  b.innerHTML = msg;
  b.style.display = 'block';
}

export function hideError() {
  document.getElementById('error-banner').style.display = 'none';
}

export function setOverlay(show) {
  const o = document.getElementById('map-overlay');
  if (o) o.classList.toggle('hidden', !show);
}

export function setButtonLoading(on) {
  const b = document.getElementById('search-btn');
  if (on) { b.classList.add('loading'); b.textContent = '⏳ SCANNING...'; b.disabled = true; }
  else    { b.classList.remove('loading'); b.textContent = '🔍 SEARCH THIS AREA'; b.disabled = false; }
}

export function updateVesselCounts() {
  const inMpa = state.vesselsCache.filter(v => v.in_protected_area).length;
  document.getElementById('vessel-count').textContent = state.vesselsCache.length;
  document.getElementById('mpa-count').innerHTML = inMpa ? ` · <b>${inMpa} IN MPAs</b>` : '';
  document.getElementById('sidebar-sub').textContent =
    `Showing ${getVisibleVessels().length} of ${state.vesselsCache.length} vessels`;
}
