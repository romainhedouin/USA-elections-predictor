"""Fetch county-level results from NBC News into raw_data[_<race>].csv.

    python fetch_results.py --race president|senate|governor

One HTTP call per state against NBC's results API (see nbc_api.py), which
replaced a Selenium browser driving every state page - the old pipeline is
kept in legacy/ as a fallback if NBC ever retires the API.

Each area's current vote share is extrapolated to 100% reporting
(Predicted = Real x 100 / PercentIn). That is a per-area scalar, so it can
never flip an area's own leader; the interesting case is a STATE whose leader
flips once you add up areas that are reporting at different rates, which is
what the map highlights.
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
from races import CSV_HEADER, RACES, race_files


def state_rows(payload):
    """One CSV row per reporting area of a state."""
    dem_name = payload["candidates"].get("dem", "")
    rep_name = payload["candidates"].get("gop", "")

    rows = []
    for area in payload["areas"]:
        percent_in = area["percent_in"]
        if percent_in <= 0:
            # Nothing counted yet, so there is no share to project forward and
            # 100/0 would blow up. Absent areas already render as "no data".
            continue
        real = [area["by_party"].get("dem", 0), area["by_party"].get("gop", 0)]
        rows.append(
            [payload["state"], area["name"], area["fips"], payload["geography"],
             payload["total_expected"], area["votes"], percent_in]
            + real
            + [round(v * 100 / percent_in) for v in real]
            + [dem_name, rep_name]
        )
    return rows


def fetch_race(race, cycle, skip, workers=8):
    race_slug = RACES[race]["nbc_slug"]
    try:
        published = nbc_api.state_slugs(race_slug, cycle)
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "?"
        if status == 404:
            # Routine before an election: the cycle only exists once there are
            # results. Say so in one line rather than a traceback - a scheduler
            # will hit this on every run for weeks beforehand.
            sys.exit(f"No {race} results published for {cycle} yet (404). "
                     f"Nothing to fetch; existing data is left alone.")
        raise

    slugs = [s for s in published if s not in skip]
    if not slugs:
        sys.exit(f"No states listed for {race} {cycle}. Has that cycle been published yet?")

    def one(state_slug):
        try:
            return state_slug, nbc_api.state_results(state_slug, race_slug, cycle), None
        except (requests.RequestException, ValueError, KeyError) as error:
            return state_slug, None, f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(workers) as pool:
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
        last_modified = payload.get("last_modified") or last_modified
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
    meta_path.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")


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
