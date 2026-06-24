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
