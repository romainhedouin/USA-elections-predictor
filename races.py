"""Config for the different NBC race types this project can scrape/visualize.

NBC uses the same page template for every top-of-ticket, statewide race:
president, Senate, and governor pages all share the same
data-testid="county-row" structure and {race}-results-* element ids (just
"president"/"senate"/"governor" swapped in) - confirmed by inspecting both
the presidential pages and archived 2024 Senate/governor results pages
directly. House is not included: its 435 districts need per-district
boundaries and 435 pages to scrape, a different shape of problem than these
three statewide races.

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

# Sanity-check the hand-maintained tables: right totals, no duplicates, and
# every state spelled the way ELECTORAL_VOTES (and therefore the map's
# topology join) spells it.
assert sum(ELECTORAL_VOTES.values()) == 538
for _states, _expected in ((SENATE_STATES_2026, 35), (GOVERNOR_STATES_2026, 36)):
    assert len(_states) == _expected, f"expected {_expected} states, got {len(_states)}"
    assert len(set(_states)) == len(_states), "duplicate state"
    assert set(_states) <= set(ELECTORAL_VOTES), f"unknown state name: {set(_states) - set(ELECTORAL_VOTES)}"

RACES = {
    "president": {
        "label": "President",
        "unit": "electoral vote",
        "nbc_slug": "president",
        "election_year": 2028,  # Tue 7 Nov 2028
        "nbc_cycle": "2024",    # NBC has no 2028 pages yet (404); bump on election night
        "hub_h2": "All Presidential races",  # NBC uses the adjective form only for president
        "weights": ELECTORAL_VOTES,
    },
    "senate": {
        "label": "Senate",
        "unit": "seat",
        "nbc_slug": "senate",
        "election_year": 2026,  # Tue 3 Nov 2026
        "nbc_cycle": "2024",    # /politics/2026-elections/senate-results is a 404 today
        "hub_h2": "All Senate races",
        "weights": {state: 1 for state in SENATE_STATES_2026},
    },
    "governor": {
        "label": "Governor",
        "unit": "governorship",
        "nbc_slug": "governor",
        "election_year": 2026,  # Tue 3 Nov 2026
        "nbc_cycle": "2024",    # /politics/2026-elections/governor-results is a 404 today
        "hub_h2": "All Governor races",
        "weights": {state: 1 for state in GOVERNOR_STATES_2026},
    },
}


# The CSV schema every producer writes and map.html reads. Party columns are
# fixed regardless of who is running - NBC tags each candidate row with its
# party, so the scraper can resolve it - with the real names carried alongside.
CSV_HEADER = [
    "State", "County", "State Total Expected", "Total Votes", "Percent In",
    "Democrat Real", "Republican Real", "Democrat Predicted", "Republican Predicted",
    "Democrat Name", "Republican Name",
]


def race_files(race):
    """Where a race's inputs and outputs live.

    President keeps the unsuffixed names it had before multi-race support, so
    existing files and muscle memory still work. The scrape cache is always
    per-race (states/<race>/<state>/) so runs can't overwrite each other.
    """
    suffix = "" if race == "president" else f"_{race}"
    return {
        "states_dir": Path("states") / race,
        "nbc_states_file": Path(f"nbc_states{suffix}.json"),
        "output_csv": Path(f"raw_data{suffix}.csv"),
    }
