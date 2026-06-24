import L from 'leaflet';
import { css } from './utils.js';
import { vesselCategoryId } from './vessels.js';

export function riskFill(score) {
  if (score <= 30) return { fill: '#22c55e', glow: 'rgba(34,197,94,0.65)' };
  if (score <= 70) return { fill: '#ffaa00', glow: 'rgba(255,170,0,0.65)' };
  return { fill: css('--ss-orange'), glow: css('--ss-orange-glow') };
}

export function vesselShape(type, fill, edgeStroke) {
  const cat = vesselCategoryId(type);
  switch (cat) {
    case 'tanker':
      return { inner: `<rect x="1" y="4" width="32" height="12" rx="6" fill="${fill}" stroke="${edgeStroke}" stroke-width="1"/>`, w: 34, h: 20 };
    case 'commercial':
      return { inner: `<polygon points="1,4 5,1 29,1 33,4 33,14 29,17 5,17 1,14" fill="${fill}" stroke="${edgeStroke}" stroke-width="1"/>`, w: 34, h: 18 };
    case 'fishing':
      return { inner: `<polygon points="11,1 21,11 11,21 1,11" fill="${fill}" stroke="${edgeStroke}" stroke-width="1"/>`, w: 22, h: 22 };
    case 'enforcement':
      return { inner: `<polygon points="11,2 21,20 1,20" fill="${fill}" stroke="${edgeStroke}" stroke-width="1"/>`, w: 22, h: 22 };
    case 'support':
      return { inner: `<rect x="1" y="3" width="26" height="12" rx="4" fill="${fill}" stroke="${edgeStroke}" stroke-width="1"/>`, w: 28, h: 18 };
    default:
      return { inner: `<circle cx="10" cy="10" r="9" fill="${fill}" stroke="${edgeStroke}" stroke-width="1"/>`, w: 20, h: 20 };
  }
}

export function getVesselSvgIcon(v) {
  const score = v.risk_score || 0;
  const type = String(v.vessel_type || 'unknown').toLowerCase();
  const { fill, glow } = riskFill(score);
  const edgeStroke = 'rgba(255,255,255,0.4)';
  const { inner, w, h } = vesselShape(type, fill, edgeStroke);
  const riskCls = score > 70 ? 'vessel-hi' : score > 30 ? 'vessel-mid' : 'vessel-lo';
  const spoofCls = v.spoofing_flag ? ' vessel-spoofing' : '';
  const svgHtml = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" ` +
    `style="filter:drop-shadow(0 0 4px ${glow});overflow:visible">${inner}</svg>`;
  return L.divIcon({
    className: `vessel-icon ${riskCls}${spoofCls}`,
    html: svgHtml,
    iconSize: [w, h],
    iconAnchor: [w / 2, h / 2],
  });
}
