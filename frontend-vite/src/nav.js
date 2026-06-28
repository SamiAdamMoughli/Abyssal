/**
 * nav.js — Left-rail navigation + view switching.
 *
 * Each view is a full-screen <div class="view">. Switching sets .view-active
 * on the target and removes it from all others. The Leaflet map lives inside
 * #view-ops; we call invalidateSize() on re-entry so tiles repaint correctly.
 *
 * Emits a 'viewchange' CustomEvent on document so view modules can lazy-init
 * their content on first activation.
 */

import { state } from './state.js';

const VIEWS = ['ops', 'fleet', 'analytics', 'governance'];

const _initialized = new Set();

export function initNav() {
  document.querySelectorAll('.nav-btn[data-view]').forEach(btn => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });

  // Mark ops as already initialized (it boots with the page)
  _initialized.add('ops');
}

export function switchView(name) {
  if (!VIEWS.includes(name)) return;
  if (name === state.activeView) return;

  VIEWS.forEach(v => {
    const el  = document.getElementById(`view-${v}`);
    const btn = document.querySelector(`.nav-btn[data-view="${v}"]`);
    el?.classList.toggle('view-active', v === name);
    btn?.classList.toggle('active', v === name);
  });

  const prev = state.activeView;
  state.activeView = name;

  // Leaflet must recalculate its container size after display change
  if (name === 'ops' && state.map) {
    requestAnimationFrame(() => state.map.invalidateSize());
  }

  document.dispatchEvent(new CustomEvent('viewchange', {
    detail: { view: name, prev, firstTime: !_initialized.has(name) },
  }));

  _initialized.add(name);
}

export function currentView() {
  return state.activeView;
}
