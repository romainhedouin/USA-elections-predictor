#!/bin/bash
# Rebuild districts-albers-10m.json and house_districts.json.
#
# Run this once per redistricting cycle (the 119th Congress's boundaries hold
# through the 2026 midterms; re-run after the 2030 census draws new lines).
# It is not part of the normal fetch/serve pipeline - those two output files
# are checked in, like county_fips.json is.
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
# Requires: curl, unzip, node/npx (installs its own toolchain into a scratch
# node_modules the first time it runs).

set -euo pipefail
cd "$(dirname "$0")/../static"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

SHAPE_URL="https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_20m.zip"
SHAPE_BASE="cb_2024_us_cd119_20m"

echo "Fetching $SHAPE_URL"
curl -sL -o "$WORK/$SHAPE_BASE.zip" "$SHAPE_URL"
unzip -q -o "$WORK/$SHAPE_BASE.zip" -d "$WORK"

echo "Installing the topojson/d3 build toolchain (once)"
npm install --silent --no-audit --no-fund --prefix "$WORK" \
  shapefile topojson-server topojson-client topojson-simplify d3-geo-projection d3-geo ndjson-cli

export PATH="$WORK/node_modules/.bin:$PATH"

echo "Building districts-albers-10m.json"
geo2topo -q 1e5 -n districts=<( \
    shp2json --encoding utf-8 -n "$WORK/$SHAPE_BASE.shp" \
      | ndjson-filter 'd.properties.STATEFP !== "11" && d.properties.STATEFP !== "72"' \
      | ndjson-map '(d.id = d.properties.GEOID, d.properties = {name: d.properties.NAMELSAD, state: d.properties.STATEFP}, d)' \
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
}

const ordered = Object.fromEntries(Object.keys(table).sort().map(k => [k, table[k]]));
fs.writeFileSync("house_districts.json", JSON.stringify(ordered, null, 1) + "\n");
console.log(`Wrote ${Object.keys(ordered).length} districts to house_districts.json`);
'

echo "Done. districts-albers-10m.json and house_districts.json are ready to commit."
