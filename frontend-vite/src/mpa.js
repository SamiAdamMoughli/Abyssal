import L from 'leaflet';
import { API_URL } from './config.js';
import { state } from './state.js';
import { css } from './utils.js';

function mpaLoading(on) {
  document.getElementById('mpa-loading').classList.toggle('show', on);
}

export async function loadMPAs(params) {
  mpaLoading(true);
  try {
    const qs = buildQuery(params);
    const res = await fetch(`${API_URL}/api/protected-areas?${qs}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
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
