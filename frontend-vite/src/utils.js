import { ISO3to2 } from './constants.js';

export function flagEmoji(iso3) {
  const c = ISO3to2[(iso3 || '').toUpperCase()];
  if (!c) return '🏴';
  return c.replace(/./g, ch => String.fromCodePoint(127397 + ch.charCodeAt(0)));
}

export function riskClass(s) {
  return s > 60 ? 'hi' : s >= 30 ? 'mid' : 'lo';
}

export function fmtDate(d) {
  return d.toISOString().slice(0, 10);
}

export function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
