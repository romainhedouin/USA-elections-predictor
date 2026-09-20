"""Unit tests for scripts/build_historical_baseline.py's pure join/aggregation
logic. Plain pytest, no Selenium/browser - unlike the rest of tests/, this
module has no rendering to check.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import build_historical_baseline as bhb
import races


def test_president_baseline_aggregates_two_party_share_per_fips():
    rows = [
        {"year": "2024", "county_fips": "06037", "party": "DEMOCRAT", "candidatevotes": "700"},
        {"year": "2024", "county_fips": "06037", "party": "REPUBLICAN", "candidatevotes": "300"},
        {"year": "2020", "county_fips": "06037", "party": "DEMOCRAT", "candidatevotes": "999"},  # wrong year
    ]
    baseline = bhb.president_baseline(rows, 2024)
    assert baseline == {"06037": {"demShare": 0.7, "repShare": 0.3, "votes": 1000, "year": 2024}}


def test_president_baseline_skips_counties_with_no_major_party_votes():
    rows = [{"year": "2024", "county_fips": "06037", "party": "GREEN", "candidatevotes": "50"}]
    assert bhb.president_baseline(rows, 2024) == {}


def test_house_baseline_skips_redistricting_affected_states():
    # Pick a real GEOID from an affected state to prove the skip is live,
    # not just a name match.
    affected_geoid = next(iter(races.REDISTRICTING_AFFECTED_DISTRICTS))
    affected_state = races.HOUSE_DISTRICTS[affected_geoid]["state"]
    affected_district_num = affected_geoid[2:].lstrip("0") or "0"

    rows = [
        {"year": "2024", "state": affected_state, "district": affected_district_num,
         "party": "DEMOCRAT", "candidatevotes": "100"},
        {"year": "2024", "state": affected_state, "district": affected_district_num,
         "party": "REPUBLICAN", "candidatevotes": "100"},
    ]
    baseline = bhb.house_baseline(rows, 2024)
    assert affected_geoid not in baseline


def test_house_baseline_computes_geoid_for_a_normal_district():
    # Find a state with more than one district, so we exercise the
    # zero-padded (not at-large) code path.
    from collections import Counter
    counts = Counter(info["state"] for info in races.HOUSE_DISTRICTS.values())
    multi_district_state = next(state for state, n in counts.items() if n > 1
                                 and state not in {"Alabama", "Georgia", "Louisiana", "New York", "North Carolina"})
    geoid = next(g for g, info in races.HOUSE_DISTRICTS.items() if info["state"] == multi_district_state)
    district_num = str(int(geoid[2:]))

    rows = [
        {"year": "2024", "state": multi_district_state, "district": district_num,
         "party": "DEMOCRAT", "candidatevotes": "600"},
        {"year": "2024", "state": multi_district_state, "district": district_num,
         "party": "REPUBLICAN", "candidatevotes": "400"},
    ]
    baseline = bhb.house_baseline(rows, 2024)
    assert baseline[geoid] == {"demShare": 0.6, "repShare": 0.4, "votes": 1000, "year": 2024}


def test_statewide_baseline_uses_the_seat_correct_year_per_state():
    # Florida's Senate seat is a 2022 special-derived comparator; a generic
    # state uses the 2020 default - see races.SENATE_LAST_CONTESTED.
    rows = [
        {"year": "2022", "state": "Florida", "office": "US SENATE", "party": "DEMOCRAT", "candidatevotes": "40"},
        {"year": "2022", "state": "Florida", "office": "US SENATE", "party": "REPUBLICAN", "candidatevotes": "60"},
        {"year": "2020", "state": "Florida", "office": "US SENATE", "party": "DEMOCRAT", "candidatevotes": "999"},
        {"year": "2020", "state": "Alabama", "office": "US SENATE", "party": "DEMOCRAT", "candidatevotes": "35"},
        {"year": "2020", "state": "Alabama", "office": "US SENATE", "party": "REPUBLICAN", "candidatevotes": "65"},
    ]
    baseline = bhb.statewide_baseline(rows, "SENATE", races.SENATE_LAST_CONTESTED)
    assert baseline["Florida"] == {"demShare": 0.4, "repShare": 0.6, "votes": 100, "year": 2022}
    assert baseline["Alabama"] == {"demShare": 0.35, "repShare": 0.65, "votes": 100, "year": 2020}


def test_statewide_baseline_ignores_states_not_in_the_year_table():
    rows = [{"year": "2020", "state": "Utah", "office": "US SENATE", "party": "DEMOCRAT", "candidatevotes": "1"}]
    assert bhb.statewide_baseline(rows, "SENATE", races.SENATE_LAST_CONTESTED) == {}


def test_statewide_baseline_matches_state_names_case_insensitively():
    # The real MEDSL 1976-2024 Senate file uses ALL CAPS state names
    # ("ARIZONA") - this used to silently match nothing against
    # SENATE_LAST_CONTESTED's Title Case keys until this was fixed.
    rows = [
        {"year": "2020", "state": "ALABAMA", "office": "US SENATE", "party": "DEMOCRAT", "candidatevotes": "35"},
        {"year": "2020", "state": "ALABAMA", "office": "US SENATE", "party": "REPUBLICAN", "candidatevotes": "65"},
    ]
    baseline = bhb.statewide_baseline(rows, "SENATE", races.SENATE_LAST_CONTESTED)
    # Keyed by races.py's own casing ("Alabama"), not the file's ("ALABAMA") -
    # that's what map.html looks up by by (the CSV's own "State" column).
    assert baseline == {"Alabama": {"demShare": 0.35, "repShare": 0.65, "votes": 100, "year": 2020}}


def test_party_of_falls_back_to_party_simplified_and_party_detailed():
    # Newer MEDSL releases (e.g. the 1976-2024 senate file) use
    # "party_simplified"/"party_detailed" instead of "party".
    assert bhb._party_of({"party_simplified": "DEMOCRAT"}) == "dem"
    assert bhb._party_of({"party_detailed": "REPUBLICAN-CONSERVATIVE"}) == "rep"
    assert bhb._party_of({"party": "DEMOCRAT", "party_simplified": "OTHER"}) == "dem"  # "party" wins if present
    assert bhb._party_of({"party_simplified": "OTHER"}) is None
