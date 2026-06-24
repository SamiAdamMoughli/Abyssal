import L from 'leaflet';
import { API_URL } from './config.js';
import { state } from './state.js';
import { css } from './utils.js';

const MPA_TTL_MS = 24 * 60 * 60 * 1000; // 24h

function cacheKey(params) {
  // Round to 1 decimal place so minor panning reuses the same slot.
  const r = (n) => Math.round(n * 10) / 10;
  return `mpa:${r(params.min_lat)},${r(params.max_lat)},${r(params.min_lon)},${r(params.max_lon)}`;
}

function readCache(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const { ts, data } = JSON.parse(raw);
    if (Date.now() - ts > MPA_TTL_MS) { localStorage.removeItem(key); return null; }
    return data;
  } catch { return null; }
}

function writeCache(key, data) {
  try {
    localStorage.setItem(key, JSON.stringify({ ts: Date.now(), data }));
  } catch {
    // localStorage full — silently skip, the layer still renders from network data.
  }
}

function mpaLoading(on) {
  document.getElementById('mpa-loading').classList.toggle('show', on);
}

export async function loadMPAs(params) {
  mpaLoading(true);
  try {
    const key = cacheKey(params);
    let data = readCache(key);
    if (!data) {
      const qs = buildQuery(params);
      const res = await fetch(`${API_URL}/api/protected-areas?${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      writeCache(key, data);
    }
    state.mpaLayer.clearLayers();
    L.geoJSON(data, {
      style: {
        color: css('--ss-cyan'), weight: 1.5, opacity: 0.8,
        fillColor: css('--ss-cyan'), fillOpacity: 0.06, dashArray: '4 4',
      },
      onEachFeature(feature, layer) {
        const p = feature.properties || {};
        layer.bindPopup(`
          <div class="mpa-popup">
            <div class="mpa-name">${p.name || 'Protected Area'}</div>
            <div class="mpa-cat">IUCN Category: ${p.iucn_cat || '?'}</div>
            <div class="mpa-area">${p.area_km2 != null ? p.area_km2.toLocaleString() : '?'} km²</div>
          </div>`);
      },
    }).addTo(state.mpaLayer);
  } catch (e) {
    console.warn('MPA layer failed:', e.message);
  } finally {
    mpaLoading(false);
  }
}

// Imported here to avoid circular dep with api.js
function buildQuery(params) {
  const q = new URLSearchParams({ source: state.currentSource });
  if (params) {
    q.set('min_lat', params.min_lat.toFixed(4));
    q.set('max_lat', params.max_lat.toFixed(4));
    q.set('min_lon', params.min_lon.toFixed(4));
    q.set('max_lon', params.max_lon.toFixed(4));
    if (params.start) q.set('start_date', params.start);
    if (params.end)   q.set('end_date',   params.end);
  }
  return q.toString();
}
