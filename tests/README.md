# Tests

Two kinds of test live here. The Selenium files drive a real headless Chrome,
because the bugs they exist to catch are layout bugs — something is the wrong
size, or in the wrong place, at some viewport. None of them are visible in the
markup. The rest are plain unit tests with no browser and no network.

```
pip install -r requirements.txt -r tests/requirements.txt
python -m pytest tests/               # everything, ~10 minutes
node --test tests/test_estimate.js    # projection math
```

Fast subset (no browser, a few seconds):

```
python -m pytest tests/test_nbc_api.py tests/test_historical_baseline.py \
  tests/test_generate_mock_data.py tests/test_generate_raw_data.py \
  tests/test_server_district_cache.py
```

The browser suite builds a throwaway site in a temp directory from `map.html`
plus freshly generated mock CSVs and serves it, so it is deterministic and
needs no network beyond the CDN assets `map.html` itself loads. It runs across
7 viewports and both themes × both palettes, each case launching a browser.

Each test is a regression for something that actually shipped broken:

| File | Catches |
|---|---|
| `test_tooltip.py` | the tooltip leaving the viewport — it flipped away from the right edge but nothing clamped it, so on a narrow screen it overflowed the left instead. Includes an exhaustive unit test of the placement maths, which reaches corners the UI never produces. |
| `test_layout.py` | horizontal overflow, the map cropped or distorted, sub-44px tap targets, a page that scrolls despite being sized to fit, and a clean render in all four theme/palette combinations. |
| `test_overlay.py` | a drill-down with no reachable backdrop (on a phone the sheet filled the screen, leaving the close button as the only exit), focus not returning on Escape, a modal that isn't announced as one, and the page scrolling behind an open sheet. |
| `test_house.py` | House grouped by state instead of district, and the lazy district drill-down firing when it shouldn't or hanging when its fetch fails (against a stubbed `/house-district/<geoid>`). |
| `test_map_helpers.py` | the geography label, summary tail and per-state county lookup that `map.html` shares across several call sites. |
| `test_nbc_api.py` *(no browser)* | `nbc_api.py`'s payload normalisation for state and district results. |
| `test_historical_baseline.py` *(no browser)* | `scripts/build_historical_baseline.py`'s join and aggregation logic. |
| `test_generate_mock_data.py` *(no browser)* | the mock CSV's header, delimiter and row shape. |
| `test_generate_raw_data.py` *(no browser)* | `legacy/generate_raw_data.py` writing columns out of order under `CSV_HEADER`. Skipped if `bs4` isn't installed. |
| `test_server_district_cache.py` *(no browser)* | `server.py`'s district cache growing past the 435 real districts, or concurrent misses each calling NBC. |
| `test_estimate.js` *(Node, no browser)* | `estimate.js`'s flat and historical-swing projection maths. |

Adding a viewport to `VIEWPORTS` in `conftest.py` extends every parametrised
test to it. `landscape` is excluded from the fit assertions on purpose: a
844×390 viewport cannot hold a 1.6:1 map plus chrome, and pretending otherwise
would mean asserting something false.
