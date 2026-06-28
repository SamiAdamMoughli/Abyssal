// Central mutable state — imported by all modules that need shared references.
export const state = {
  map: null,
  markerLayer: null,
  ringLayer: null,
  gapLayer: null,
  mpaLayer: null,
  markersByMmsi: {},
  vesselsCache: [],
  currentParams: null,
  currentSource: 'gfw',
  ready: false,
  currentFilter: '',
  currentSort: 'score_desc',
  currentCatFilter: 'all',
  eventSource: null,
  // Alert feed
  alertsSocket: null,
  alertsCache: [],
  // Navigation
  activeView: 'ops',
  // Quick filters (ops sidebar)
  _quickFilters: [],
  // Timestamp of last vessel data fetch (for cache age telemetry)
  _lastFetch: null,
};
