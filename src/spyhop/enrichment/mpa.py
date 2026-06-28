"""Marine Protected Area bounding-box intersection.

Uses a curated list of the 30 most significant global marine protected areas
with lat/lon bounding boxes derived from WDPA/official boundary data.
Fast O(N) scan — N=30 is negligible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MPABox:
    name: str
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    wraps_antimeridian: bool = False  # True when the box crosses ±180°


# 30 major global marine protected areas with bounding boxes.
# Sources: WDPA, NOAA, IUCN MPA database.
MAJOR_MPAS: list[MPABox] = [
    # Pacific
    MPABox("Great Barrier Reef Marine Park",     -24.5, -10.7,  142.5,  154.0),
    MPABox("Coral Sea Marine Park",              -33.0, -10.0,  147.0,  165.0),
    MPABox("Papahānaumokuākea MNM",               21.0,  30.0, -182.0, -161.0, wraps_antimeridian=True),
    MPABox("Pacific Remote Islands MNM",         -12.0,  18.0,  165.0, -154.0, wraps_antimeridian=True),
    MPABox("Phoenix Islands Protected Area",      -6.0,  -1.0, -176.0, -170.0),
    MPABox("Mariana Trench NMM",                  11.0,  23.0,  142.0,  148.0),
    MPABox("Galápagos Marine Reserve",            -2.5,   1.5,  -92.5,  -88.5),
    MPABox("Revillagigedo NP",                    17.5,  20.5, -114.0, -108.0),
    MPABox("Cook Islands MPA",                   -22.0,   -8.0, -168.0, -156.0),
    MPABox("Pitcairn Islands MPA",               -28.0, -20.0, -132.0, -120.0),

    # Indian Ocean
    MPABox("Chagos / BIOT MPA",                   -7.6,  -4.4,   70.0,   74.7),
    MPABox("Heard & McDonald Islands MPA",        -55.0, -50.0,   71.0,   79.0),
    MPABox("Seychelles MPA",                       -9.5,  -3.5,   45.0,   56.0),
    MPABox("Maldives MPA",                         -0.5,   7.5,   72.0,   74.0),
    MPABox("Cocos (Keeling) Islands MR",          -13.0, -11.0,   96.0,   97.0),

    # Atlantic
    MPABox("South Georgia & South Sandwich MPA",  -60.0, -51.0,  -42.0,  -24.0),
    MPABox("Ascension Island MPA",                 -8.5,  -7.0,  -14.7,  -14.0),
    MPABox("St Helena MPA",                       -16.5, -15.5,   -5.9,   -5.5),
    MPABox("Tristan da Cunha MPA",                -41.0, -36.0,  -13.0,   -8.0),
    MPABox("NE US Canyons & Seamounts NM",         38.0,  43.0,  -74.0,  -64.0),
    MPABox("Sargasso Sea MPA",                     20.0,  35.0,  -75.0,  -40.0),
    MPABox("Canary Islands MPA",                   27.0,  30.0,  -18.5,  -13.0),

    # Antarctic / Southern Ocean
    MPABox("Ross Sea MPA",                        -78.0, -60.0,  160.0, -150.0, wraps_antimeridian=True),
    MPABox("East Antarctic MPA",                  -70.0, -55.0,   30.0,  120.0),
    MPABox("Weddell Sea MPA (proposed)",          -80.0, -55.0,  -60.0,   20.0),

    # Arctic
    MPABox("Greenland MPA",                        57.0,  83.5,  -74.0,  -12.0),
    MPABox("Svalbard MPA",                         74.0,  81.0,   10.0,   35.0),

    # Southeast Asia / Coral Triangle
    MPABox("Tubbataha Reef NMP",                    8.5,  10.5,  119.0,  120.5),
    MPABox("Raja Ampat MPA",                       -2.5,   1.5,  129.0,  132.0),
    MPABox("Coral Triangle (broad)",               -12.0,  12.0,  115.0,  145.0),
]


def _lon_in_box(lon: float, box: MPABox) -> bool:
    if box.wraps_antimeridian:
        return lon >= box.min_lon or lon <= box.max_lon
    return box.min_lon <= lon <= box.max_lon


def in_protected_area(lat: float, lon: float) -> str | None:
    """Return the MPA name if (lat, lon) falls inside any major MPA, else None."""
    for mpa in MAJOR_MPAS:
        if mpa.min_lat <= lat <= mpa.max_lat and _lon_in_box(lon, mpa):
            return mpa.name
    return None
