"""House renders districts, not states, and its drill-down is fetched lazily.

Regression coverage for the two ways that could quietly break: grouping CSV
rows by state instead of by district GEOID (which would silently merge every
district of a state into one), and the district drill-down either firing a
request it shouldn't (on load/resize/theme-flip, not just a click) or hanging
forever when the fetch fails.

The drill-down itself talks to server.py's /house-district/<geoid> proxy,
which in production calls real, live NBC data - not something this
deterministic, offline suite can depend on. So these tests run their own tiny
HTTP server that serves the same site plus a stubbed /house-district/<geoid>
route, instead of hitting server.py or NBC at all.
"""

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from conftest import STUB_CA01, VIEWPORTS, click_district, house_pages, severe_logs, wait_for_note


@pytest.fixture
def house_page(site):
    """Like conftest's `page`, but backed by a server that also stubs
    /house-district/0601 - real district drill-down data, a real 404 for
    every other geoid, no network."""
    yield from house_pages(site, {"0601": STUB_CA01}, delay=0.2, default_viewport="desktop")


def test_house_renders_all_435_districts(house_page):
    driver = house_page()
    count = driver.execute_script("return document.querySelectorAll('#map path.state').length")
    assert count == 435
    assert not severe_logs(driver)


def test_house_districts_are_clickable_and_focusable(house_page):
    driver = house_page()
    tabindex = driver.execute_script(
        "return document.querySelector('#map path.state').getAttribute('tabindex')")
    assert tabindex == "0"


def test_district_click_shows_loading_state_immediately(house_page):
    """The overlay must open with a loading note synchronously - the fetch
    hasn't resolved yet, so there's nothing else to show."""
    driver = house_page()
    click_district(driver, "0601")
    assert driver.execute_script("return document.querySelector('#overlay').classList.contains('open')")
    note = driver.execute_script("return document.querySelector('#overlay-note').textContent")
    assert "loading" in note.lower()


def test_district_drilldown_renders_fetched_counties(house_page):
    driver = house_page()
    click_district(driver, "0601")

    wait_for_note(driver)

    assert driver.execute_script("return document.querySelector('#overlay-title').textContent") == "California District 1"
    assert driver.execute_script("return document.querySelectorAll('#state-map path.county').length") == 2
    assert driver.execute_script("return document.querySelectorAll('#overlay-table tbody tr').length") == 2
    assert driver.execute_script("return document.querySelector('#overlay-summary').innerHTML.length") > 0
    assert not severe_logs(driver)


def test_district_drilldown_shows_error_when_the_fetch_fails(house_page):
    """0602 isn't stubbed, so the server 404s - the panel must say so instead
    of hanging on "Loading..." forever."""
    driver = house_page()
    click_district(driver, "0602")

    wait_for_note(driver)
    note = driver.execute_script("return document.querySelector('#overlay-note').textContent")
    assert "could not load" in note.lower()


def test_closing_and_opening_a_different_district_does_not_show_stale_data(house_page):
    """Regression guard for the fetch-token cancellation: a slow first fetch
    landing after the user already moved on must not clobber what's on screen."""
    driver = house_page()
    click_district(driver, "0601")
    driver.execute_script("document.querySelector('#overlay-close').click();")
    assert not driver.execute_script("return document.querySelector('#overlay').classList.contains('open')")

    click_district(driver, "0602")
    wait_for_note(driver)
    note = driver.execute_script("return document.querySelector('#overlay-note').textContent")
    assert "could not load" in note.lower()


def test_house_district_label_names_state_and_district(house_page):
    driver = house_page()
    label = driver.execute_script("return document.querySelector('#map path.state').getAttribute('aria-label')")
    assert "District" in label or "at Large" in label


def test_switching_back_to_a_statewide_race_restores_the_drill_down(house_page):
    driver = house_page()
    driver.execute_script("""
        const btn = [...document.querySelectorAll('#race-tabs button')].find(b => b.textContent.includes('President'));
        btn.click();""")
    WebDriverWait(driver, 25).until(
        lambda d: d.execute_script("return document.querySelectorAll('#map path.state').length === 51"))
    driver.execute_script("""
        const p = [...document.querySelectorAll('#map path.state')]
          .find(p => p.getAttribute('aria-label').startsWith('Pennsylvania'));
        p.dispatchEvent(new MouseEvent('click', {bubbles: true}));""")
    assert driver.execute_script("return document.querySelector('#overlay').classList.contains('open')")
    # And it must be the real state drill-down, not stuck showing a district
    # "loading"/error note left over from before the switch.
    assert driver.execute_script("return document.querySelectorAll('#state-map path.county').length") > 2


@pytest.mark.parametrize("viewport", [v for v in VIEWPORTS if v != "landscape"])
def test_house_tab_fits_and_has_no_console_errors(house_page, viewport):
    """Adding a fourth tab must not push anything below the fold - see the
    tablet/phone tab-grid fix this required in map.html. Landscape phone is
    excluded here the same way test_layout.py excludes it: the map and chrome
    together genuinely don't fit that viewport for any race."""
    driver = house_page(viewport)
    overflow = driver.execute_script(
        "const e = document.documentElement; return e.scrollHeight - e.clientHeight;")
    assert overflow <= 0, f"page scrolls vertically by {overflow}px on the House tab"
    assert not severe_logs(driver)
