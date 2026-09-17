"""Layout invariants that must hold at every supported viewport.

These are the things that make the page feel broken rather than wrong: content
running off the side, the map cropped, a dialog you cannot scroll or escape.
"""

import pytest

from conftest import PHONES, VIEWPORTS, severe_logs

OPEN_STATE = """
const p = [...document.querySelectorAll('#map path.state')]
  .find(p => p.getAttribute('aria-label').startsWith(arguments[0]));
p.dispatchEvent(new MouseEvent('click', {bubbles: true}));"""


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_no_horizontal_overflow(page, viewport):
    driver = page(viewport)
    overflow = driver.execute_script(
        "return document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0, f"page scrolls sideways by {overflow}px"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_no_horizontal_overflow_with_drilldown_open(page, viewport):
    driver = page(viewport)
    driver.execute_script(OPEN_STATE, "Texas")  # widest state in the set
    overflow = driver.execute_script("""
      const panel = document.querySelector('#overlay-panel');
      return Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth,
                      panel.scrollWidth - panel.clientWidth);""")
    assert overflow <= 0, f"drill-down scrolls sideways by {overflow}px"


@pytest.mark.parametrize("viewport", [v for v in VIEWPORTS if v != "landscape"])
def test_map_and_legend_fit_on_one_screen(page, viewport):
    """A landscape phone genuinely cannot fit a 1.6:1 map plus chrome, so it is
    excluded rather than asserted loosely - everything else must fit."""
    driver = page(viewport)
    slack = driver.execute_script("""
      return document.querySelector('#legend').getBoundingClientRect().bottom
             - document.documentElement.clientHeight;""")
    assert slack <= 8, f"map+legend overflow the first screen by {slack:.0f}px"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_map_keeps_its_aspect_ratio(page, viewport):
    driver = page(viewport)
    ratio = driver.execute_script("""
      const r = document.querySelector('#map').getBoundingClientRect();
      return r.width / r.height;""")
    assert abs(ratio - 975 / 610) < 0.02, f"map is distorted: {ratio:.3f} vs {975/610:.3f}"


@pytest.mark.parametrize("viewport", PHONES)
def test_touch_targets_are_big_enough(page, viewport):
    """Anything tappable should clear ~44px, the usual accessibility floor."""
    driver = page(viewport)
    driver.execute_script(OPEN_STATE, "Georgia")
    small = driver.execute_script("""
      const out = [];
      for (const el of document.querySelectorAll('#race-tabs button, #palette-toggle, #theme-toggle, #overlay-close')) {
        const r = el.getBoundingClientRect();
        if (r.width && r.height && (r.width < 44 || r.height < 44))
          out.push({id: el.id || el.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height)});
      }
      return out;""")
    assert not small, f"tap targets under 44px: {small}"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
@pytest.mark.parametrize("theme,palette", [("light", None), ("dark", None),
                                           ("light", "cvd"), ("dark", "cvd")])
def test_renders_cleanly_in_every_theme_and_palette(page, viewport, theme, palette):
    driver = page(viewport, theme=theme, palette=palette)
    state = driver.execute_script("""
      const states = [...document.querySelectorAll('#map path.state')];
      return {count: states.length,
              unfilled: states.filter(s => !s.getAttribute('fill')).length,
              error: document.querySelector('#error').classList.contains('shown')};""")
    assert state["count"] == 51, f"expected 51 states, drew {state['count']}"
    assert state["unfilled"] == 0
    assert not state["error"], "the page showed its error banner"
    assert not severe_logs(driver)


@pytest.mark.parametrize("viewport", [v for v in VIEWPORTS if v != "landscape"])
def test_page_does_not_scroll_vertically(page, viewport):
    """The layout sizes itself to the viewport, so a scrollbar means something
    overflowed - usually padding or a margin nobody counted, which looks like a
    bug even though every element is individually fine."""
    driver = page(viewport)
    overflow = driver.execute_script(
        "const e = document.documentElement; return e.scrollHeight - e.clientHeight;")
    assert overflow <= 0, f"page scrolls vertically by {overflow}px despite being sized to fit"


def test_page_works_without_injected_config(page):
    """Served statically there is no window.__config, and the page must still
    come up on its own - which is exactly how these tests serve it."""
    driver = page("laptop")
    state = driver.execute_script("""
      const on = [...document.querySelectorAll('#race-tabs button')]
        .find(b => b.getAttribute('aria-pressed') === 'true');
      return {injected: window.__config === undefined,
              landed: on ? on.textContent : null,
              states: document.querySelectorAll('#map path.state').length};""")
    assert state["injected"], "the static fixture should have no injected config"
    assert state["landed"].startswith("President"), f"fell back wrongly: {state['landed']}"
    assert state["states"] == 51
