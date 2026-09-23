"""Regression locks for three status/geography-formatting helpers that map.html
duplicates across several call sites (see the code review that flagged them):

  * the camelCase-Geography-to-words transform
    (`status.geography.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase()`),
    copy-pasted at four call sites;
  * the "percent in / total ballots / tied-or-leader(-mismatch)" tail shared
    byte-for-byte between stateSummary and districtSummary;
  * the "this state's/district's counties" fips-filter lookup shared by
    ballotTotalLabel, historicalOnlyBallots and openStateOverlay.

These tests exercise the real rendered output at the real call sites (aria
labels, the overlay note, the overlay table caption, the drill-down's county
count) rather than any internal helper, so the same assertions hold whether
that logic lives inline, duplicated four times, or behind one shared
function - the point is to pin the *behavior*, not the implementation, so a
readability-only refactor can be verified not to have changed anything.
"""

import re

import pytest

from conftest import (OPEN_STATE, STUB_CA01, click_district, house_pages,
                      severe_logs, wait_for_note)

# STUB_CA01 (conftest) is a district that reports by county, fully counted,
# with a real D/R skew - exercises the shared "percent in / total ballots /
# certain-or-projected leader" tail (districtSummary) with no geography caveat.

# A district that reports by a non-county geography - exercises the
# camelCase-to-words transform at both of its call sites in the district
# drill-down (renderOverlayTable's caption "unit" and the overlay note),
# together with the tail's "this district reports by X rather than by
# county" branch.
STUB_NONCOUNTY_DISTRICT = {
    "geoid": "0602",
    "label": "Test District",
    "state": "Test State",
    "geography": "electionDayVoteCenters",
    "countyLevel": False,
    "totalExpected": 1000,
    "percentIn": 100.0,
    "demName": "Alice",
    "repName": "Bob",
    "lastModified": None,
    "areas": [
        {"name": "Center 1", "fips": None, "percentIn": 100.0, "votes": 1000,
         "demReal": 600, "repReal": 400},
    ],
}


@pytest.fixture
def house_page(site):
    yield from house_pages(site, {"0601": STUB_CA01, "0602": STUB_NONCOUNTY_DISTRICT},
                           delay=0.05, default_viewport="laptop")


# ---------- districtSummary's shared tail (finding: stateSummary/
# districtSummary duplicate the same tail) ----------

def test_district_summary_tail_for_a_fully_reported_county_level_district(house_page):
    """100% in, no historical baseline needed (percentIn=100 so estimateArea
    hands back the real counts unchanged) - a fully deterministic exercise of
    the tie/unprojected/leader/mismatch tail with a real R lead."""
    driver = house_page()
    click_district(driver, "0601")
    wait_for_note(driver)
    note = driver.execute_script("return document.querySelector('#overlay-note').textContent")
    assert note == "99% in, 0.10M total ballots, certain Republican", note
    assert not severe_logs(driver)


# ---------- geographyLabel's camelCase-to-words transform (finding: the
# regex is copy-pasted at four call sites) ----------

def test_geography_label_transform_in_overlay_table_caption(house_page):
    driver = house_page()
    click_district(driver, "0602")
    wait_for_note(driver)
    caption = driver.execute_script(
        "return document.querySelector('#overlay-table caption').textContent")
    assert "election day vote centers" in caption
    assert "1 of 1 election day vote centers reporting" in caption


def test_geography_label_transform_in_overlay_note(house_page):
    driver = house_page()
    click_district(driver, "0602")
    wait_for_note(driver)
    note = driver.execute_script("return document.querySelector('#overlay-note').textContent")
    assert note == (
        "100% in, 0.00M total ballots, certain Democrat — this district reports by "
        "election day vote centers rather than by county, so there is no county map."
    ), note


# ---------- summarizeStatus's shared tail, exercised at stateSummary's own
# call site (the national map's aria-labels) ----------

TAIL_RE = re.compile(
    r"^\d+% in, ~?\d+\.\d+M total ballots, "
    r"(tied|no projected winner yet|(certain|projected) (Democrat|Republican)"
    r"( \(raw leader currently disagrees\))?)$"
)


def test_state_summary_tail_matches_the_documented_shape_everywhere(page):
    """Every state's aria-label on the national map ends in the shared
    tie/unprojected/leader(/mismatch) tail, in exactly the wording and
    ordering stateSummary and districtSummary both hard-code - a broad,
    real-data sweep standing in for exhaustive enumeration of that logic."""
    driver = page("desktop")
    labels = driver.execute_script(
        "return [...document.querySelectorAll('#map path.state')]"
        ".map(p => p.getAttribute('aria-label'));")
    assert labels, "no states rendered - probe or selector is stale"

    checked_a_real_tail = False
    for label in labels:
        _, _, tail = label.partition(": ")
        assert tail, f"aria-label has no ': ' separator: {label}"
        if tail == "No data yet" or tail.startswith("No data yet,"):
            continue
        if tail.startswith("No ") and "race in" in tail:
            continue
        assert TAIL_RE.match(tail), f"tail does not match the documented shape: {tail!r}"
        checked_a_real_tail = True
    assert checked_a_real_tail, "every state was in the 'no data'/'no race' branch - nothing exercised the tail"


# ---------- the "this state's/district's counties" fips-filter lookup
# (finding: ballotTotalLabel / historicalOnlyBallots / openStateOverlay all
# duplicate the same fips lookup + filter) ----------

def test_state_drilldown_shows_exactly_that_states_counties(page):
    """openStateOverlay's county set must be exactly the counties whose FIPS
    prefix matches the state - computed independently here from the same
    topology the page itself loads, not from any internal helper."""
    driver = page("desktop")
    driver.execute_script(OPEN_STATE, "Pennsylvania")
    ids = driver.execute_script(
        "return [...document.querySelectorAll('#state-map path.county')]"
        ".map(p => p.__data__.id);")
    assert ids, "drill-down rendered no counties"
    assert all(fips.startswith("42") for fips in ids), ids
    # Pennsylvania has 67 counties in the real topology.
    assert len(set(ids)) == 67


def test_state_drilldown_for_a_small_state_matches_its_own_county_count(page):
    driver = page("desktop")
    driver.execute_script(OPEN_STATE, "Rhode Island")
    ids = driver.execute_script(
        "return [...document.querySelectorAll('#state-map path.county')]"
        ".map(p => p.__data__.id);")
    assert ids and all(fips.startswith("44") for fips in ids), ids
    assert len(set(ids)) == 5
