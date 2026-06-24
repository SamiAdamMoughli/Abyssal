import { state } from './state.js';
import { getCurrentLocation, calculateBbox } from './geo.js';
import { loadData, currentDates } from './api.js';

function showOnboardingError(msg) {
  const form = document.getElementById('onboarding-form');
  let el = form.querySelector('.onboarding-error');
  if (!el) {
    el = document.createElement('div');
    el.className = 'onboarding-error';
    el.style.cssText = 'color:var(--ss-red);font-size:12px;margin-top:12px;text-align:center;';
    form.appendChild(el);
  }
  el.textContent = msg;
}

export function hideOnboarding() {
  document.getElementById('onboarding-screen').classList.add('hidden');
}

export function setupOnboarding() {
  const form         = document.getElementById('onboarding-form');
  const btnLocation  = document.getElementById('btn-use-location');
  const btnInit      = document.getElementById('btn-initialize');
  const latInput     = document.getElementById('onboarding-lat');
  const lonInput     = document.getElementById('onboarding-lon');
  const radiusInput  = document.getElementById('onboarding-radius');

  btnLocation.addEventListener('click', async () => {
    btnLocation.disabled = true;
    btnLocation.textContent = '⏳ DETECTING…';
    try {
      const loc = await getCurrentLocation();
      latInput.value = loc.lat.toFixed(4);
      lonInput.value = loc.lon.toFixed(4);
      showOnboardingError('');
      btnLocation.textContent = '✓ LOCATION FOUND';
    } catch (err) {
      showOnboardingError(err.message);
      btnLocation.textContent = '📍 USE CURRENT LOCATION';
    } finally {
      btnLocation.disabled = false;
    }
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const lat    = parseFloat(latInput.value);
    const lon    = parseFloat(lonInput.value);
    const radius = parseFloat(radiusInput.value);

    if (isNaN(lat) || isNaN(lon) || isNaN(radius)) {
      showOnboardingError('Please enter valid latitude, longitude, and radius.');
      return;
    }
    if (radius <= 0 || radius > 500) {
      showOnboardingError('Radius must be between 1 and 500 nautical miles.');
      return;
    }

    try {
      btnInit.disabled = true;
      btnInit.textContent = '⏳ INITIALIZING…';
      showOnboardingError('');

      const bbox  = calculateBbox(lat, lon, radius);
      const dates = currentDates();
      await loadData({
        min_lat: bbox.min_lat, max_lat: bbox.max_lat,
        min_lon: bbox.min_lon, max_lon: bbox.max_lon,
        start: dates.start, end: dates.end,
      });

      const zoomLevel = Math.max(7, 20 - Math.log2(radius / 10));
      state.map.flyTo([lat, lon], zoomLevel, { duration: 0.8 });
      hideOnboarding();
      state.ready = true;
    } catch (err) {
      showOnboardingError(err.message || 'Failed to initialize. Please try again.');
      btnInit.textContent = 'INITIALIZE SCOPE';
      btnInit.disabled = false;
    }
  });
}
