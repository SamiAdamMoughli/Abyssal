import { API_URL, REFRESH_INTERVAL_MS } from './config.js';
import { state } from './state.js';
import { syncMarkers } from './markers.js';
import { loadMPAs } from './mpa.js';
import { renderCards, setStatus, showError, hideError, setOverlay, setButtonLoading, showSkeletons, updateVesselCounts } from './ui.js';

export function buildQuery(params) {
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

export function startAutoRefresh() {
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  state.refreshTimer = setInterval(() => {
    if (state.currentParams) refreshVessels();
  }, REFRESH_INTERVAL_MS);
}

export async function refreshVessels() {
  if (state.isRefreshing) return;
  state.isRefreshing = true;
  try {
    const qs = buildQuery(state.currentParams);
    const res = await fetch(`${API_URL}/api/vessels?${qs}`);
    if (!res.ok) return;
    const vData = await res.json();
    if (!vData || !Array.isArray(vData.vessels)) return;
    state.vesselsCache = vData.vessels;
    syncMarkers(vData.vessels);
    renderCards();
    updateVesselCounts();
  } catch (err) {
    console.warn('Auto-refresh failed:', err.message || err);
  } finally {
    state.isRefreshing = false;
  }
}

export function currentDates() {
  return {
    start: document.getElementById('date-from').value,
    end:   document.getElementById('date-to').value,
  };
}

export async function loadData(params) {
  setButtonLoading(true);
  state.markerLayer.eachLayer(l => l.setOpacity && l.setOpacity(0.3));
  showSkeletons();

  const payload = params || (() => {
    const b = state.map.getBounds();
    const d = currentDates();
    return {
      min_lat: b.getSouth(), max_lat: b.getNorth(),
      min_lon: b.getWest(), max_lon: b.getEast(),
      start: d.start, end: d.end,
    };
  })();
  state.currentParams = payload;

  try {
    const health = await fetch(`${API_URL}/`).then(r => r.ok ? r.json() : null).catch(() => null);
    if (health?.default_data_source) {
      state.currentSource = health.default_data_source;
      setStatus(state.currentSource);
    }

    const qs = buildQuery(state.currentParams);
    const vRes = await fetch(`${API_URL}/api/vessels?${qs}`);
    if (!vRes.ok) {
      const body = await vRes.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${vRes.status}`);
    }
    const vData = await vRes.json();
    if (!health && vData.source) {
      state.currentSource = vData.source;
      setStatus(state.currentSource);
    }

    state.markerLayer.clearLayers();
    state.ringLayer.clearLayers();
    Object.keys(state.markersByMmsi).forEach(k => delete state.markersByMmsi[k]);
    syncMarkers(vData.vessels);

    state.vesselsCache = vData.vessels;
    renderCards();
    updateVesselCounts();
    loadMPAs(state.currentParams);

    if (params) {
      const h = `#bbox=${params.min_lon.toFixed(3)},${params.min_lat.toFixed(3)},` +
        `${params.max_lon.toFixed(3)},${params.max_lat.toFixed(3)}` +
        `&start=${params.start}&end=${params.end}`;
      history.replaceState(null, '', h);
    }
    hideError();
    setOverlay(false);
    startAutoRefresh();
  } catch (err) {
    setOverlay(false);
    showError(`⚠ ${err.message.includes('large') ? err.message : 'BACKEND OFFLINE — Run: '}` +
      (err.message.includes('large') ? '' : `<code>uvicorn app.main:app --reload</code>`));
  } finally {
    setButtonLoading(false);
  }
}
