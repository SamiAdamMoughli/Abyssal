/**
 * Ray-casting point-in-polygon test.
 * polygon: array of [lat, lon] pairs (Leaflet convention).
 */
export function pointInPolygon(lat, lon, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [yi, xi] = polygon[i];
    const [yj, xj] = polygon[j];
    if ((yi > lat) !== (yj > lat) &&
        lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

export function nmToKm(nm) {
  return nm * 1.852;
}

export function calculateBbox(centerLat, centerLon, radiusNm) {
  const radiusKm = nmToKm(radiusNm);
  const latDelta = radiusKm / 111.0;
  const lonDelta = radiusKm / (111.0 * Math.cos(centerLat * Math.PI / 180));
  return {
    min_lat: centerLat - latDelta,
    max_lat: centerLat + latDelta,
    min_lon: centerLon - lonDelta,
    max_lon: centerLon + lonDelta,
    center_lat: centerLat,
    center_lon: centerLon,
    radius_nm: radiusNm,
  };
}

export function getCurrentLocation() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('Geolocation not supported by this browser'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      pos => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy }),
      err => reject(new Error(`Geolocation failed: ${err.message}`)),
      { timeout: 10000, enableHighAccuracy: false },
    );
  });
}
