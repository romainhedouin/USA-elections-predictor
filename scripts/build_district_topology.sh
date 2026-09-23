#!/bin/bash
# Rebuild districts-albers-10m.json and house_districts.json.
#
# Run this whenever a state's congressional map changes (nine states redrew
# for 2026 - see build_district_geometry.py's NEW_MAP_STATES, which also
# says how to handle a court changing a map again). It is not part of the
# normal fetch/serve pipeline - those two output files are checked in, like
# county_fips.json is.
#
# WHY THIS EXISTS, AND WHY IT ISN'T JUST A CDN URL
# map.html's county/state layer is us-atlas's counties-albers-10m.json,
# fetched straight from a CDN with no build step of its own. us-atlas ships
# no congressional-district layer - searched npm and jsDelivr, nothing exists
# - so this script reproduces us-atlas's own build recipe (see its
# `prepublish` script) against the Census Bureau's district shapefile
# instead, to get a layer that lines up pixel-for-pixel with the counties and
# states already on screen. Same d3.geoAlbersUsa().scale(1300)
# .translate([487.5, 305]) us-atlas itself uses - change that and every
# existing layer stops aligning with this one.
#
# DC and Puerto Rico are dropped: both have a delegate in the House, not a
# voting member, so they have no seat for this map's purposes (the same
# reasoning races.py already applies to president/governor).
#
#   ./scripts/build_district_topology.sh
#
# Requires: curl, unzip, python3, node/npx (installs its own Python and node
# toolchains into a scratch directory; nothing is installed globally).

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR/../static"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# The current map's shapes: 2024 cartographic 119th-Congress districts.
SHAPE_URL="https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_20m.zip"
SHAPE_BASE="cb_2024_us_cd119_20m"
# 120th-Congress (2026) districts, only published as a geodatabase so far.
CD120_URL="https://www2.census.gov/geo/tiger/TGRGDB26/tlgdb_2026_us_legislative.gdb.zip"
# Detailed 119th-Congress lines, per state - compared against the 120th to
# find which districts actually changed.
CD119_DIR_URL="https://www2.census.gov/geo/tiger/TIGER2025/CD"

echo "Fetching $SHAPE_URL"
curl -fsSL -o "$WORK/$SHAPE_BASE.zip" "$SHAPE_URL"
unzip -q -o "$WORK/$SHAPE_BASE.zip" -d "$WORK"

echo "Fetching $CD120_URL (~100MB)"
curl -fsSL -o "$WORK/cd120.gdb.zip" "$CD120_URL"
unzip -q -o "$WORK/cd120.gdb.zip" -d "$WORK"

echo "Fetching per-state 119th-Congress lines from $CD119_DIR_URL"
mkdir -p "$WORK/cd119"
for zip in $(curl -fsSL "$CD119_DIR_URL/" | grep -oE 'tl_2025_[0-9]{2}_cd119\.zip' | sort -u); do
  curl -fsSL -o "$WORK/cd119/$zip" "$CD119_DIR_URL/$zip"
  unzip -q -o "$WORK/cd119/$zip" -d "$WORK/cd119"
done

echo "Installing the Python geometry toolchain (once)"
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --quiet --disable-pip-version-check pyogrio shapely pyproj numpy

echo "Merging the 2026 lines for redrawn states"
"$WORK/venv/bin/python" "$SCRIPT_DIR/build_district_geometry.py" \
  --cb "$WORK/$SHAPE_BASE.shp" \
  --cd120 "$WORK/tlgdb_2026_us_legislative.gdb" \
  --cd119-dir "$WORK/cd119" \
  --out "$WORK/redrawn.ndjson" \
  --replaced-states "$WORK/replaced.json"

echo "Installing the topojson/d3 build toolchain (once)"
npm install --silent --no-audit --no-fund --prefix "$WORK" \
  shapefile topojson-server topojson-client topojson-simplify d3-geo-projection d3-geo ndjson-cli

