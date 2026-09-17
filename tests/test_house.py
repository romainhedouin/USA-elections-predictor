"""House renders districts, not states, and (this pass) has no drill-down.

Regression coverage for the two ways that could quietly break: grouping CSV
rows by state instead of by district GEOID (which would silently merge every
district of a state into one), and the district tab picking up the
state-drill-down click handler it isn't built for yet.
"""

import pytest

from conftest import VIEWPORTS, severe_logs

OPEN_HOUSE_TAB = """
const btn = [...document.querySelectorAll('#race-tabs button')].find(b => b.textContent.includes('House'));
btn.click();"""


def open_house(driver):
    driver.execute_script(OPEN_HOUSE_TAB)
    from selenium.webdriver.support.ui import WebDriverWait
    WebDriverWait(driver, 25).until(
        lambda d: d.execute_script(
            "return document.querySelector('#race-tabs button[aria-pressed=\"true\"]')"
            ".textContent.startsWith('House')"))


def test_house_renders_all_435_districts(page):
    driver = page("desktop")
    open_house(driver)
    count = driver.execute_script("return document.querySelectorAll('#map path.state').length")
    assert count == 435
    assert not severe_logs(driver)


def test_house_districts_are_not_clickable(page):
    """No per-district drill-down yet - a click must not try to open one."""
    driver = page("desktop")
    open_house(driver)
    tabindex = driver.execute_script(
        "return document.querySelector('#map path.state').getAttribute('tabindex')")
    assert tabindex is None

    driver.execute_script("""
        const p = document.querySelector('#map path.state');
        p.dispatchEvent(new MouseEvent('click', {bubbles: true}));""")
    assert not driver.execute_script("return document.querySelector('#overlay').classList.contains('open')")


def test_house_district_label_names_state_and_district(page):
    driver = page("desktop")
    open_house(driver)
    label = driver.execute_script("return document.querySelector('#map path.state').getAttribute('aria-label')")
    assert "District" in label or "at Large" in label


def test_switching_back_to_a_statewide_race_restores_the_drill_down(page):
    """The district tab must not leave the click handler permanently disabled
    for the races that do have one."""
    driver = page("desktop")
    open_house(driver)
    driver.execute_script("""
        const btn = [...document.querySelectorAll('#race-tabs button')].find(b => b.textContent.includes('President'));
        btn.click();""")
    from selenium.webdriver.support.ui import WebDriverWait
    WebDriverWait(driver, 25).until(
        lambda d: d.execute_script("return document.querySelectorAll('#map path.state').length === 51"))
    driver.execute_script("""
        const p = [...document.querySelectorAll('#map path.state')]
          .find(p => p.getAttribute('aria-label').startsWith('Pennsylvania'));
        p.dispatchEvent(new MouseEvent('click', {bubbles: true}));""")
    assert driver.execute_script("return document.querySelector('#overlay').classList.contains('open')")


@pytest.mark.parametrize("viewport", [v for v in VIEWPORTS if v != "landscape"])
def test_house_tab_fits_and_has_no_console_errors(page, viewport):
    """Adding a fourth tab must not push anything below the fold - see the
    tablet/phone tab-grid fix this required in map.html. Landscape phone is
    excluded here the same way test_layout.py excludes it: the map and chrome
    together genuinely don't fit that viewport for any race."""
    driver = page(viewport)
    open_house(driver)
    overflow = driver.execute_script(
        "const e = document.documentElement; return e.scrollHeight - e.clientHeight;")
    assert overflow <= 0, f"page scrolls vertically by {overflow}px on the House tab"
    assert not severe_logs(driver)
