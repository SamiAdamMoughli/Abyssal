import { VESSEL_CATEGORIES, CATEGORY_LABELS, SUBTYPE_LABELS } from './constants.js';
import { state } from './state.js';
import { pointInPolygon } from './geo.js';
import { activeRegion } from './phase.js';

export function vesselCategoryId(type) {
  for (const [catId, types] of Object.entries(VESSEL_CATEGORIES)) {
    if (types.has(type)) return catId;
  }
  return 'unknown';
}

export function subtypeLabel(type) {
  return SUBTYPE_LABELS[type] || 'Unknown';
}

export function categoryLabel(type) {
  return CATEGORY_LABELS[vesselCategoryId(type)];
}

export function matchesSearch(v, term) {
  if (!term) return true;
  const q = term.toLowerCase();
  return [v.name, String(v.mmsi), v.flag, v.top_reason_label, (v.reasons || []).map(r => r.label).join(' ')]
    .some(val => val && val.toLowerCase().includes(q));
}

export function matchesCategory(v) {
  if (state.currentCatFilter === 'all') return true;
  return vesselCategoryId(String(v.vessel_type || 'unknown').toLowerCase()) === state.currentCatFilter;
}

export function sortVessels(a, b) {
  const s = state.currentSort;
  const scoreA = a.risk_score ?? a.score ?? 0;
  const scoreB = b.risk_score ?? b.score ?? 0;
  if (s === 'score_desc') return scoreB - scoreA;
  if (s === 'score_asc')  return scoreA - scoreB;
  if (s === 'name_asc')   return String(a.name || '').localeCompare(String(b.name || ''), undefined, { sensitivity: 'base' });
  if (s === 'name_desc')  return String(b.name || '').localeCompare(String(a.name || ''), undefined, { sensitivity: 'base' });
  if (s === 'flag_asc') {
    const cmp = String(a.flag || '').localeCompare(String(b.flag || ''), undefined, { sensitivity: 'base' });
    return cmp || scoreB - scoreA;
  }
  if (s === 'mpa_first') {
    return (a.in_protected_area ? 0 : 1) - (b.in_protected_area ? 0 : 1) || scoreB - scoreA;
  }
  return scoreB - scoreA;
}

export function matchesQuickFilters(v) {
  const qf = state._quickFilters || [];
  if (!qf.length) return true;
  return qf.every(f => {
    if (f === 'fishing_only') return String(v.vessel_type || '').toLowerCase().includes('fish');
    if (f === 'mpa_only')    return v.in_protected_area;
    if (f === 'dark_only')   return v.dark_vessel || v.gap_events_90d > 0;
    if (f === 'hi_risk')     return (v.risk_score ?? 0) >= 40;
    return true;
  });
}

export function getVisibleVessels() {
  const region = activeRegion();
  return state.vesselsCache
    .filter(v => {
      if (region?.polygon && v.lat != null && v.lon != null) {
        if (!pointInPolygon(v.lat, v.lon, region.polygon)) return false;
      }
      return matchesSearch(v, state.currentFilter) && matchesCategory(v) && matchesQuickFilters(v);
    })
    .slice()
    .sort(sortVessels);
}
