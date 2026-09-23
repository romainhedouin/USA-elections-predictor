"""Shared fixtures: a throwaway site built from mock data, served over HTTP.

The tests drive a real headless Chrome rather than asserting on the source,
because every bug this suite exists to catch is a layout bug - something is
the wrong size, or in the wrong place, at some viewport. None of them are
visible in the markup.

The site is assembled in a temp directory from map.html plus freshly generated
mock CSVs, so the suite is deterministic and needs no network beyond the CDN
assets map.html itself loads.
"""

import shutil
import subprocess
import sys
import threading
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
    # never finishes rendering without it - same reason districts-albers-10m
    # below isn't conditional on the House tab either.
    shutil.copy(REPO / "estimate.js", root / "estimate.js")
    static = root / "static"
    static.mkdir()
    # map.html fetches this unconditionally on boot (not just when the House
    # tab is opened - see start()), so every test needs it present, not only
    # ones that exercise House.
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


def wait_ready(driver, timeout=25):
    """Block until the map has actually drawn its states."""
    from selenium.webdriver.support.ui import WebDriverWait
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.querySelectorAll('#map path.state').length > 0")
    )


def severe_logs(driver):
    return [e["message"] for e in driver.get_log("browser") if e["level"] == "SEVERE"]
