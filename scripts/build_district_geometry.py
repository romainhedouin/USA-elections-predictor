"""2026 district shapes for the states that redrew, as newline-delimited GeoJSON.

Called by build_district_topology.sh. Every other state keeps the Census
Bureau's 2024 cartographic 119th-Congress districts (the lines the 2024
election used), which that script reads straight from the shapefile as it
always has. The states in NEW_MAP_STATES get the Census 120th-Congress lines
(the ones the November 2026 election uses), written by this script, trimmed to
that state's existing outline so the coastline and the borders with
neighbouring states stay exactly as drawn now.

The script refuses to run if the Census geometry disagrees with
NEW_MAP_STATES + KEEP_OLD_MAP: it compares every district's 119th and 120th
lines itself, so a state that changed but isn't listed (or vice versa) stops
the build instead of shipping a wrong map.

Each district whose own lines changed also gets "redrawnSince2024": its 2024
result describes different territory, so it can't be a 2026 baseline.

    python build_district_geometry.py --cb CB_CD119.shp --cd120 LEGISLATIVE.gdb \\
        --cd119-dir DIR_OF_TL_2025_XX_CD119_SHPS --out redrawn.ndjson \\
        --replaced-states replaced.json
"""

import argparse
import glob
import json
import sys

import numpy as np
import shapely
from pyogrio.raw import read
from pyproj import Transformer

# Census: "Ten states (Alabama, California, Florida, Louisiana, Missouri,
# North Carolina, Ohio, Tennessee, Texas, and Utah) delineated new boundaries
# for the 2026 election cycle."
# https://www.census.gov/programs-surveys/decennial-census/about/rdo/congressional-districts.html
NEW_MAP_STATES = {
    "01": "Alabama",         # 2023 legislative plan, restored by the Supreme Court (June 2026)
    "06": "California",      # Proposition 50 (Nov 2025)
    "12": "Florida",         # legislature (May 2026)
    "22": "Louisiana",       # legislature, after Louisiana v. Callais (May 2026)
    "37": "North Carolina",  # legislature (Oct 2025)
    "39": "Ohio",            # redistricting commission (Oct 2025)
    "47": "Tennessee",       # legislature (May 2026)
    "48": "Texas",           # legislature (Aug 2025)
    "49": "Utah",            # court-ordered (Nov 2025)
}
# Redrawn in the Census 120th-Congress file, but NOT the map voters use in
# November 2026, so these keep their 2024 lines.
KEEP_OLD_MAP = {
    "29": "Missouri",  # Missouri Supreme Court, Sept 3 2026: 2022 map for Nov 2026 (referendum pending)
}
EXCLUDED_STATES = {"11", "72"}  # DC and Puerto Rico: delegates, no voting seat
SIMPLIFY_DEGREES = 0.01  # ~1km, well under a pixel of the national map (~4.6km/px)
SAME_LINES = 0.999  # area overlap (intersection / union) above which a district counts as unchanged

_TO_EQUAL_AREA = Transformer.from_crs("EPSG:4269", "EPSG:5070", always_xy=True)


def load(path, layer=None):
    """GEOID -> (properties dict, geometry)."""
    meta, _, geoms, fields = read(path, layer=layer)
    columns = dict(zip(meta["fields"], fields))
    shapes = shapely.make_valid(shapely.from_wkb(geoms))
    return {
        geoid: ({name: columns[name][i] for name in columns}, shapes[i])
        for i, geoid in enumerate(columns["GEOID"])
    }


def equal_area(geometry):
    return shapely.transform(geometry, lambda xy: np.column_stack(_TO_EQUAL_AREA.transform(xy[:, 0], xy[:, 1])))


def overlap(a, b):
    a, b = equal_area(a), equal_area(b)
    return shapely.area(shapely.intersection(a, b)) / shapely.area(shapely.union(a, b))


def real_district(geoid):
    return geoid[:2] not in EXCLUDED_STATES and geoid[2:] not in ("ZZ", "98")


