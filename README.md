# USA Election Live Predictor 2028

Scrapes live results from NBC News — president, Senate, governor, or House —
and projects each area's current vote share out to 100% reporting, to get an
early read on where a race is heading before all votes are counted. `map.html`
renders president/Senate/governor as a county map and House as a district map.

## How the prediction works

`estimate.js` (client-side, loaded by `map.html`) turns each area's partial
count into a projection for its full count, one of two ways:

- **With a historical baseline** — compare the area's counted-so-far split to
  how it voted last time, treat the difference as a swing, and apply that
  swing (trusted more as more of the area reports) to the historical split for
  the remaining vote. At 0% counted there's nothing to extrapolate from, so no
  projection is shown.
- **Without one** (no historical match for that area) — assume the remaining
  vote splits like the vote counted so far: `Predicted = Real × 100 /
  PercentIn`.

A county's raw leader and its own projection can differ; the map outlines any
state or district where the projected winner disagrees with whoever's
currently "leading" on raw votes. In a drill-down, the **?** next to each row
(or tapping the row) shows the exact working for that projection, step by step
with the real numbers, and "How this total is computed" does the same for the
state or district total.

`scripts/build_historical_baseline.py` builds `static/historical_<race>.json`
once per cycle. What each race is compared against:

| Race | Baseline |
|---|---|
| President | each county's 2024 presidential result |
| Senate | each county's 2024 presidential lean, shifted by how the state's last Senate race for that seat differed from its 2024 presidential result ([MIT Election Lab](https://electionlab.mit.edu/) statewide returns) |
| House | each district's 2024 result, from NBC's final numbers (checked against the House Clerk's official statistics). Districts redrawn for 2026 have none |
| Governor | none yet: vote totals but no party split, so the plain extrapolation |

Areas with no baseline (a town rather than a county, a one-party race, a
redrawn district) use the plain extrapolation, and the **?** says which reason
applies. See that script's docstring for the exact sourcing and known gaps.

Nine states use new House maps in 2026 (Alabama, California, Florida,
Louisiana, North Carolina, Ohio, Tennessee, Texas, Utah). The district map uses
those lines (`scripts/build_district_topology.sh`, from the Census Bureau's
120th-Congress files). Until 2026 results exist, the 137 districts whose lines
changed show no results, since the 2024 ones cover different territory.

## Requirements

- Python 3.9+ and `pip install -r requirements.txt` (just `requests`)
- Network access for `map.html`'s CDN assets (d3, topojson-client, us-atlas
  county boundaries) plus the same-origin files under `static/`

## Pipeline

```
pip install -r requirements.txt
python fetch_results.py --race president     # or senate / governor / house
```

Pulls NBC's unauthenticated results API (`nbc_api.py`) and writes
`raw_data[_<race>].csv` — real counts only, never a projection — plus a
`.meta.json` sidecar. `races.py` holds the config: election years, in-play
states, and the 435 House districts. Flags: `--nbc-cycle`, `--skip
alaska,hawaii`, `--out-dir`.

House is fetched differently: NBC returns all 435 districts in one national
request, so there's no per-state loop. Its county-level drill-down is instead
fetched lazily, on click, via `server.py`'s `/house-district/<geoid>` route.

## Trying it without scraping

```
python generate_mock_data.py --race president   # or senate / governor / house
```

Writes a fictional CSV with the same schema, no network required. The page
shows a "Demo data" badge when serving it.

## Map

```
DATA_DIR=. REFRESH_ENABLED=0 python server.py
```

Then open `http://localhost:8000/` (serves the CSVs written above, with no
background fetching; opening the file directly won't work — `fetch` needs
HTTP). `python3 -m http.server` also works, except that clicking a House
district can't load its county breakdown. It shows a national map with President / Senate /
Governor / House tabs; click a state (or House district) to drill into
counties. Solid fill means a region's count is effectively done, hatching
means it's still counting, grey means no data yet, and a dashed outline marks
a state/district where the raw leader disagrees with the projected winner.

## Layout

| | |
|---|---|
| `map.html` | the whole UI, one static file |
| `estimate.js` | client-side projection math (flat and historical-swing) |
| `server.py` + `railway.toml` | deployment: serves the page, refreshes data on a timer |
| `races.py` | race config: electoral votes, in-play states, House districts |
| `nbc_api.py` | client for NBC's results API |
| `fetch_results.py` | pipeline: API → `raw_data[_<race>].csv` |
| `generate_mock_data.py` | fictional fixtures, same schema |
| `static/` | checked-in reference data: county FIPS table, district topology, historical baselines |
| `scripts/` | one-time/rare build scripts that regenerate `static/`'s contents |
| `tests/` | layout regressions (Selenium) + unit tests (Python and Node) |
| `DEPLOY.md` | Railway setup |

## Deploying

`server.py` serves `map.html` plus the CSVs and refreshes them on a timer, in
one process. Seeds mock data on a cold start so the site is never broken, then
fetches live results for whichever races `RACES` names.

```
DATA_DIR=./data REFRESH_SECONDS=900 NBC_CYCLE=2024 RACES=president python server.py
```

| Variable | Default | |
|---|---|---|
| `DATA_DIR` | `./data` | where the CSVs live (a mounted volume in production) |
| `RACES` | all four | which races get *refreshed* (all four are always served) |
| `DEFAULT_RACE` | `president` | which race a visitor lands on |
| `REFRESH_SECONDS` | `900` | 60 on election night; floor is 30 |
| `REFRESH_ENABLED` | `1` | `0` pauses fetching; the site stays up serving what it has |
| `DATA_YEAR` (alias `NBC_CYCLE`) | race config | override the cycle to pull |
| `FETCH_TIMEOUT` | `300` | hard kill per race |
| `SEED_ON_BOOT` | `1` | `0` skips the cold-start mock seed for missing CSVs |
| `DISTRICT_CACHE_SECONDS` | `60` | how long a `/house-district/<geoid>` response is cached; floor is 15 |
| `PORT` | `8000` | listen port (Railway injects it) |

`/healthz` reports per-race freshness and errors. See `DEPLOY.md` for the
Railway-specific steps.

## License

MIT — see `LICENSE`. Prior-cycle results used for the historical baseline are
from MEDSL, separately CC0-licensed.
