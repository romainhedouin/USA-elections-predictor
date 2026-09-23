"""The "?" breakdown next to each projection must show the working behind the
exact numbers on screen, and behave as a proper dialog on top of the
drill-down (Escape closes it alone, focus returns to the button).

Parses the popup's final "D ... = N" / "R ... = N" lines and compares them to
the table row / summary it explains, so a breakdown that drifted from the
displayed projection would fail here.
"""

import re
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import OPEN_STATE, REPO, VIEWPORTS, make_driver, severe_logs, wait_ready


@pytest.fixture(scope="module")
def page(site, tmp_path_factory):
    """The mock site, but with the real historical baselines: the shared site
    ships empty ones, which would only ever exercise the flat fallback."""
    root = tmp_path_factory.mktemp("explain-site")
    shutil.copytree(site, root, dirs_exist_ok=True)
    for baseline in (REPO / "static").glob("historical_*.json"):
        shutil.copy(baseline, root / "static" / baseline.name)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    drivers = []

    def open_page(viewport):
        width, height, mobile = VIEWPORTS[viewport]
        driver = make_driver(width, height, mobile)
        drivers.append(driver)
        driver.get(f"http://127.0.0.1:{server.server_address[1]}/map.html")
        wait_ready(driver)
        return driver

    yield open_page
    for driver in drivers:
        driver.quit()
    server.shutdown()

PROJECTION = re.compile(r"^([DR]) .*= ([\d,]+)$", re.M)


def _projection_lines(text):
    return {side: int(n.replace(",", "")) for side, n in PROJECTION.findall(text)}


def _popup(driver):
    return driver.execute_script("""
      const box = document.querySelector('#explain');
      return {open: !box.hidden, title: document.querySelector('#explain-title').textContent,
              text: document.querySelector('#explain-body').innerText, focus: document.activeElement.id};""")


def test_row_breakdown_matches_its_row(page):
    driver = page("desktop")
    driver.execute_script(OPEN_STATE, "Georgia")
    row = driver.execute_script("""
      const tr = document.querySelector('#overlay-table tr.explainable');
      const c = tr.querySelectorAll('td');
      tr.querySelector('button.why').click();
      return {name: c[0].textContent, d: +c[3].textContent.replace(/,/g, ''), r: +c[5].textContent.replace(/,/g, '')};""")
    popup = _popup(driver)
    assert popup["open"] and popup["title"].startswith(row["name"])
    assert popup["focus"] == "explain-panel"
    shown = _projection_lines(popup["text"])
    if "Fully counted" not in popup["text"]:
        assert shown == {"D": row["d"], "R": row["r"]}

    driver.execute_script("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    state = driver.execute_script("""return {popup: !document.querySelector('#explain').hidden,
      overlay: document.querySelector('#overlay').classList.contains('open'),
      focusIsWhy: document.activeElement.classList.contains('why')}""")
    assert state == {"popup": False, "overlay": True, "focusIsWhy": True}
    assert not severe_logs(driver)


def test_every_projected_row_has_a_breakdown_that_adds_up(page):
    driver = page("desktop")
    driver.execute_script(OPEN_STATE, "Georgia")
    rows = driver.execute_script("""
      return [...document.querySelectorAll('#overlay-table tr.explainable')].map(tr => {
        tr.click();
        const text = document.querySelector('#explain-body').innerText;
        const c = tr.querySelectorAll('td');
        document.querySelector('#explain-close').click();
        return {name: c[0].textContent, d: +c[3].textContent.replace(/,/g, ''), r: +c[5].textContent.replace(/,/g, ''), text};
      });""")
    # The mock Georgia counties are partly counted and have 2024 baselines, so
    # this really exercises the swing model, not just "fully counted".
    assert any("Swing so far" in row["text"] for row in rows)
    for row in rows:
        if "Fully counted" in row["text"]:
            continue
        assert _projection_lines(row["text"]) == {"D": row["d"], "R": row["r"]}, row["name"]


def test_total_breakdown_matches_the_summary(page):
    driver = page("desktop")
    driver.execute_script(OPEN_STATE, "Georgia")
    summary = driver.execute_script("""
      return [...document.querySelectorAll('#overlay-summary .cand-numbers')].map(n =>
        +n.querySelectorAll('span')[2].textContent.replace(/,/g, ''));""")
    driver.execute_script("document.querySelector('button.why-total').click()")
    text = _popup(driver)["text"]
    projected = re.search(r"D ([\d,]+), R ([\d,]+)", text)
    assert [int(projected.group(1).replace(",", "")), int(projected.group(2).replace(",", ""))] == summary
    assert "Expected total ballots" in text and "Call" in text


@pytest.mark.parametrize("viewport", ["iphone", "android"])
def test_drilldown_with_breakdown_column_fits_a_phone(page, viewport):
    driver = page(viewport)
    driver.execute_script(OPEN_STATE, "Georgia")
    widths = driver.execute_script("const p = document.querySelector('#overlay-panel'); return [p.scrollWidth, p.clientWidth]")
    assert widths[0] <= widths[1], f"drill-down scrolls sideways: {widths}"
