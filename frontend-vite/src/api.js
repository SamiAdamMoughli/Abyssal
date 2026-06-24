import { API_URL } from './config.js';
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

// ==================================================================
// SSE STREAM  (replaces setInterval polling)
// ==================================================================
export function closeStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

export function openStream(params) {
  closeStream();
  const qs = buildQuery(params);
  const es = new EventSource(`${API_URL}/api/vessels/stream?${qs}`);
  state.eventSource = es;

  es.onmessage = (e) => {
    try {
      const vData = JSON.parse(e.data);
      if (!Array.isArray(vData?.vessels)) return;
      state.vesselsCache = vData.vessels;
      syncMarkers(vData.vessels);
      renderCards();
      updateVesselCounts();
    } catch (err) {
      console.warn('SSE parse error:', err);
    }
  };

  es.addEventListener('error', () => {
    // EventSource auto-reconnects on network errors — no manual retry needed.
    if (es.readyState === EventSource.CLOSED) {
      console.warn('SSE stream closed by server');
    }
  });
}

// ==================================================================
// DATE HELPER
// ==================================================================
export function currentDates() {
  return {
    start: document.getElementById('date-from').value,
    end:   document.getElementById('date-to').value,
  };
}

// ==================================================================
// INITIAL FULL LOAD  (one-shot fetch, then hands off to SSE stream)
// ==================================================================
export async function loadData(params) {
  setButtonLoading(true);
  state.markerLayer.eachLayer(l => l.setOpacity && l.setOpacity(0.3));
  const skeletonTimer = setTimeout(showSkeletons, 200);

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
    openStream(state.currentParams);
  } catch (err) {
    setOverlay(false);
    showError(`⚠ ${err.message.includes('large') ? err.message : 'BACKEND OFFLINE — Run: '}` +
      (err.message.includes('large') ? '' : `<code>uvicorn app.main:app --reload</code>`));
  } finally {
    clearTimeout(skeletonTimer);
    setButtonLoading(false);
  }
}
