import L from 'leaflet';
import { state } from './state.js';
import { css, riskClass } from './utils.js';
import { getVesselSvgIcon } from './icons.js';
import { openDetail } from './detail.js';

function radiusFor(v) {
  const c = riskClass(v.risk_score);
  if (c === 'hi')  return 8 + v.risk_score / 12;
  if (c === 'mid') return 6 + v.risk_score / 15;
  return 5;
}

function gapCircleRadius(v) {
  const sog = Math.max(v.speed_knots || 0, 1.0);
  return (v.ais_gap_hours || 0) * sog * 1852;
}

export function createMarkerForVessel(v) {
  const marker = L.marker([v.lat, v.lon], { icon: getVesselSvgIcon(v) });
  marker.on('click', () => openDetail(v.mmsi));
  let ring = null;
  let gapCircle = null;
  if (v.in_protected_area) {
    ring = L.circleMarker([v.lat, v.lon], {
      radius: radiusFor(v) * 3,
      color: css('--ss-orange'), weight: 1, fillOpacity: 0, dashArray: '3 3',
    }).addTo(state.ringLayer);
  }
  if ((v.ais_gap_hours || 0) >= 2) {
    gapCircle = L.circle([v.lat, v.lon], {
      radius: gapCircleRadius(v),
      color: v.gap_type === 'tactical_dark' ? css('--ss-red') : css('--ss-amber'),
      weight: 1.5, fillOpacity: 0.04, dashArray: '6 8', className: 'gap-uncertainty',
    }).addTo(state.gapLayer);
  }
  marker.addTo(state.markerLayer);
  return { marker, ring, gapCircle, data: v };
}

export function updateMarkerForVessel(v, entry) {
  entry.marker.setLatLng([v.lat, v.lon]);
  entry.marker.setIcon(getVesselSvgIcon(v));
  entry.marker.off('click').on('click', () => openDetail(v.mmsi));

  if (v.in_protected_area) {
    if (entry.ring) {
      entry.ring.setLatLng([v.lat, v.lon]);
    } else {
      entry.ring = L.circleMarker([v.lat, v.lon], {
        radius: radiusFor(v) * 3,
        color: css('--ss-orange'), weight: 1, fillOpacity: 0, dashArray: '3 3',
      }).addTo(state.ringLayer);
    }
  } else if (entry.ring) {
    state.ringLayer.removeLayer(entry.ring);
    entry.ring = null;
  }

  const hasGap = (v.ais_gap_hours || 0) >= 2;
  const gapColor = v.gap_type === 'tactical_dark' ? css('--ss-red') : css('--ss-amber');
  if (hasGap) {
    if (entry.gapCircle) {
      entry.gapCircle.setLatLng([v.lat, v.lon]);
      entry.gapCircle.setRadius(gapCircleRadius(v));
      entry.gapCircle.setStyle({ color: gapColor });
    } else {
      entry.gapCircle = L.circle([v.lat, v.lon], {
        radius: gapCircleRadius(v),
        color: gapColor, weight: 1.5, fillOpacity: 0.04, dashArray: '6 8', className: 'gap-uncertainty',
      }).addTo(state.gapLayer);
    }
  } else if (entry.gapCircle) {
    state.gapLayer.removeLayer(entry.gapCircle);
    entry.gapCircle = null;
  }
  entry.data = v;
}

export function syncMarkers(vessels) {
  const nextMmsi = new Set(vessels.map(v => v.mmsi));
  vessels.forEach(v => {
    const existing = state.markersByMmsi[v.mmsi];
    if (existing) {
      updateMarkerForVessel(v, existing);
    } else {
      state.markersByMmsi[v.mmsi] = createMarkerForVessel(v);
    }
  });
  Object.keys(state.markersByMmsi).forEach(mmsi => {
    if (!nextMmsi.has(mmsi)) {
      const e = state.markersByMmsi[mmsi];
      state.markerLayer.removeLayer(e.marker);
      if (e.ring)      state.ringLayer.removeLayer(e.ring);
      if (e.gapCircle) state.gapLayer.removeLayer(e.gapCircle);
      delete state.markersByMmsi[mmsi];
    }
  });
}

export function clearMarkers() {
  Object.keys(state.markersByMmsi).forEach(mmsi => {
    const e = state.markersByMmsi[mmsi];
    state.markerLayer.removeLayer(e.marker);
    if (e.ring)      state.ringLayer.removeLayer(e.ring);
    if (e.gapCircle) state.gapLayer.removeLayer(e.gapCircle);
    delete state.markersByMmsi[mmsi];
  });
}
