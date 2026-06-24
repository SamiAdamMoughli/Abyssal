import { BEHAVIOR_LABELS, ENCOUNTER_LABELS, TRAJ_LABELS, GAP_TYPE_LABELS } from './constants.js';

export function spatialBadges(v) {
  const parts = [];
  if (v.border_skirting) {
    parts.push(`<span class="spatial-badge sp-skirting">GRENZ-SCHLEICHEN</span>`);
  }
  const nm = v.nearest_mpa_nm;
  if (!v.in_protected_area && nm >= 0 && nm <= 5) {
    const label = nm <= 2 ? `${nm.toFixed(1)} NM ZUR MPA` : `${nm.toFixed(1)} NM PUFFERZONE`;
    parts.push(`<span class="spatial-badge sp-proximity">${label}</span>`);
  }
  const h = v.time_in_zone_hours || 0;
  if (v.in_protected_area && h >= 2) {
    parts.push(`<span class="spatial-badge sp-timeinzone">IN MPA ${h.toFixed(0)}H</span>`);
  }
  return parts.join('');
}

export function behaviorBadge(v) {
  const b = (v.behavior_status || '').toLowerCase();
  if (!b || b === 'unknown') return '';
  const label = BEHAVIOR_LABELS[b] || b.toUpperCase();
  const conf = v.behavior_confidence > 0 ? ` ${Math.round(v.behavior_confidence * 100)}%` : '';
  return `<span class="behavior-badge beh-${b}">${label}${conf}</span>`;
}

export function encounterBadge(v) {
  const mc = (v.rendezvous_meeting_class || '').toLowerCase();
  if (!mc || mc === 'unknown' || mc === 'port_assist' || mc === 'vessel_to_vessel') return '';
  const dur = v.rendezvous_duration_hours > 0 ? ` ${v.rendezvous_duration_hours.toFixed(1)}H` : '';
  const label = ENCOUNTER_LABELS[mc] || mc.toUpperCase();
  return `<span class="encounter-badge enc-${mc}">${label}${dur}</span>`;
}

export function trajectoryBadge(v) {
  const tp = (v.trajectory_pattern || '').toLowerCase();
  if (!tp || tp === 'unknown' || tp === 'transit') return '';
  const conf = v.trajectory_confidence > 0 ? ` ${Math.round(v.trajectory_confidence * 100)}%` : '';
  const label = TRAJ_LABELS[tp] || tp.toUpperCase();
  return `<span class="traj-badge traj-${tp}">${label}${conf}</span>`;
}

export function weatherBadge(v) {
  const wave = v.wave_height_m != null ? v.wave_height_m : -1;
  const wind = v.wind_speed_kn  != null ? v.wind_speed_kn  : -1;
  if (wave < 5 && wind < 40) return '';
  const parts = [];
  if (wave >= 5)  parts.push(`Hs ${wave.toFixed(1)}m`);
  if (wind >= 40) parts.push(`${wind.toFixed(0)}kn`);
  return `<span class="ctx-badge ctx-storm">STURM ${parts.join(' / ')}</span>`;
}

export function contextBadges(v) {
  const parts = [];
  if (v.sst_at_thermal_front) {
    const sst = v.sst_celsius > -999 ? ` ${v.sst_celsius.toFixed(1)}°C` : '';
    parts.push(`<span class="ctx-badge ctx-front">TEMP-FRONT${sst}</span>`);
  }
  if (v.historical_risk_score >= 60) {
    parts.push(`<span class="ctx-badge ctx-hist">VORBELASTET ${v.historical_risk_score.toFixed(0)}</span>`);
  }
  if (v.verified_vessel_type && v.verified_vessel_type !== v.vessel_type) {
    parts.push(`<span class="ctx-badge ctx-typmm">TYP ≠ REGISTER</span>`);
  }
  return parts.join('');
}

export function gapBadge(v) {
  const gh = v.ais_gap_hours || 0;
  if (gh < 2) return '';
  const gt = (v.gap_type || 'unknown').toLowerCase();
  const label = GAP_TYPE_LABELS[gt] || 'GAP';
  const cls = gt === 'tactical_dark' ? 'gap-tactical'
    : gt === 'spoofing'          ? 'gap-spoofing'
    : gt === 'technical_failure' ? 'gap-technical'
    : 'gap-unknown';
  return `<span class="gap-badge ${cls}">${label} ${gh.toFixed(0)}H</span>`;
}

export function spoofingBadge(v) {
  if (!v.spoofing_flag) return '';
  const kn = v.spoofing_max_speed_kn || 0;
  const detail = kn >= 50 ? ` ${kn.toFixed(0)}KN` : '';
  return `<span class="gap-badge spoof-badge">SPOOFING${detail}</span>`;
}
