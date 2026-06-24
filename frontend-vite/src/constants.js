export const ISO3to2 = {
  ECU: 'EC', CHN: 'CN', PAN: 'PA', USA: 'US', POL: 'PL', FRA: 'FR', GBR: 'GB', VEN: 'VE',
  NGA: 'NG', GNQ: 'GQ', TGO: 'TG', ESP: 'ES', KOR: 'KR', TWN: 'TW', JPN: 'JP', RUS: 'RU',
  PRT: 'PT', ITA: 'IT', MEX: 'MX', PER: 'PE', CHL: 'CL', COL: 'CO', IDN: 'ID', VNM: 'VN',
  BLZ: 'BZ', COM: 'KM', STP: 'ST', LBR: 'LR', MHL: 'MH', HKG: 'HK', ATG: 'AG', MLT: 'MT', SEN: 'SN',
};

export const VESSEL_CATEGORIES = {
  tanker:      new Set(['tanker']),
  commercial:  new Set(['container', 'bulk', 'ro_ro', 'cargo']),
  fishing:     new Set(['trawler', 'longliner', 'purse_seiner', 'reefer', 'fishing', 'whaling']),
  enforcement: new Set(['coast_guard', 'naval', 'ngo', 'patrol', 'enforcement']),
  support:     new Set(['research', 'tug', 'supply', 'icebreaker', 'support']),
};

export const CATEGORY_LABELS = {
  tanker:      'Tanker',
  commercial:  'Commercial Fleet',
  fishing:     'Extractive & Fishing',
  enforcement: 'Enforcement & State',
  support:     'Support & Special',
  unknown:     'Unknown',
};

export const SUBTYPE_LABELS = {
  container: 'Container Ship', bulk: 'Bulk Carrier',
  tanker: 'Tanker', ro_ro: 'Ro-Ro Carrier', cargo: 'Cargo Ship',
  trawler: 'Trawler', longliner: 'Longliner', purse_seiner: 'Purse Seiner',
  reefer: 'Factory / Reefer', fishing: 'Fishing Vessel', whaling: 'Whaling Vessel',
  coast_guard: 'Coast Guard', naval: 'Naval Warship',
  ngo: 'NGO Vessel', patrol: 'Patrol Vessel', enforcement: 'Enforcement',
  research: 'Research Vessel', tug: 'Tugboat',
  supply: 'Supply Ship', icebreaker: 'Icebreaker', support: 'Support Vessel',
};

export const BEHAVIOR_LABELS = {
  trawling:  'TRAWLING',
  loitering: 'LOITERING',
  transit:   'TRANSIT',
  anchored:  'ANCHORED',
};

export const ENCOUNTER_LABELS = {
  transshipment: 'TRANSSHIPMENT',
  bunkering:     'BUNKERING',
  fishing_coord: 'FISCHEREI-KOORDINATION',
};

export const TRAJ_LABELS = {
  grid:    'GITTER-TRAJEKT.',
  holding: 'WARTE-MUSTER',
  spiral:  'SPIRAL-MUSTER',
  anomaly: 'TRAJEKT.-ANOMALIE',
  transit: '',
};

export const GAP_TYPE_LABELS = {
  tactical_dark:     'TAKT. DUNKEL',
  technical_failure: 'AIS-AUSFALL',
  spoofing:          'POS.-SPRUNG',
  unknown:           'GAP',
};
