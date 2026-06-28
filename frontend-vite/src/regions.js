export const REGIONS = [
  {
    id: 'baltic_sea',
    name: 'Baltic Sea',
    sub: 'Enclosed basin · ferries, cargo, fishing fleets',
    center: [59.5, 20.5],
    zoom: 6,
    bounds: [[54.0, 10.0], [66.0, 30.5]],
    color: '#00e5ff',
    vessels: '400+',
    // Clockwise from Øresund, tracing the actual coastline
    polygon: [
      [55.3, 10.5],  // Øresund south / Copenhagen
      [55.0, 11.2],  // Fehmarn Belt
      [54.3, 12.5],  // Rügen
      [54.0, 14.2],  // Pomeranian coast
      [54.4, 16.2],  // Polish coast
      [54.5, 18.6],  // Gdańsk Bay
      [54.8, 19.9],  // Kaliningrad coast
      [55.4, 21.1],  // Lithuanian coast
      [56.5, 21.1],  // Latvian SW
      [57.5, 21.6],  // Latvian W
      [58.4, 22.1],  // Saaremaa / Estonian W
      [59.4, 22.2],  // Gulf of Finland mouth S
      [59.3, 24.5],  // Tallinn
      [59.6, 26.0],  // Gulf of Finland E
      [60.1, 27.2],  // Viborg area
      [60.4, 28.7],  // Near Vyborg
      [59.9, 29.8],  // Near St Petersburg
      [60.3, 25.0],  // Finnish S coast
      [60.2, 24.9],  // Helsinki
      [60.5, 22.3],  // Turku archipelago
      [61.1, 21.5],  // Rauma
      [62.6, 21.2],  // Vaasa
      [63.5, 21.6],  // Gulf of Bothnia central
      [64.6, 22.1],  // Umeå
      [65.6, 22.6],  // Luleå
      [65.8, 24.6],  // Head of Bothnia (Tornio)
      [65.5, 25.5],  // Oulu
      [64.1, 24.2],  // Finnish coast mid
      [62.5, 20.6],  // Finnish coast south
      [60.5, 19.1],  // Åland Islands
      [59.5, 18.5],  // Stockholm archipelago
      [59.3, 18.4],  // Stockholm
      [58.5, 17.6],  // SE Sweden
      [57.5, 16.6],  // Kalmar
      [56.5, 16.1],  // Karlskrona
      [55.8, 14.6],  // Malmö
      [55.5, 13.1],  // Øresund N
      [55.3, 10.5],  // close
    ],
  },

  {
    id: 'english_channel',
    name: 'English Channel',
    sub: 'Busiest chokepoint · 500+ ships/day through Dover',
    center: [50.2, -1.8],
    zoom: 7,
    bounds: [[48.0, -6.0], [51.8, 2.6]],
    color: '#ffd600',
    vessels: '500+',
    // English coast (N) → Dover → French coast (S) → Brittany → back
    polygon: [
      [51.6, -5.5],  // SW Wales / Pembrokeshire
      [51.1, -5.7],  // N Devon coast
      [50.7, -5.7],  // Land's End
      [50.0, -5.2],  // Lizard Point
      [50.2, -4.2],  // Plymouth
      [50.6, -2.5],  // Weymouth / Lyme Bay
      [50.7, -1.2],  // Solent / Isle of Wight
      [50.8, -0.2],  // Eastbourne
      [51.1, 1.3],   // Dover
      [51.2, 1.8],   // Goodwin Sands
      [51.5, 2.5],   // North Sea entrance (UK)
      [51.2, 2.5],   // North Sea entrance (FR)
      [51.0, 1.9],   // Calais
      [50.7, 1.6],   // Boulogne
      [50.1, 1.6],   // Somme estuary
      [49.9, 1.0],   // Dieppe
      [49.5, 0.1],   // Étretat / Fécamp
      [49.4, -0.4],  // Le Havre
      [49.4, -1.0],  // Normandy coast
      [49.1, -1.7],  // Cherbourg E
      [49.7, -1.9],  // Cherbourg
      [49.7, -2.6],  // Cap de la Hague
      [48.8, -3.5],  // Brittany N coast
      [48.6, -4.4],  // Brest outer
      [48.4, -5.1],  // Pointe du Raz
      [48.0, -5.1],  // SW Brittany
      [48.0, -6.0],  // Atlantic corner
      [49.5, -6.0],  // Atlantic approach
      [50.0, -5.7],  // Scilly area
      [51.6, -5.5],  // close
    ],
  },

  {
    id: 'gulf_of_mexico',
    name: 'Gulf of Mexico',
    sub: 'Industrial hub · oil rigs, Port of Houston, cruise traffic',
    center: [24.5, -90.0],
    zoom: 5,
    bounds: [[18.0, -98.0], [30.5, -80.0]],
    color: '#ff6d00',
    vessels: '600+',
    // Clockwise from Texas coast
    polygon: [
      [30.0, -97.5],  // Texas central coast
      [29.4, -94.7],  // Galveston / Houston
      [29.3, -93.5],  // Sabine Pass
      [29.1, -92.5],  // Louisiana coast W
      [29.2, -90.0],  // Mississippi delta
      [29.0, -88.8],  // Louisiana coast E
      [30.2, -88.1],  // Mobile Bay
      [30.2, -87.3],  // Pensacola
      [30.1, -86.0],  // Florida panhandle
      [30.0, -85.0],  // Apalachicola Bay
      [29.5, -83.5],  // Florida W coast N
      [28.0, -83.0],  // Tampa Bay
      [27.5, -82.6],  // Sarasota
      [26.1, -82.0],  // Fort Myers
      [25.5, -81.5],  // Cape Sable
      [25.2, -80.6],  // Florida Keys start
      [24.8, -81.5],  // Key West
      [24.5, -82.8],  // Dry Tortugas
      [23.3, -84.1],  // Cuba NW cape
      [22.5, -84.5],  // Cuba W tip
      [21.9, -85.0],  // Cuba SW
      [21.5, -86.7],  // Yucatan Channel
      [21.3, -87.5],  // Yucatan NE
      [20.8, -87.3],  // Cancún area
      [19.8, -87.5],  // Belize coast N
      [18.5, -88.2],  // Belize S
      [18.5, -90.0],  // Tabasco / Campeche
      [19.0, -91.5],  // Campeche Bay E
      [18.7, -92.6],  // Tabasco coast
      [19.1, -93.6],  // Veracruz state N
      [20.0, -97.1],  // Tamaulipas
      [22.5, -97.8],  // Tampico / Altamira
      [24.0, -97.4],  // Brownsville / Matamoros
      [26.1, -97.2],  // South Texas
      [28.0, -97.0],  // Corpus Christi
      [30.0, -97.5],  // close
    ],
  },
];

export const REGION_BY_ID = Object.fromEntries(REGIONS.map(r => [r.id, r]));
