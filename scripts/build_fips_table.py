"""Rebuild county_fips.json, the NBC-area-name -> county-FIPS lookup.

Run this once per election cycle (or when NBC renames something); it is not
part of the normal pipeline.

WHY A TABLE, AND NOT A NAME MATCH AT RUNTIME
NBC's results API gives every county's FIPS in `mapData`, but keyed by FIPS
with no area name attached, and the `areas` list carries names with no id. So
the two have to be joined, and neither obvious shortcut works:

  - Position: mapData's key order matches the areas order in only 16 of the 43
    county-reporting states. FIPS codes were assigned alphabetically in the
    1960s and counties have been renamed since.
  - Name: cannot disambiguate Baltimore City (24510) from Baltimore County
    (24005), St. Louis City from St. Louis County, or any of Virginia's
    independent cities that share a name with the county around them. It also
    misses NBC's "Brooklyn"/"Manhattan"/"Staten Island" for Kings/New York/
    Richmond.

What does work is the vote vector: within a state, the per-candidate vote
counts identify a county uniquely once results are final (verified: zero
collisions across all 3,044 areas in 2024). So we join on that, against a
settled cycle, and freeze the answer. At runtime it is then a dictionary
lookup by name, which is stable and needs no votes - important, because on
election night every county starts at zero and vote vectors are useless.

    python scripts/build_fips_table.py [--cycle 2024] [--race president]
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nbc_api import BASE_URL, COUNTY_GEOGRAPHIES, _get, state_slugs

OUTPUT = Path(__file__).resolve().parent.parent / "static" / "county_fips.json"


def state_table(state_slug, race_slug, cycle):
    payload = _get(f"{BASE_URL}/state-results/{cycle}-elections/{state_slug}-{race_slug}-results")
    if payload.get("geography") not in COUNTY_GEOGRAPHIES:
        return state_slug, None, []

    race = max(payload["races"], key=lambda r: len(r.get("areas") or []))
    by_vector = {}
    for fips, cell in (race.get("mapData") or {}).items():
        by_vector.setdefault(_vector(cell.get("candidates") or []), []).append(fips)

    table, ambiguous = {}, []
    for area in race.get("areas") or []:
        matches = by_vector.get(_vector(area.get("candidates") or []), [])
        if len(matches) == 1:
            table[area["name"]] = matches[0]
        else:
            ambiguous.append(area["name"])
    return state_slug, table, ambiguous


def _vector(candidates):
    return tuple(sorted((c.get("code"), c.get("votes")) for c in candidates))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cycle", default="2024", help="A SETTLED cycle - the join needs final vote counts")
    parser.add_argument("--race", default="president")
    args = parser.parse_args()

    slugs = state_slugs(args.race, args.cycle)
    print(f"{len(slugs)} states published for {args.race} {args.cycle}")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda s: state_table(s, args.race, args.cycle), slugs))

    table, skipped = {}, []
    for state_slug, mapping, ambiguous in results:
        if mapping is None:
            skipped.append(state_slug)
            continue
        table[state_slug] = mapping
        if ambiguous:
            print(f"  {state_slug}: could not place {len(ambiguous)} area(s): {', '.join(ambiguous[:5])}")

    with open(OUTPUT, "w", encoding="utf-8") as file:
        json.dump(table, file, indent=1, sort_keys=True)

    print(f"Wrote {sum(len(v) for v in table.values())} areas across {len(table)} states to {OUTPUT}")
    print(f"Not county-reporting, so no entry: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
