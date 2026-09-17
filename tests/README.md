# Tests

Layout regression tests. They drive a real headless Chrome, because every bug
they exist to catch is a layout bug — something is the wrong size, or in the
wrong place, at some viewport. None of them are visible in the markup.

```
pip install -r tests/requirements.txt
python -m pytest tests/
```

The suite builds a throwaway site in a temp directory from `map.html` plus
freshly generated mock CSVs and serves it, so it is deterministic and needs no
network beyond the CDN assets `map.html` itself loads. A full run takes ~10
minutes: 115 cases across 7 viewports and both themes × both palettes, each
launching a browser.

Each test is a regression for something that actually shipped broken:

| File | Catches |
|---|---|
| `test_tooltip.py` | the tooltip leaving the viewport — it flipped away from the right edge but nothing clamped it, so on a narrow screen it overflowed the left instead. Includes an exhaustive unit test of the placement maths, which reaches corners the UI never produces. |
| `test_layout.py` | horizontal overflow, the map cropped or distorted, sub-44px tap targets, a page that scrolls despite being sized to fit, and a clean render in all four theme/palette combinations. |
| `test_overlay.py` | a drill-down with no reachable backdrop (on a phone the sheet filled the screen, leaving the close button as the only exit), focus not returning on Escape, a modal that isn't announced as one, and the page scrolling behind an open sheet. |

Adding a viewport to `VIEWPORTS` in `conftest.py` extends every parametrised
test to it. `landscape` is excluded from the fit assertions on purpose: a
844×390 viewport cannot hold a 1.6:1 map plus chrome, and pretending otherwise
would mean asserting something false.
