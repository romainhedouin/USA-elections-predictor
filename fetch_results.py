"""Fetch results from NBC News into raw_data[_<race>].csv.

    python fetch_results.py --race president|senate|governor|house

One HTTP call per state against NBC's results API (see nbc_api.py) for
president/senate/governor - which replaced a Selenium browser driving every
state page, kept in legacy/ as a fallback if NBC ever retires the API. House
is one HTTP call total: NBC's national House payload is already broken down
by district, so there's no per-state loop for it (see
nbc_api.house_results()).

This file writes real vote counts only - no extrapolation. Projecting each
area's current vote share to 100% reporting (flat, or the historical-swing
model for areas with a baseline) happens client-side in estimate.js, per the
decision that the server fetches/caches raw data and never computes derived
values. See map.html for the projection and its per-STATE (or, for House,
per-DISTRICT) leader-mismatch check, which is why it aggregates every area of
a state rather than treating each one independently.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

import nbc_api
from races import CSV_HEADER, HOUSE_DISTRICTS, RACES, race_files

WORKERS = 8  # parallel NBC state requests; keep <= the HTTP pool size (10)


def state_rows(payload):
    """One CSV row per reporting area of a state."""
    dem_name = payload["candidates"].get("dem", "")
    rep_name = payload["candidates"].get("gop", "")

    rows = []
    for area in payload["areas"]:
        percent_in = area["percent_in"]
        if percent_in <= 0:
            # Nothing counted yet, so there is no share for estimate.js to
            # project forward. Absent areas already render as "no data".
            continue
        real = [area["by_party"].get("dem", 0), area["by_party"].get("gop", 0)]
        rows.append(
            [payload["state"], area["name"], area["fips"], payload["geography"],
             payload["total_expected"], area["votes"], percent_in]
            + real
            + [dem_name, rep_name]
        )
    return rows


def house_rows(districts):
    """One CSV row per House district - no per-state or per-county loop.

    Each row already *is* a full race (see nbc_api.house_results), unlike the
    other three races where a row is one county and the map sums counties up
    into a state. "State Total Expected" here is just that district's own
    expected total, which is also why the map's per-state aggregation is a
    no-op for House: grouping by district finds exactly one row.
    """
    rows = []
    for district in districts:
        percent_in = district["percent_in"]
        if percent_in <= 0:
            continue
        info = HOUSE_DISTRICTS.get(district["geoid"])
        if info is None:
            # DC and Puerto Rico have a non-voting delegate race NBC may list
            # here; they're not in HOUSE_DISTRICTS (no voting seat), so skip
            # rather than paint a seat that doesn't exist.
            continue
        real = [district["by_party"].get("dem", 0), district["by_party"].get("gop", 0)]
        rows.append(
            [info["state"], info["label"], district["geoid"], "districts",
             district["total_expected"], district["votes"], percent_in]
            + real
            + [district["candidates"].get("dem", ""), district["candidates"].get("gop", "")]
        )
    return rows


def _or_exit_on_404(fn, race, cycle, *args):
    try:
        return fn(*args)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            # Routine before an election: the cycle only exists once there are
            # results. Say so in one line rather than a traceback - a scheduler
            # will hit this on every run for weeks beforehand.
            sys.exit(f"No {race} results published for {cycle} yet (404). "
                     f"Nothing to fetch; existing data is left alone.")
        raise


def fetch_race(race, cycle, skip):
    if race == "house":
        # One national payload, not one request per state - see
        # nbc_api.house_results(). --skip has no meaning here (there is no
        # per-state fetch to skip) and is silently ignored.
        districts, last_modified = _or_exit_on_404(nbc_api.house_results, race, cycle, cycle)
        return house_rows(districts), [], last_modified

    race_slug = RACES[race]["nbc_slug"]
    published = _or_exit_on_404(nbc_api.state_slugs, race, cycle, race_slug, cycle)

    slugs = [s for s in published if s not in skip]
    if not slugs:
        sys.exit(f"No states listed for {race} {cycle}. Has that cycle been published yet?")

    def one(state_slug):
        try:
            return state_slug, nbc_api.state_results(state_slug, race_slug, cycle), None
        except (requests.RequestException, ValueError, KeyError) as error:
            return state_slug, None, f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(one, slugs))

    rows, failures, no_county = [], [], []
    last_modified = None
    for state_slug, payload, error in results:
        if error:
            failures.append(f"{state_slug} ({error})")
            continue
        if payload is None:
            continue  # no race of this type in this state - routine
        rows.extend(state_rows(payload))
        lm = payload.get("last_modified")
        if lm and (last_modified is None or lm > last_modified):
            last_modified = lm  # ISO-8601 Z strings sort chronologically
        if not payload["county_level"]:
            no_county.append(f"{payload['state']} ({payload['geography']})")

    if no_county:
        # Not an error - these states genuinely have no county-level numbers,
        # and the map says so rather than inventing a county breakdown.
        print(f"Reported below county level, so no county map: {', '.join(sorted(no_county))}")
    return rows, failures, last_modified


def write_meta(output_csv, race, cycle, rows, source, last_modified=None):
    """A sidecar the page reads so it can say what it is showing, and when.

    Without this the page has no way to tell real results from the mock
    fixtures, or to show how stale the numbers are - both of which matter a
    lot more once this is deployed somewhere strangers can find it.
    """
    config = RACES[race]
    meta = {
        "race": race,
        "label": config["label"],
        "electionYear": config["election_year"],
        "dataYear": cycle,
        "source": source,
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sourceLastModified": last_modified,
        "rows": len(rows),
        "states": len({row[0] for row in rows}),
    }
    meta_path = output_csv.with_suffix(".meta.json")
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    tmp.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, meta_path)


def write_csv(rows, output_csv):
    # Write-then-rename, so a crash mid-write can't leave a half-finished CSV
    # that the map would happily render as real results.
    tmp = output_csv.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    os.replace(tmp, output_csv)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race", default="president", choices=RACES.keys())
    parser.add_argument("--nbc-cycle", dest="nbc_cycle",
                        help="Year in NBC's URL path (default: the race's nbc_cycle in races.py)")
    parser.add_argument("--out-dir", default=".", help="Where to write the CSV (default: current directory)")
    parser.add_argument("--skip", default="", help="Comma-separated state slugs to skip, e.g. alaska,hawaii")
    args = parser.parse_args()

    race = RACES[args.race]
    cycle = args.nbc_cycle or race["nbc_cycle"]
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    output_csv = Path(args.out_dir) / race_files(args.race)["output_csv"].name

    print(f"{race['label']} {race['election_year']} results, from NBC's {cycle} pages")
    rows, failures, last_modified = fetch_race(args.race, cycle, skip)

    if not rows:
        sys.exit(f"No results parsed - refusing to overwrite {output_csv}.")

    write_csv(rows, output_csv)
    write_meta(output_csv, args.race, cycle, rows, source="live", last_modified=last_modified)
    states = len({row[0] for row in rows})
    print(f"Wrote {len(rows)} rows across {states} states to {output_csv}")

    if failures:
        # The good states are already written; exit non-zero so a scheduler
        # notices rather than treating a partial refresh as a clean one.
        sys.exit(f"Failed: {', '.join(failures)}")


if __name__ == "__main__":
    main()