export PATH="$WORK/node_modules/.bin:$PATH"

echo "Building districts-albers-10m.json"
# Unchanged states straight from the shapefile, exactly as before; the
# redrawn states' 2026 districts appended after them.
REPLACED=$(cat "$WORK/replaced.json")
geo2topo -q 1e5 -n districts=<( \
    { shp2json --encoding utf-8 -n "$WORK/$SHAPE_BASE.shp" \
        | ndjson-filter "d.properties.STATEFP !== '11' && d.properties.STATEFP !== '72' && !$REPLACED.includes(d.properties.STATEFP)" \
        | ndjson-map '(d.id = d.properties.GEOID, d.properties = {name: d.properties.NAMELSAD, state: d.properties.STATEFP}, d)'; \
      cat "$WORK/redrawn.ndjson"; } \
      | geoproject -n 'd3.geoAlbersUsa().scale(1300).translate([487.5, 305])') \
  | toposimplify -f -p 0.25 \
  > districts-albers-10m.json

echo "Building house_districts.json"
NODE_PATH="$WORK/node_modules" node -e '
const fs = require("fs");
const topojson = require("topojson-client");

// Standard 2-digit Census state FIPS codes - stable, not redistricting data.
const STATE_NAMES = {
  "01":"Alabama","02":"Alaska","04":"Arizona","05":"Arkansas","06":"California",
  "08":"Colorado","09":"Connecticut","10":"Delaware","12":"Florida","13":"Georgia",
  "15":"Hawaii","16":"Idaho","17":"Illinois","18":"Indiana","19":"Iowa","20":"Kansas",
  "21":"Kentucky","22":"Louisiana","23":"Maine","24":"Maryland","25":"Massachusetts",
  "26":"Michigan","27":"Minnesota","28":"Mississippi","29":"Missouri","30":"Montana",
  "31":"Nebraska","32":"Nevada","33":"New Hampshire","34":"New Jersey","35":"New Mexico",
  "36":"New York","37":"North Carolina","38":"North Dakota","39":"Ohio","40":"Oklahoma",
  "41":"Oregon","42":"Pennsylvania","44":"Rhode Island","45":"South Carolina",
  "46":"South Dakota","47":"Tennessee","48":"Texas","49":"Utah","50":"Vermont",
  "51":"Virginia","53":"Washington","54":"West Virginia","55":"Wisconsin","56":"Wyoming",
};

const topology = JSON.parse(fs.readFileSync("districts-albers-10m.json"));
// Everything must land where the AlbersUsa layout puts the US (roughly
// [-58, 14] to [958, 607], the Alaska inset poking left of 0). A shape d3
// read inside-out (wrong ring winding) comes out as the whole clip box of
// the projection instead, far outside that.
const [x0, y0, x1, y1] = topology.bbox;
if (x0 < -60 || y0 < 10 || x1 > 960 || y1 > 610) throw new Error(`topology bbox ${topology.bbox} leaves the map`);
const features = topojson.feature(topology, topology.objects.districts).features;

const table = {};
for (const f of features) {
  const stateFips = f.properties.state;
  const stateName = STATE_NAMES[stateFips];
  if (!stateName) throw new Error(`unknown state FIPS ${stateFips} for district ${f.id}`);
  const districtNum = f.id.slice(2);
  table[f.id] = {
    state: stateName,
    label: districtNum === "00" ? `${stateName} At-Large` : `${stateName} District ${parseInt(districtNum, 10)}`,
  };
  if (f.properties.redrawnSince2024) table[f.id].redrawnSince2024 = true;
}

const ordered = Object.fromEntries(Object.keys(table).sort().map(k => [k, table[k]]));
fs.writeFileSync("house_districts.json", JSON.stringify(ordered, null, 1) + "\n");
console.log(`Wrote ${Object.keys(ordered).length} districts to house_districts.json`);
'

echo "Done. districts-albers-10m.json and house_districts.json are ready to commit."
