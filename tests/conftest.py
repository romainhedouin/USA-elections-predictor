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
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

REPO = Path(__file__).resolve().parent.parent

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


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    """map.html + mock CSVs in a temp dir."""
    root = tmp_path_factory.mktemp("site")
    shutil.copy(REPO / "map.html", root / "map.html")
    for race in ("president", "senate", "governor"):
        subprocess.run(
            [sys.executable, str(REPO / "generate_mock_data.py"), "--race", race, "--out-dir", str(root)],
            cwd=REPO, check=True, capture_output=True,
        )
    return root


@pytest.fixture(scope="session")
def base_url(site):
    handler = partial(SimpleHTTPRequestHandler, directory=str(site))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def make_driver(width, height, mobile, theme="light", palette=None):
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
    driver._prefs = {"theme": theme, "palette": palette}
    return driver


@pytest.fixture
def page(request, base_url):
    """open(viewport_name, theme=..., palette=...) -> a loaded driver."""
    drivers = []

    def open_page(viewport, theme="light", palette=None):
        width, height, mobile = VIEWPORTS[viewport]
        driver = make_driver(width, height, mobile, theme, palette)
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
