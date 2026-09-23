"""Config for the different NBC race types this project can scrape/visualize.

NBC uses the same page template for every top-of-ticket, statewide race:
president, Senate, and governor pages all share the same
data-testid="county-row" structure and {race}-results-* element ids (just
"president"/"senate"/"governor" swapped in) - confirmed by inspecting both
the presidential pages and archived 2024 Senate/governor results pages
directly.

House is a different shape: 435 single-seat districts, all always in play,
rather than a subset of 50 states. It is scraped and rendered differently
(one national payload, already broken down by district - see
nbc_api.house_results() - joined against district boundaries instead of
county boundaries) but shares the same CSV schema and extrapolation math as
the other three. This pass covers district-level results only; a
per-district county drill-down (mirroring the state -> county drill-down the
other races have) would need ~435 more page fetches and is deferred.

Two different years matter, and conflating them was a real bug:
  - `election_year` is the election we are REPORTING ON. It is what the UI
    shows and never changes until that election is over.
  - `nbc_cycle` is the year in NBC's URL path (/politics/<nbc_cycle>-elections/...).
    NBC mints a path per election *once results exist*, so today the only
    cycle that resolves for all three races is "2024" - 2026 and 2028 both
    404 (verified 2026-09-17). Flip `nbc_cycle` on election night.

Each race's `weights` dict is both "how much is a win in this state worth"
(electoral votes for president, one seat for Senate/governor) and "which
states are actually in play this cycle" - most states have no Senate or
governor race in any given cycle.
"""

import json
from pathlib import Path

# 2020-census apportionment, effective for the 2024 and 2028 presidential
# elections (verified against archives.gov/electoral-college/allocation;
# sums to 538).
ELECTORAL_VOTES = {
    "Alabama": 9, "Alaska": 3, "Arizona": 11, "Arkansas": 6, "California": 54,
    "Colorado": 10, "Connecticut": 7, "Delaware": 3, "District of Columbia": 3,
    "Florida": 30, "Georgia": 16, "Hawaii": 4, "Idaho": 4, "Illinois": 19,
    "Indiana": 11, "Iowa": 6, "Kansas": 6, "Kentucky": 8, "Louisiana": 8,
    "Maine": 4, "Maryland": 10, "Massachusetts": 11, "Michigan": 15,
    "Minnesota": 10, "Mississippi": 6, "Missouri": 10, "Montana": 4,
    "Nebraska": 5, "Nevada": 6, "New Hampshire": 4, "New Jersey": 14,
    "New Mexico": 5, "New York": 28, "North Carolina": 16, "North Dakota": 3,
    "Ohio": 17, "Oklahoma": 7, "Oregon": 8, "Pennsylvania": 19,
    "Rhode Island": 4, "South Carolina": 9, "South Dakota": 3, "Tennessee": 11,
    "Texas": 40, "Utah": 6, "Vermont": 3, "Virginia": 13, "Washington": 12,
    "West Virginia": 4, "Wisconsin": 10, "Wyoming": 3,
}

# States with a 2026 U.S. Senate race: the 33 regularly-scheduled Class 2
# seats plus the Florida and Ohio special elections (Rubio's and Vance's
# vacated seats). Verified against Wikipedia's "2026 United States Senate
# elections" (35 seats total).
SENATE_STATES_2026 = [
    "Alabama", "Alaska", "Arkansas", "Colorado", "Delaware", "Georgia",
    "Idaho", "Illinois", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Montana",
    "Nebraska", "New Hampshire", "New Jersey", "New Mexico", "North Carolina",
    "Oklahoma", "Oregon", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Virginia", "West Virginia", "Wyoming",
    "Florida", "Ohio",
]

# States with a 2026 gubernatorial race. Verified against Wikipedia's "2026
# United States gubernatorial elections" (36 states; DC/territories excluded
# since they're outside our county-map data and DC has no governorship).
GOVERNOR_STATES_2026 = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois",
    "Iowa", "Kansas", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Nebraska", "Nevada", "New Hampshire", "New Mexico",
    "New York", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Vermont",
    "Wisconsin", "Wyoming",
]

# (district GEOID) -> {"state": ..., "label": ..., and "redrawnSince2024": true
# where the 2026 lines differ from 2024's}. Built once by
# scripts/build_district_topology.sh from the same Census shapefile the district map
# boundaries come from, so the two can never disagree about which 435
# districts exist.
HOUSE_DISTRICTS = json.loads((Path(__file__).parent / "static" / "house_districts.json").read_text(encoding="utf-8"))

# Governors mostly serve 4-year terms, but not on a synced clock - New
# Hampshire and Vermont re-elect theirs every 2 years, and a handful of
# states vote in odd years (Virginia, New Jersey, Kentucky, Mississippi,
# Louisiana - none of which are up in 2026, so they don't appear here at
# all). "Latest year this state actually elected a governor before 2026" is
# therefore per-state, not a flat "-4" - build_historical_baseline.py needs
# the real year to know which MEDSL file to pull for each state's baseline.
_GOVERNOR_TERM_LENGTH = {"New Hampshire": 2, "Vermont": 2}
GOVERNOR_LAST_ELECTED = {
    state: 2026 - _GOVERNOR_TERM_LENGTH.get(state, 4) for state in GOVERNOR_STATES_2026
}

