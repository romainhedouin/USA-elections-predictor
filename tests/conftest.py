"""Shared fixtures: a throwaway site built from mock data, served over HTTP.

The tests drive a real headless Chrome rather than asserting on the source,
because every bug this suite exists to catch is a layout bug - something is
the wrong size, or in the wrong place, at some viewport. None of them are
visible in the markup.

The site is assembled in a temp directory from map.html plus freshly generated
mock CSVs, so the suite is deterministic and needs no network beyond the CDN
assets map.html itself loads.
"""

import json
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Viewports the project claims to support. Landscape phone is deliberately
# included: it is the one where a US map plus chrome cannot all fit, so it is
# where "fits on screen" assertions have to be relaxed rather than dropped.
VIEWPORTS = {
    "desktop":   (1920, 1080, False),
    "laptop":    (1440, 900, False),
    "small-lap": (1366, 768, False),
    "tablet":    (768, 1024, False),
    "iphone":    (390, 844, True),
    "android":   (360, 740, True),
    "landscape": (844, 390, True),
}
PHONES = ("iphone", "android")

# Click the state whose aria-label starts with arguments[0]. Shared because
# test_layout.py, test_overlay.py and test_tooltip.py all need to open a
# state's drill-down before asserting on it.
OPEN_STATE = """
const p = [...document.querySelectorAll('#map path.state')]
  .find(p => p.getAttribute('aria-label').startsWith(arguments[0]));
p.dispatchEvent(new MouseEvent('click', {bubbles: true}));"""


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    """map.html + mock CSVs in a temp dir."""
    root = tmp_path_factory.mktemp("site")
    shutil.copy(REPO / "map.html", root / "map.html")
    # map.html loads this as a plain <script> on every page load (see the
    # projection math it moved client-side into), so it 404s and the page
    # never finishes rendering without it.
    shutil.copy(REPO / "estimate.js", root / "estimate.js")
    static = root / "static"
    static.mkdir()
    # Fetched when the House tab is first opened (see loadDistricts()).
    shutil.copy(REPO / "static" / "districts-albers-10m.json", static / "districts-albers-10m.json")
    for race in ("president", "senate", "governor", "house"):
        subprocess.run(
            [sys.executable, str(REPO / "generate_mock_data.py"), "--race", race, "--out-dir", str(root)],
            cwd=REPO, check=True, capture_output=True,
        )
        # In production this is scripts/build_historical_baseline.py's output;
        # an empty baseline is a legitimate result (see its docstring - not
        # every area gets one) and, unlike a missing file, doesn't 404 and
        # trip the "no severe console errors" assertions below.
        (static / f"historical_{race}.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def base_url(site):
    handler = partial(SimpleHTTPRequestHandler, directory=str(site))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def make_driver(width, height, mobile, theme="light"):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--window-size={width},{height}")
    if mobile:
        options.add_experimental_option("mobileEmulation", {
            "deviceMetrics": {"width": width, "height": height, "pixelRatio": 3, "mobile": True},
            "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                         "AppleWebKit/605.1.15 Mobile/15E148",
        })
    driver = webdriver.Chrome(options=options)
    driver.set_window_size(width, height)
    driver.execute_cdp_cmd("Emulation.setEmulatedMedia",
                           {"features": [{"name": "prefers-color-scheme", "value": theme}]})
    return driver


@pytest.fixture
def page(base_url):
    """open(viewport_name, theme=..., palette=...) -> a loaded driver."""
    drivers = []

    def open_page(viewport, theme="light", palette=None):
        width, height, mobile = VIEWPORTS[viewport]
        driver = make_driver(width, height, mobile, theme)
        drivers.append(driver)
        # Seed the stored preferences before the page's head script reads them.
        driver.get(f"{base_url}/map.html")
        driver.execute_script(
            "localStorage.setItem('theme', arguments[0]);"
            "if (arguments[1]) localStorage.setItem('palette', arguments[1]);"
            "else localStorage.removeItem('palette');",
            theme, palette or "",
        )
        driver.get(f"{base_url}/map.html")
        wait_ready(driver)
        return driver

    yield open_page
    for driver in drivers:
        driver.quit()


OPEN_HOUSE_TAB = """
const btn = [...document.querySelectorAll('#race-tabs button')].find(b => b.textContent.includes('House'));
btn.click();"""

# A minimal, real-shaped stand-in for what server.py's proxy (and, upstream of
# it, NBC) returns - see nbc_api.district_results() for the real fields.
STUB_CA01 = {
    "geoid": "0601",
    "label": "California District 1",
    "state": "California",
    "geography": "counties",
    "countyLevel": True,
    "totalExpected": 100000,
    "percentIn": 100.0,
    "demName": "Rose Yee",
    "repName": "Doug LaMalfa",
    "lastModified": None,
    "areas": [
        {"name": "Butte", "fips": "06007", "percentIn": 100.0, "votes": 92708,
         "demReal": 41729, "repReal": 50979},
        {"name": "Colusa", "fips": "06011", "percentIn": 100.0, "votes": 6623,
         "demReal": 2095, "repReal": 4528},
    ],
}


def _house_handler(directory, stubs, delay):
    bodies = {f"/house-district/{g}": json.dumps(s).encode("utf-8") for g, s in stubs.items()}

    class StubbingHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            body = bodies.get(self.path)
            if body is not None:
                # A loopback response can land before the test checks for the
                # loading state - delay just enough to make it observable.
                time.sleep(delay)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/house-district/"):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            super().do_GET()

        def log_message(self, *args):
            pass  # keep pytest output readable

    return partial(StubbingHandler, directory=str(directory))


def house_pages(site, stubs, delay, default_viewport):
    """Generator backing a `house_page` fixture: serves the site plus stubbed
    /house-district/<geoid> routes (404 for any other geoid), and yields
    open(viewport) -> a driver with the House tab selected."""
    from selenium.webdriver.support.ui import WebDriverWait

    server = ThreadingHTTPServer(("127.0.0.1", 0), _house_handler(site, stubs, delay))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    drivers = []

    def open_page(viewport=default_viewport):
        width, height, mobile = VIEWPORTS[viewport]
        driver = make_driver(width, height, mobile)
        drivers.append(driver)
        driver.get(f"{base_url}/map.html")
        wait_ready(driver)
        driver.execute_script(OPEN_HOUSE_TAB)
        WebDriverWait(driver, 25).until(
            lambda d: d.execute_script(
                "return document.querySelector('#race-tabs button[aria-pressed=\"true\"]')"
                ".textContent.startsWith('House')"))
        return driver

    yield open_page
    for driver in drivers:
        driver.quit()
    server.shutdown()


def click_district(driver, geoid):
    driver.execute_script("""
        const geoid = arguments[0];
        const p = [...document.querySelectorAll('#map path.state')]
          .find(el => el.__data__ && el.__data__.id === geoid);
        p.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    """, geoid)


def wait_for_note(driver):
    """Block until the district overlay's note is no longer 'Loading...'."""
    from selenium.webdriver.support.ui import WebDriverWait
    WebDriverWait(driver, 10).until(
        lambda d: "loading" not in d.execute_script(
            "return document.querySelector('#overlay-note').textContent").lower())


def wait_ready(driver, timeout=25):
    """Block until the map has actually drawn its states."""
    from selenium.webdriver.support.ui import WebDriverWait
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.querySelectorAll('#map path.state').length > 0")
    )


def severe_logs(driver):
    return [e["message"] for e in driver.get_log("browser") if e["level"] == "SEVERE"]
