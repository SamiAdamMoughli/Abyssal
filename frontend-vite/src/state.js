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
};
