import { riskClass, flagEmoji } from './utils.js';
import { categoryLabel, subtypeLabel } from './vessels.js';
import {
  behaviorBadge, spatialBadges, trajectoryBadge, encounterBadge,
  gapBadge, spoofingBadge, weatherBadge, contextBadges,
} from './badges.js';

export function popupHtml(v) {
  const cls = riskClass(v.risk_score);
  const type = String(v.vessel_type || 'unknown').toLowerCase();
  const rules = (v.reasons || []).map(r =>
    `<li><span class="b">▸ ${r.label} +${r.points}</span><br>${r.detail || ''}</li>`).join('');
  const mpaBadge = v.in_protected_area ? `<div class="pop-mpa">⚠ IN PROTECTED AREA</div>` : '';
  const behBadge = behaviorBadge(v);
  const extraBadges = [spatialBadges(v), trajectoryBadge(v), encounterBadge(v), gapBadge(v),
    spoofingBadge(v), weatherBadge(v), contextBadges(v)].filter(Boolean).join('');
  return `
    <div class="pop-name">${v.name}</div>
    <div class="pop-score ${cls}">${v.risk_score.toFixed(1)}</div>
    <div class="pop-mmsi">MMSI ${v.mmsi} · ${flagEmoji(v.flag)} ${v.flag} · ${v.speed_knots.toFixed(1)} KN</div>
    <div class="pop-type">${categoryLabel(type)} · ${subtypeLabel(type)}${behBadge ? ' &nbsp;' + behBadge : ''}</div>
    ${extraBadges ? `<div style="margin-top:5px;display:flex;gap:4px;flex-wrap:wrap">${extraBadges}</div>` : ''}
    ${mpaBadge}
    ${rules ? `<ul class="pop-rules">${rules}</ul>` : `<div class="pop-mmsi" style="margin-top:8px">No risk reasons.</div>`}
  `;
}