# U.S. Senate seats serve 6-year terms, so a regular seat up in 2026 was last
# contested in 2020. Florida's and Ohio's 2026 races are specials filling
# Rubio's and Vance's vacated seats - both were last *regularly* elected in
# 2022 (Rubio's Class 3 seat, Vance's Class 3 seat), so 2020 would compare
# against a different election than the one that actually produced today's
# incumbent. Verified against Wikipedia's "2026 United States Senate
# elections" and each senator's own election history.
_SENATE_LAST_CONTESTED_OVERRIDE = {"Florida": 2022, "Ohio": 2022}
SENATE_LAST_CONTESTED = {
    state: _SENATE_LAST_CONTESTED_OVERRIDE.get(state, 2020) for state in SENATE_STATES_2026
}

# Districts whose 2026 lines differ from the ones their 2024 election used
# (nine states redrew - see scripts/build_district_geometry.py, which sets
# the flag by comparing the Census Bureau's 119th- and 120th-Congress lines
# district by district). Their 2024 result describes different territory, so
# it is not a swing baseline: build_historical_baseline.py omits them, and
# estimate.js falls back to the flat projection for them.
REDRAWN_SINCE_2024 = {geoid for geoid, info in HOUSE_DISTRICTS.items() if info.get("redrawnSince2024")}

# Sanity-check the hand-maintained tables: right totals, no duplicates, and
# every state spelled the way ELECTORAL_VOTES (and therefore the map's
# topology join) spells it.
assert sum(ELECTORAL_VOTES.values()) == 538
for _states, _expected in ((SENATE_STATES_2026, 35), (GOVERNOR_STATES_2026, 36)):
    assert len(_states) == _expected, f"expected {_expected} states, got {len(_states)}"
    assert len(set(_states)) == len(_states), "duplicate state"
    assert set(_states) <= set(ELECTORAL_VOTES), f"unknown state name: {set(_states) - set(ELECTORAL_VOTES)}"
assert len(HOUSE_DISTRICTS) == 435, f"expected 435 House districts, got {len(HOUSE_DISTRICTS)}"
assert set(GOVERNOR_LAST_ELECTED) == set(GOVERNOR_STATES_2026)
assert all(1900 < year < 2026 for year in GOVERNOR_LAST_ELECTED.values())
assert set(SENATE_LAST_CONTESTED) == set(SENATE_STATES_2026)
assert all(1900 < year < 2026 for year in SENATE_LAST_CONTESTED.values())
assert REDRAWN_SINCE_2024 <= set(HOUSE_DISTRICTS)

RACES = {
    "president": {
        "label": "President",
        "nbc_slug": "president",
        "election_year": 2028,  # Tue 7 Nov 2028
        "nbc_cycle": "2024",    # NBC has no 2028 pages yet (404); bump on election night
        "weights": ELECTORAL_VOTES,
    },
    "senate": {
        "label": "Senate",
        "nbc_slug": "senate",
        "election_year": 2026,  # Tue 3 Nov 2026
        "nbc_cycle": "2024",    # /politics/2026-elections/senate-results is a 404 today
        "weights": {state: 1 for state in SENATE_STATES_2026},
    },
    "governor": {
        "label": "Governor",
        "nbc_slug": "governor",
        "election_year": 2026,  # Tue 3 Nov 2026
        "nbc_cycle": "2024",    # /politics/2026-elections/governor-results is a 404 today
        "weights": {state: 1 for state in GOVERNOR_STATES_2026},
    },
    "house": {
        "label": "House",
        "nbc_slug": "house",
        "election_year": 2026,  # Tue 3 Nov 2026
        "nbc_cycle": "2024",    # /politics/2026-elections/house-results is a 404 today
        # Every one of the 435 seats is up every cycle, keyed by district
        # GEOID rather than state name - a different key space than the other
        # three races' weights, which map.html has to know about.
        "weights": {geoid: 1 for geoid in HOUSE_DISTRICTS},
    },
}

# President and House are both synced everywhere (every House seat is up
# every 2 years, all 50 states at once), so these are flat offsets rather
# than per-state tables - unlike Senate/Governor, which are not.
PRESIDENT_LAST_ELECTED = RACES["president"]["election_year"] - 4
HOUSE_LAST_ELECTED = RACES["house"]["election_year"] - 2


# The CSV schema every producer writes and map.html reads.
#
# Party columns are fixed regardless of who is running - NBC tags every
# candidate with a party - with the real names carried alongside.
#
# "Area" rather than "County" because NBC does not report every state by
# county: eight report by township, municipality, ward or legislative
# district. FIPS is the county code where one applies and empty where one does
# not, and Geography says which case a row is, so the map can join exactly
# instead of guessing from names and can be honest about the states it cannot
# break down.
# No "Predicted" columns: that used to be a flat extrapolation
# (Real * 100/PercentIn) computed here and written to the CSV, but the
# projection math (flat or historical-swing) now runs client-side in
# estimate.js - see map.html. This file writes real counts only.
CSV_HEADER = [
    "State", "Area", "FIPS", "Geography", "State Total Expected", "Total Votes", "Percent In",
    "Democrat Real", "Republican Real",
    "Democrat Name", "Republican Name",
]


def race_files(race):
    """Where a race's output lives.

    President keeps the unsuffixed name it had before multi-race support, so
    existing files and muscle memory still work.
    """
    suffix = "" if race == "president" else f"_{race}"
    return {"output_csv": Path(f"raw_data{suffix}.csv")}
