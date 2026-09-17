"""The drill-down must always be escapable, at every viewport.

On a phone the panel used to fill the whole screen, which left the close
button as the only exit - there was no backdrop to tap. That is a trap on the
device where precise tapping is hardest.
"""

import pytest
from selenium.webdriver.common.keys import Keys

from conftest import PHONES, VIEWPORTS

OPEN_STATE = """
const p = [...document.querySelectorAll('#map path.state')]
  .find(p => p.getAttribute('aria-label').startsWith(arguments[0]));
p.dispatchEvent(new MouseEvent('click', {bubbles: true}));"""

IS_OPEN = "return document.querySelector('#overlay').classList.contains('open');"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_backdrop_is_reachable_and_closes(page, viewport):
    driver = page(viewport)
    driver.execute_script(OPEN_STATE, "Pennsylvania")
    assert driver.execute_script(IS_OPEN)

    closed = driver.execute_script("""
      const panel = document.querySelector('#overlay-panel').getBoundingClientRect();
      const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
      // Find a point of backdrop outside the panel. If there is none, the user
      // is trapped with only the close button.
      const spots = [[vw / 2, panel.top / 2], [vw / 2, (panel.bottom + vh) / 2],
                     [panel.left / 2, vh / 2], [(panel.right + vw) / 2, vh / 2]];
      for (const [x, y] of spots) {
        if (x < 0 || y < 0 || x > vw || y > vh) continue;
        const el = document.elementFromPoint(x, y);
        if (el && el.id === 'overlay') {
          el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          return {found: true, at: [Math.round(x), Math.round(y)]};
        }
      }
      return {found: false};""")
    assert closed["found"], "no tappable backdrop anywhere - the close button is the only way out"
    assert not driver.execute_script(IS_OPEN), "tapping the backdrop did not close the drill-down"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_escape_closes_and_restores_focus(page, viewport):
    driver = page(viewport)
    driver.execute_script("""
      const p = [...document.querySelectorAll('#map path.state')]
        .find(p => p.getAttribute('aria-label').startsWith('Illinois'));
      p.focus(); p.dispatchEvent(new MouseEvent('click', {bubbles: true}));""")
    assert driver.execute_script(IS_OPEN)
    driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    assert not driver.execute_script(IS_OPEN)
    assert driver.execute_script(
        "return (document.activeElement.getAttribute('aria-label') || '').startsWith('Illinois');"
    ), "focus was dropped instead of returning to the state you came from"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_dialog_is_announced_as_a_dialog(page, viewport):
    driver = page(viewport)
    driver.execute_script(OPEN_STATE, "Georgia")
    info = driver.execute_script("""
      const panel = document.querySelector('#overlay-panel');
      const labelled = document.getElementById(panel.getAttribute('aria-labelledby'));
      return {role: panel.getAttribute('role'), modal: panel.getAttribute('aria-modal'),
              label: labelled && labelled.textContent,
              focused: document.activeElement === panel,
              backgroundInert: document.querySelector('.page').inert};""")
    assert info["role"] == "dialog" and info["modal"] == "true"
    assert info["label"] == "Georgia", "aria-labelledby does not resolve to the state name"
    assert info["focused"], "focus was not moved into the dialog"
    assert info["backgroundInert"], "background is still reachable behind a modal"


@pytest.mark.parametrize("viewport", PHONES)
def test_phone_sheet_leaves_room_to_tap_past_it(page, viewport):
    driver = page(viewport)
    driver.execute_script(OPEN_STATE, "Pennsylvania")
    gap = driver.execute_script(
        "return document.querySelector('#overlay-panel').getBoundingClientRect().top;")
    assert gap >= 40, f"only {gap:.0f}px of backdrop above the sheet - too small to tap reliably"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_background_is_frozen_while_the_drilldown_is_open(page, viewport):
    """With the sheet open it is the sheet that scrolls, not the page behind it.

    Asserted on the computed styles rather than by scripting a scroll, because
    window.scrollBy drives the scrolling element directly and slips past
    overflow:hidden - it would pass a broken build and fail a working one.
    """
    driver = page(viewport)
    driver.execute_script(OPEN_STATE, "Pennsylvania")
    state = driver.execute_script("""
      return {html: getComputedStyle(document.documentElement).overflowY,
              body: getComputedStyle(document.body).overflowY,
              chaining: getComputedStyle(document.querySelector('#overlay-panel')).overscrollBehaviorY};""")
    assert state["html"] == "hidden" and state["body"] == "hidden", f"background still scrollable: {state}"
    assert state["chaining"] == "contain", "sheet scrolling chains into the page behind it"

    driver.execute_script("document.querySelector('#overlay-close').click();")
    restored = driver.execute_script(
        "return getComputedStyle(document.documentElement).overflowY;")
    assert restored != "hidden", "page left permanently unscrollable after closing"
