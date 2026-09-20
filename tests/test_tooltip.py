"""The tooltip must never leave the viewport.

Regression test for a real bug: the tooltip flipped to the left of the cursor
when it would have overflowed on the right, but nothing clamped the result, so
on a narrow screen a wide tooltip simply overflowed the OTHER edge instead.
It showed up as a half-cut-off black box when tapping counties in the
drill-down on a phone, which is exactly where the tooltip is at its tallest
(four lines) and the screen at its narrowest.
"""

import pytest

from conftest import OPEN_STATE, VIEWPORTS, severe_logs

# Hover a county and report where the tooltip landed, for every county in the
# open drill-down. Centroids, so the pointer is always inside the shape.
PROBE_COUNTIES = """
const out = [];
const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
for (const county of document.querySelectorAll('#state-map path.county')) {
  const box = county.getBoundingClientRect();
  if (box.width < 1 || box.height < 1) continue;
  const x = box.left + box.width / 2, y = box.top + box.height / 2;
  county.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: x, clientY: y}));
  const tip = document.querySelector('#tooltip').getBoundingClientRect();
  out.push({x: Math.round(x), y: Math.round(y), left: tip.left, top: tip.top,
            right: tip.right, bottom: tip.bottom, vw, vh});
}
return out;"""

# Same idea for the national map, sweeping a grid over it.
PROBE_STATES = """
const out = [];
// On a short viewport the map can start below the fold, and elementFromPoint
// only sees what is actually on screen - so bring it into view first and skip
// any grid point that still falls outside.
document.querySelector('#map').scrollIntoView({block: 'center'});
const map = document.querySelector('#map').getBoundingClientRect();
const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
for (let i = 0; i <= 12; i++) for (let j = 0; j <= 12; j++) {
  const x = map.left + map.width * (i / 12), y = map.top + map.height * (j / 12);
  if (x < 0 || y < 0 || x > vw || y > vh) continue;
  const el = document.elementFromPoint(x, y);
  if (!el || !el.classList || !el.classList.contains('state')) continue;
  el.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: x, clientY: y}));
  const tip = document.querySelector('#tooltip').getBoundingClientRect();
  out.push({x: Math.round(x), y: Math.round(y), left: tip.left, top: tip.top,
            right: tip.right, bottom: tip.bottom, vw, vh});
}
return out;"""


def offenders(samples):
    return [s for s in samples
            if s["left"] < 0 or s["top"] < 0 or s["right"] > s["vw"] or s["bottom"] > s["vh"]]


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_state_tooltip_stays_on_screen(page, viewport):
    driver = page(viewport)
    samples = driver.execute_script(PROBE_STATES)
    assert samples, "probe hit no states at all - the grid or the map moved"
    bad = offenders(samples)
    assert not bad, f"{len(bad)}/{len(samples)} state tooltips left the viewport, e.g. {bad[0]}"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_county_tooltip_stays_on_screen(page, viewport):
    driver = page(viewport)
    driver.execute_script(OPEN_STATE, "Pennsylvania")
    samples = driver.execute_script(PROBE_COUNTIES)
    assert samples, "drill-down rendered no counties"
    bad = offenders(samples)
    assert not bad, f"{len(bad)}/{len(samples)} county tooltips left the viewport, e.g. {bad[0]}"
    assert not severe_logs(driver)


def test_tooltip_never_overflows_even_when_wider_than_the_screen(page):
    """The degenerate case: a tooltip too wide to fit anywhere.

    It cannot be placed without overflowing something, so the contract is that
    it pins to the padded left edge rather than running off the right.
    """
    driver = page("android")
    placed = driver.execute_script("""
      const f = window.__mapTest.clampToViewport;
      return [f(180, 300, 1000, 60, 360, 740), f(10, 10, 1000, 60, 360, 740)];""")
    for box in placed:
        assert box["x"] == 8, f"expected a left-pinned fallback, got {box}"
        assert box["y"] >= 8


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_clamp_is_exhaustively_correct(page, viewport):
    """Unit-test the placement maths directly, over a dense grid.

    Driving the browser can only reach the pointer positions the map happens to
    put a shape under. This calls the pure function everywhere, including the
    corners the UI test can never produce.
    """
    width, height, _ = VIEWPORTS[viewport]
    driver = page(viewport)
    failures = driver.execute_script("""
      const [vw, vh] = arguments;
      const f = window.__mapTest.clampToViewport;
      const pad = 8, bad = [];
      for (const w of [80, 190, 240])
        for (const h of [40, 53, 164])
          for (let cx = 0; cx <= vw; cx += Math.max(1, Math.floor(vw / 40)))
            for (let cy = 0; cy <= vh; cy += Math.max(1, Math.floor(vh / 40))) {
              const p = f(cx, cy, w, h, vw, vh);
              const fits = w + 2 * pad <= vw, tall = h + 2 * pad <= vh;
              if (p.x < pad || p.y < pad) bad.push({cx, cy, w, h, p, why: 'before padding'});
              else if (fits && p.x + w > vw - pad) bad.push({cx, cy, w, h, p, why: 'past right'});
              else if (tall && p.y + h > vh - pad) bad.push({cx, cy, w, h, p, why: 'past bottom'});
            }
      return bad.slice(0, 5);""", width, height)
    assert not failures, f"clampToViewport misplaced: {failures}"
