import { VESSEL_CATEGORIES, CATEGORY_LABELS, SUBTYPE_LABELS } from './constants.js';
import { state } from './state.js';

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
  if (s === 'score_desc') return b.score - a.score;
  if (s === 'score_asc')  return a.score - b.score;
  if (s === 'name_asc')   return String(a.name || '').localeCompare(String(b.name || ''), undefined, { sensitivity: 'base' });
  if (s === 'name_desc')  return String(b.name || '').localeCompare(String(a.name || ''), undefined, { sensitivity: 'base' });
  if (s === 'flag_asc') {
    const cmp = String(a.flag || '').localeCompare(String(b.flag || ''), undefined, { sensitivity: 'base' });
    return cmp || b.score - a.score;
  }
  if (s === 'mpa_first') {
    return (a.in_protected_area ? 0 : 1) - (b.in_protected_area ? 0 : 1) || b.score - a.score;
  }
  return b.score - a.score;
}

export function getVisibleVessels() {
  return state.vesselsCache
    .filter(v => matchesSearch(v, state.currentFilter) && matchesCategory(v))
    .slice()
    .sort(sortVessels);
}
