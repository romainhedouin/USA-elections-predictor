"""Build historical_<race>.json: the prior-cycle baseline estimate.js needs
to turn a flat extrapolation into a swing-adjusted one.

Run once per redistricting/data refresh, like build_fips_table.py and
build_district_topology.sh - this is not part of the regular fetch_results.py
refresh loop.

    python scripts/build_historical_baseline.py --race president --input countypres.csv
    python scripts/build_historical_baseline.py --race house --input house.csv
    python scripts/build_historical_baseline.py --race senate --input senate.csv
    python scripts/build_historical_baseline.py --race governor --input governor.csv

DATA SOURCES:
  --format medsl (default): MIT Election Data and Science Lab (MEDSL),
    Harvard Dataverse (https://doi.org/10.7910/DVN/VOQCHQ and sibling MEDSL
    datasets for House, Senate, and Governor returns), CC0-licensed. Download
    the relevant CSV by hand from Dataverse first - its API requires a
    Guestbook response before it will serve the file, so there's no
    tokenless URL to fetch it from here, and the raw files are too large to
    check into this repo anyway. See README.md's Data Sources section for
    the citation to use.
  --format wide: a pre-aggregated one-row-per-county CSV (columns:
    state_name, county_fips, votes_gop, votes_dem, ...) - e.g.
    github.com/tonmcg/US_County_Level_Election_Results_08-24, MIT-licensed
    and freely downloadable with no gate, which is what this project's own
    historical_president.json was actually built from (see README.md's Data
    Sources section) since the MEDSL guestbook isn't practical to automate.
    President only - this format doesn't cover House/Senate/Governor.
  --format house-state-apportioned: House only. No real per-district House
    total is available to us (see above), but a real per-STATE total is -
    the same `wide` president file, summed by state, divided evenly across
    that state's own current district count (districts are apportioned to
    roughly equal population by design, so this is a defensible stand-in).
    Produces a ballot-COUNT-only entry (votes, no demShare/repShare) per
    district - estimate.js still falls back to the flat estimate for the
    party split, honestly, but the "total ballots" figure is now a real
    number instead of absent.
  --format house-county-weighted: House only, and better than
    house-state-apportioned - instead of splitting a state's total evenly
    across its districts, sums each district's REAL constituent counties'
    vote totals (from the same wide president CSV, passed via --counties),
    using the Census Bureau's own county<->congressional-district
    relationship file (--input) to know which counties are in which
    district: https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.2020.html
    ("119th Congressional District to County", a plain pipe-delimited .txt,
    no gate). Reflects each district's actual population distribution
    instead of assuming every district in a state is equal in size.

GRANULARITY DIFFERS BY RACE, and that's a real constraint, not a choice:
  - President: MEDSL publishes actual county-level returns, so the baseline
    is per-FIPS, matching how granular our live county data is.
  - House: MEDSL publishes district-level returns directly (no county
    aggregation needed, and none would be correct anyway - district lines
    don't nest inside county lines). Baseline is per-GEOID.
  - Senate and Governor: MEDSL's readily-available data for these offices is
    STATEWIDE only, not county-level. The baseline for these two races is
    therefore one value per state, applied uniformly to every county in that
    state - there is no per-county swing to compute for Senate/Governor in
    v1. (This is hole #6's "similar-county clustering" limitation taken to
    its extreme for these two races: with no county-level historical data at
    all, there's nothing finer to cluster.)

Expected MEDSL column names (verify against the actual downloaded file's
codebook before relying on this - Dataverse has revised these before):
  county-level (president): year, state, county_fips, office, party,
    candidatevotes, totalvotes
  district-level (house): year, state, state_po, district, party,
    candidatevotes, totalvotes
  state-level (senate/governor): year, state, office, party, candidatevotes,
    totalvotes
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from races import (
    GOVERNOR_LAST_ELECTED,
    HOUSE_DISTRICTS,
    HOUSE_LAST_ELECTED,
    PRESIDENT_LAST_ELECTED,
    REDISTRICTING_AFFECTED_DISTRICTS,
    SENATE_LAST_CONTESTED,
)

_STATE_FIPS_PREFIX = {info["state"]: geoid[:2] for geoid, info in HOUSE_DISTRICTS.items()}
_AT_LARGE_STATE_FIPS = {geoid[:2] for geoid in HOUSE_DISTRICTS if geoid.endswith("00")}


def _party_of(row):
    # MEDSL has used different column names across releases: "party" in the
    # older long-format files (e.g. countypres_2000-2016.csv), "party_simplified"
    # in newer ones (e.g. the 1976-2024 senate file) - check both rather than
    # picking one and silently matching nothing against the other.
    party = (row.get("party") or row.get("party_simplified") or row.get("party_detailed") or "").strip().upper()
    if party.startswith("DEM"):
        return "dem"
    if party.startswith("REP"):
        return "rep"
    return None


def _two_party_share(dem_votes, rep_votes):
    """The share AND the raw total two-party vote count.

    The total matters beyond the share itself: estimate.js's confidenceWeight
    needs to know how much of a state's expected vote is accounted for by
    areas currently reporting, and for an area that hasn't reported yet this
    historical total is the only estimate of its eventual size we have - see
    estimate.js's expectedTotalVotes().
    """
    # Uncontested (one major party absent) would give a degenerate 0%/100% baseline.
    if dem_votes <= 0 or rep_votes <= 0:
        return None
    total = dem_votes + rep_votes
    return {"demShare": round(dem_votes / total, 4), "repShare": round(rep_votes / total, 4), "votes": total}


def _county_fips(row):
    raw = (row.get("county_fips") or "").strip()
    return raw.zfill(5) if raw.isdigit() else None


def president_baseline(rows, year):
    """FIPS -> {demShare, repShare, votes, year} from county-level returns."""
    votes = {}
    for row in rows:
        if str(row.get("year")) != str(year):
            continue
        party = _party_of(row)
        if party is None:
            continue
        fips = _county_fips(row)
        if fips is None:
            continue
        bucket = votes.setdefault(fips, {"dem": 0, "rep": 0})
        bucket[party] += int(float(row.get("candidatevotes") or 0))

    baseline = {}
    for fips, bucket in votes.items():
        share = _two_party_share(bucket["dem"], bucket["rep"])
        if share:
            baseline[fips] = {**share, "year": year}
    return baseline


def president_baseline_wide(rows):
    """FIPS -> {demShare, repShare, votes, year} from a wide, one-row-per-
    county CSV (state_name, county_fips, votes_gop, votes_dem, ...) - see
    this file's --format wide docs above for where that shape comes from.
    No year filter: unlike MEDSL's long format, a wide file is already a
    single election's results, not several years stacked in one file.
    """
    baseline = {}
    for row in rows:
        fips = _county_fips(row)
        if fips is None:
            continue
        dem = int(float(row.get("votes_dem") or 0))
        rep = int(float(row.get("votes_gop") or 0))
        share = _two_party_share(dem, rep)
        if share:
            baseline[fips] = {**share, "year": PRESIDENT_LAST_ELECTED}
    return baseline


def house_baseline_state_apportioned(rows, year):
    """GEOID -> {votes, year} (no demShare/repShare - a ballot-COUNT-only
    estimate, not a partisan one), from the same wide president CSV as
    president_baseline_wide, summed by state and divided evenly across that
    state's own current district count. See this file's --format
    house-state-apportioned docs above for the reasoning.
    """
    totals_by_state = {}
    for row in rows:
        state = row.get("state_name")
        if not state:
            continue
        totals_by_state[state] = totals_by_state.get(state, 0) + int(float(row.get("total_votes") or 0))

    districts_by_state = {}
    for geoid, info in HOUSE_DISTRICTS.items():
        districts_by_state.setdefault(info["state"], []).append(geoid)

    baseline = {}
    for state, geoids in districts_by_state.items():
        total = totals_by_state.get(state)
        if not total:
            continue
        per_district = total / len(geoids)
        for geoid in geoids:
            baseline[geoid] = {"votes": round(per_district), "year": year}
    return baseline


def house_baseline_county_weighted(relationship_rows, county_votes, year):
    """GEOID -> {votes, year} (ballot-COUNT-only), by summing each
    district's actual constituent counties' real vote totals - more
    accurate than house_baseline_state_apportioned's even split within a
    state, since it reflects each district's real population distribution
    rather than assuming every district in a state is equal in size.

    relationship_rows: the Census Bureau's county<->congressional-district
    relationship file (GEOID_CD119_20, GEOID_COUNTY_20) - see this file's
    --format house-county-weighted docs above for where to get it.
    county_votes: county FIPS -> real total vote count (e.g. summed from
    the same wide president CSV as president_baseline_wide).

    A county entirely inside one district contributes its whole total to
    that district - exact, not an estimate. A split county (~13% of them,
    per the 119th Congress file) is divided among its districts by each
    district's REMAINING population quota, not land area: redistricting law
    requires every district within a state to have essentially equal
    population, so a district's "fair share" of the state's total is just
    state_total / num_districts_in_state; subtracting whatever it already
    gets from whole counties leaves how much of a split county's population
    it still needs. This matters because land area and population can point
    in opposite directions within one county - Maricopa County, AZ spans
    both dense Phoenix-metro districts and vast empty desert, so an
    area-weighted split (an earlier version of this function) starved the
    urban districts of nearly all of Maricopa's real vote count and handed
    it to whichever district happened to grab the empty desert instead.
    """
    # Drops the Census "ZZ" water pseudo-districts and DC's delegate "98".
    relationship_rows = [r for r in relationship_rows if r["GEOID_CD119_20"] in HOUSE_DISTRICTS]
    by_state = {}
    for row in relationship_rows:
        state_fips = row["GEOID_CD119_20"][:2]
        by_state.setdefault(state_fips, []).append(row)

    district_votes = {}
    for state_rows in by_state.values():
        districts_in_state = {r["GEOID_CD119_20"] for r in state_rows}
        county_district_count = {}
        for r in state_rows:
            county_district_count[r["GEOID_COUNTY_20"]] = county_district_count.get(r["GEOID_COUNTY_20"], 0) + 1

        # Each county counted exactly once, whole or split.
        seen_counties = set()
        state_total = 0
        for r in state_rows:
            fips = r["GEOID_COUNTY_20"]
            if fips in seen_counties:
                continue
            seen_counties.add(fips)
            state_total += county_votes.get(fips, 0)
        fair_share = state_total / len(districts_in_state) if districts_in_state else 0

        whole_county_votes = {geoid: 0 for geoid in districts_in_state}
        split_by_county = {}
        for r in state_rows:
            fips, geoid = r["GEOID_COUNTY_20"], r["GEOID_CD119_20"]
            if county_district_count[fips] == 1:
                whole_county_votes[geoid] += county_votes.get(fips, 0)
            else:
                split_by_county.setdefault(fips, []).append(geoid)

        remaining_quota = {geoid: fair_share - whole_county_votes[geoid] for geoid in districts_in_state}
        state_district_votes = dict(whole_county_votes)
        for fips, geoids in split_by_county.items():
            total = county_votes.get(fips, 0)
            weights = [max(remaining_quota[g], 0) for g in geoids]
            weight_sum = sum(weights)
            if weight_sum <= 0:
                # Every district sharing this county already has at least
                # its fair share from whole counties alone - nothing left to
                # weight by, so split evenly among just these districts
                # rather than assigning it all to one arbitrarily.
                for g in geoids:
                    state_district_votes[g] = state_district_votes.get(g, 0) + total / len(geoids)
            else:
                for g, w in zip(geoids, weights):
                    state_district_votes[g] = state_district_votes.get(g, 0) + total * (w / weight_sum)

        district_votes.update(state_district_votes)

    return {geoid: {"votes": round(votes), "year": year} for geoid, votes in district_votes.items() if votes > 0}


def _house_geoid(state, district_raw):
    prefix = _STATE_FIPS_PREFIX.get(state)
    if prefix is None:
        return None
    if prefix in _AT_LARGE_STATE_FIPS:
        return prefix + "00"
    digits = "".join(ch for ch in str(district_raw) if ch.isdigit())
    if not digits:
        return None
    return prefix + digits.zfill(2)


def house_baseline(rows, year):
    """GEOID -> {demShare, repShare, votes, year} from district-level returns.

    Skips REDISTRICTING_AFFECTED_DISTRICTS entirely (see races.py) - those
    districts get no historical entry, so estimate.js falls back to the flat
    projection for them rather than comparing across changed boundaries.
    """
    votes = {}
    for row in rows:
        if str(row.get("year")) != str(year):
            continue
        party = _party_of(row)
        if party is None:
            continue
        geoid = _house_geoid(row.get("state"), row.get("district"))
        if geoid is None or geoid in REDISTRICTING_AFFECTED_DISTRICTS:
            continue
        bucket = votes.setdefault(geoid, {"dem": 0, "rep": 0})
        bucket[party] += int(float(row.get("candidatevotes") or 0))

    baseline = {}
    for geoid, bucket in votes.items():
        share = _two_party_share(bucket["dem"], bucket["rep"])
        if share:
            baseline[geoid] = {**share, "year": year}
    return baseline


def statewide_baseline(rows, office_contains, year_by_state):
    """State name -> {demShare, repShare, votes, year}, one seat-correct year per
    state (from races.SENATE_LAST_CONTESTED or GOVERNOR_LAST_ELECTED), for
    offices MEDSL only reports at state level.

    Matches state names case-insensitively (some MEDSL files use ALL CAPS,
    e.g. "ARIZONA") but always keys the output by year_by_state's own
    spelling ("Arizona"), since that's what map.html looks up by (the CSV's
    own "State" column, in races.py's casing) - storing the raw file's
    casing instead would silently never match anything client-side.

    Excludes special elections: a state can have two Senate seats up the
    same year if one of them is filling a vacancy (Georgia in 2020 had both
    a regular Perdue-vs-Ossoff race AND a special multi-candidate race for
    Loeffler's seat) - summing both would silently double the state's true
    one-seat vote total. races.py's *_LAST_CONTESTED tables point at a
    specific seat's own last REGULAR election, never a special, so this
    should never accidentally exclude the race we actually want.
    """
    canonical_by_upper = {state.upper(): state for state in year_by_state}
    votes = {}
    for row in rows:
        canonical = canonical_by_upper.get((row.get("state") or "").upper())
        if canonical is None:
            continue
        year = year_by_state[canonical]
        if str(row.get("year")) != str(year):
            continue
        if (row.get("special") or "").strip().upper() == "TRUE":
            continue
        office = (row.get("office") or "").upper()
        if office_contains not in office:
            continue
        party = _party_of(row)
        if party is None:
            continue
        bucket = votes.setdefault(canonical, {"dem": 0, "rep": 0})
        bucket[party] += int(float(row.get("candidatevotes") or 0))

    baseline = {}
    for state, bucket in votes.items():
        share = _two_party_share(bucket["dem"], bucket["rep"])
        if share:
            baseline[state] = {**share, "year": year_by_state[state]}
    return baseline


def _read_rows(path):
    # MEDSL's own Dataverse exports are tab-separated (.tab); the Census
    # Bureau's relationship files are pipe-separated (.txt, and shipped with
    # a UTF-8 BOM - utf-8-sig strips it if present, harmless if not);
    # community mirrors and pre-aggregated wide files are typically
    # comma-separated (.csv) - go by the extension rather than forcing
    # callers to say so.
    suffix = path.suffix.lower()
    delimiter = "\t" if suffix == ".tab" else "|" if suffix == ".txt" else ","
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race", required=True, choices=["president", "senate", "governor", "house"])
    parser.add_argument("--input", required=True, type=Path, help="MEDSL CSV downloaded from Harvard Dataverse")
    parser.add_argument("--counties", type=Path,
                         help="wide president CSV of real county vote totals - required for "
                              "--format house-county-weighted only")
    parser.add_argument("--output", type=Path, help="defaults to historical_<race>.json")
    parser.add_argument("--format", choices=["medsl", "wide", "house-state-apportioned", "house-county-weighted"],
                         default="medsl",
                         help="medsl (default): MEDSL's long format. wide: a pre-aggregated "
                              "one-row-per-county CSV - president only. house-state-apportioned / "
                              "house-county-weighted: house only - see this file's docstring for all four")
    args = parser.parse_args()

    rows = _read_rows(args.input)

    if args.format == "wide":
        if args.race != "president":
            raise SystemExit("--format wide only covers president (no House/Senate/Governor equivalent)")
        baseline = president_baseline_wide(rows)
    elif args.format == "house-state-apportioned":
        if args.race != "house":
            raise SystemExit("--format house-state-apportioned only covers house")
        baseline = house_baseline_state_apportioned(rows, HOUSE_LAST_ELECTED)
    elif args.format == "house-county-weighted":
        if args.race != "house":
            raise SystemExit("--format house-county-weighted only covers house")
        if not args.counties:
            raise SystemExit("--format house-county-weighted requires --counties <wide president CSV>")
        county_votes = {fips: int(float(r.get("total_votes") or 0))
                         for r in _read_rows(args.counties) if (fips := _county_fips(r)) is not None}
        baseline = house_baseline_county_weighted(rows, county_votes, HOUSE_LAST_ELECTED)
    elif args.race == "president":
        baseline = president_baseline(rows, PRESIDENT_LAST_ELECTED)
    elif args.race == "house":
        baseline = house_baseline(rows, HOUSE_LAST_ELECTED)
    elif args.race == "senate":
        baseline = statewide_baseline(rows, "SENATE", SENATE_LAST_CONTESTED)
    else:
        baseline = statewide_baseline(rows, "GOVERNOR", GOVERNOR_LAST_ELECTED)

    output = args.output or Path(__file__).resolve().parent.parent / "static" / f"historical_{args.race}.json"
    output.write_text(json.dumps(baseline, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"wrote {len(baseline)} areas to {output}")


if __name__ == "__main__":
    main()