def trim_to_outline(state, cartographic, cd120):
    """This state's 120th-Congress districts, cut to the state's current
    outline, as an exact coverage: no gaps, no overlaps, neighbours sharing
    identical edges. The cartographic outline is generalized, so it pokes
    slightly past the detailed Census lines in places (rivers, coast); each
    such sliver joins the district it shares the most border with."""
    outline = shapely.union_all([geom for g, (_, geom) in cartographic.items() if g[:2] == state])
    clipped = {g: shapely.intersection(geom, outline) for g, (_, geom) in cd120.items() if g[:2] == state}
    for geoid, geom in clipped.items():
        if equal_area(geom).area < 1e6:  # under 1 km^2 left: a bug, not a district
            sys.exit(f"{geoid}: almost nothing left after trimming to the state outline")
    uncovered = shapely.difference(outline, shapely.union_all(list(clipped.values())))
    if equal_area(uncovered).area > 0.01 * equal_area(outline).area:
        sys.exit(f"state {state}: the Census lines leave over 1% of the outline uncovered")

    # Every district line plus the outline, noded together, cut into faces.
    lines = shapely.union_all([shapely.boundary(outline)] + [shapely.boundary(g) for g in clipped.values()])
    faces = [f for f in shapely.get_parts(shapely.polygonize(shapely.get_parts(lines)))
             if shapely.within(shapely.point_on_surface(f), outline)]
    geoids = list(clipped)
    owned = {g: [] for g in geoids}
    for face in faces:
        covered = {g: shapely.area(shapely.intersection(face, clipped[g])) for g in geoids}
        owner = max(covered, key=covered.get)
        if covered[owner] < 0.5 * face.area:  # a gap sliver, not part of any district
            edge = shapely.buffer(shapely.boundary(face), 1e-7)  # ~1cm
            shared = {g: shapely.length(shapely.intersection(shapely.boundary(clipped[g]), edge)) for g in geoids}
            owner = max(shared, key=shared.get)
        owned[owner].append(face)
    districts = [polygonal(shapely.union_all(owned[g])) for g in geoids]
    if not shapely.coverage_is_valid(districts):
        sys.exit(f"state {state}: districts overlap or leave gaps")

    # The Census lines are full-detail; the rest of the map is pre-generalized.
    # Simplify the inner lines to a similar level (shared edges once, so no
    # gaps can open) and leave the outline alone so neighbours still line up.
    simplified = [polygonal(g) for g in shapely.coverage_simplify(districts, SIMPLIFY_DEGREES, simplify_boundary=False)]
    if not shapely.coverage_is_valid(simplified):
        sys.exit(f"state {state}: simplified districts overlap or leave gaps")
    return dict(zip(geoids, simplified))


def polygonal(geometry):
    """Valid, polygons only - overlay operations can leave stray lines, points
    and zero-area slivers. A flat sliver is worse than useless: d3 can't tell
    which way it winds and draws it as the whole globe."""
    parts = shapely.get_parts(shapely.make_valid(geometry))
    polygons = [p for p in parts if shapely.get_type_id(p) == 3 and equal_area(p).area >= 1e4]  # 1 hectare
    result = shapely.make_valid(shapely.union_all(polygons))
    if not shapely.is_valid(result) or shapely.get_type_id(result) not in (3, 6):
        sys.exit("could not produce a valid polygon")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cb", required=True, help="cb_2024_us_cd119_20m.shp (the current map's shapes)")
    parser.add_argument("--cd120", required=True, help="tlgdb_2026_us_legislative.gdb")
    parser.add_argument("--cd119-dir", required=True, help="directory of unzipped tl_2025_XX_cd119 shapefiles")
    parser.add_argument("--out", required=True, help="GeoJSON features for NEW_MAP_STATES")
    parser.add_argument("--replaced-states", required=True,
                        help="JSON list of the state FIPS codes --out replaces")
    args = parser.parse_args()

    cartographic = {g: v for g, v in load(args.cb).items() if real_district(g)}
    cd120 = {g: v for g, v in load(args.cd120, "Congressional_Districts").items() if real_district(g)}
    cd119 = {}
    for shp in glob.glob(f"{args.cd119_dir}/*.shp"):
        cd119.update({g: v for g, v in load(shp).items() if real_district(g)})

    if not (set(cartographic) == set(cd120) == set(cd119)) or len(cd120) != 435:
        sys.exit(f"district sets disagree: cb={len(cartographic)} cd120={len(cd120)} cd119={len(cd119)}")

    same = {g: overlap(cd119[g][1], cd120[g][1]) for g in cd120}
    changed_states = {g[:2] for g, v in same.items() if v < SAME_LINES}
    expected = set(NEW_MAP_STATES) | set(KEEP_OLD_MAP)
    if changed_states != expected:
        sys.exit(f"Census geometry says these states changed: {sorted(changed_states)}; "
                 f"NEW_MAP_STATES + KEEP_OLD_MAP list {sorted(expected)}. Update the lists first.")

    trimmed = {}
    for state in NEW_MAP_STATES:
        trimmed.update(trim_to_outline(state, cartographic, cd120))

    count = redrawn = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for geoid in sorted(trimmed):
            state = geoid[:2]
            # Clockwise outer rings, like the shapefile input: d3 reads the
            # other winding as "the whole globe except this district".
            geom = shapely.orient_polygons(trimmed[geoid], exterior_cw=True)
            properties = {"name": cd120[geoid][0]["NAMELSAD"], "state": state}
            if same[geoid] < SAME_LINES:
                properties["redrawnSince2024"] = True
                redrawn += 1
            feature = {"type": "Feature", "id": geoid, "properties": properties,
                       "geometry": json.loads(shapely.to_geojson(geom))}
            out.write(json.dumps(feature) + "\n")
            count += 1

    with open(args.replaced_states, "w", encoding="utf-8") as out:
        json.dump(sorted(NEW_MAP_STATES), out)
    print(f"{count} districts replaced in {len(NEW_MAP_STATES)} states, {redrawn} of them redrawn since 2024 "
          f"(kept 2024 lines for {', '.join(KEEP_OLD_MAP.values())})", file=sys.stderr)


if __name__ == "__main__":
    main()
