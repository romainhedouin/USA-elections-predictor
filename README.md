# USA Election Live Predictor 2028

Scrapes live, county-level results from NBC News — president, Senate or
governor — and extrapolates each county's current vote share out to 100%
reporting, to get an early read on where a state is heading before all votes
are counted. `map.html` renders the result as a clickable county map.

## How the prediction works

A county isn't done reporting just because it isn't at 100%. If a county of
100k voters is 5% counted and currently 60% Republican, it's likely to
finish close to 60% Republican — and because it's a big county, that can
outweigh a small county of 5k voters that's 100% counted at 90% Democrat.
We extrapolate each county's *current* vote share out to its expected final
vote count, so a state's projected winner can differ from whoever's
"leading" on raw votes counted so far. The map outlines those states.

Note this only ever shows up once you **aggregate**: within a single county,
`Predicted = Real × (100 / PercentIn)` scales both candidates by the same
number, so the leader can't flip. The flip comes from combining counties
that are reporting at different rates.

## Requirements

- Python 3.9+ and `pip install -r requirements.txt` (just `requests`)
- Network access for `map.html`'s three jsDelivr assets (d3, topojson-client,
  and the us-atlas county boundaries)

Google Chrome and the extra packages in `legacy/requirements.txt` are only
needed for the superseded scraper in that folder.

## Data source

Everything comes from NBC News' election-night results pages,
`nbcnews.com/politics/<year>-elections/...`. `races.py` is the config for all
three race types and splits two years that are easy to confuse:

- `election_year` — the election being reported on (2028 president, 2026
  midterms). This is what the UI displays.
- `nbc_cycle` — the year in NBC's URL path. NBC only mints a path once
  results exist, so today all three races point at `"2024"`; the 2026 and
  2028 paths are 404s. Bump `nbc_cycle` on election night (or override it
  per-run with `--nbc-cycle`).

Expect some scraping selectors to need small fixes between elections — NBC
can change their markup.

## Pipeline

```
pip install -r requirements.txt
python fetch_results.py --race president     # or senate / governor
```

That's the whole thing — about ten seconds for all 51 states. It writes
`raw_data.csv` (president) or `raw_data_<race>.csv`, one row per reporting
area, plus a `.meta.json` sidecar recording where the numbers came from and
when. Flags: `--nbc-cycle 2024` to override the year, `--skip alaska,hawaii`,
`--out-dir` to write elsewhere.

The results pages are a Next.js app that calls an unauthenticated JSON API,
so `nbc_api.py` calls it directly instead of driving a browser. That is ~25×
faster than the old Selenium scraper, and it hands over party codes and county
FIPS as structured fields rather than things to infer from markup. The old
scraper is kept in `legacy/` in case NBC ever retires the API.

NBC tags every candidate with a party, so the CSV columns are always
`Democrat`/`Republican` regardless of who's running — the candidates' actual
names ride along in the trailing `Democrat Name` / `Republican Name` columns.
`State Total Expected` is NBC's state-wide estimate of final turnout, which
they only publish per state, so it's repeated on every row of that state.

### Counties, and the eight states that don't have them

Rows carry a 5-digit county `FIPS`, which is exactly the id `us-atlas` gives
its county shapes — so the map joins on it instead of matching names. That
matters: name matching silently painted ten states wrong, because NBC calls
Kings County "Brooklyn", and because a New England *town* often shares its
name with a county it is not in.

NBC's API doesn't attach a name to its FIPS keys, so `county_fips.json` is a
lookup table built once by `build_fips_table.py` against a settled cycle,
where per-county vote totals identify each county unambiguously. Read that
script's docstring before regenerating it.

Eight states have no county-level numbers at all: Connecticut, Maine,
Massachusetts, New Hampshire and Vermont report by township, Rhode Island by
municipality, Alaska by legislative district, DC by ward. Their state colour
and totals are correct; their drill-down says so rather than inventing a
county breakdown.

## Trying it without scraping

`python generate_mock_data.py [--race ...]` writes a fictional CSV (see "How
the prediction works" above) without touching NBC at all. Its metadata sidecar
marks it as mock, and the page shows a "Demo data" badge accordingly. It covers states
where the projected winner matches the raw leader — both nearly-counted
("certain") and barely-counted ("projected"), for both parties — and two
states where they disagree. Every other in-play state gets a "no data yet"
placeholder.

The CSVs are gitignored, so run it once per race before opening the map:

```
python generate_mock_data.py --race president
python generate_mock_data.py --race senate
python generate_mock_data.py --race governor
```

## Map

`map.html` renders those CSVs down to the county level in the browser, using
[`topojson/us-atlas`](https://github.com/topojson/us-atlas)'s county
boundaries (derived from Census Bureau cartographic shapefiles, ISC licensed)
loaded from a CDN. Each county is matched to the topology by name within its
state — the state comes from the county FIPS prefix — so no external lookup
table is needed.

Serve the repo root and open the page:

```
python3 -m http.server
```

then visit `http://localhost:8000/map.html` (opening the file directly won't
work — `fetch` needs HTTP).

It opens on a national, state-level map with President / Senate / Governor
tabs; click or tab-and-Enter a state to drill into its counties. It re-pulls
the active race once a minute, and the subtitle says what is on screen and how
old it is. Solid fill
means that region's own count is effectively done, a hatch means it's still
counting, grey means no data yet, and a dashed outline marks a state where
the raw leader disagrees with the extrapolated winner. The scoreboard totals
electoral votes or seats, split certain vs projected.

`RACE_META` in `map.html` mirrors `races.py`'s weight tables by hand, since
the page is deliberately a single static file with no build step. If you edit
one, edit the other.

That's it, unless I forgot something or NBC's website has changed, in which
case good luck!

## Layout

| | |
|---|---|
| `races.py` | race config: electoral votes, which states are in play, and the two different years (`election_year` vs `nbc_cycle`) |
| `nbc_api.py` | client for NBC's results API |
| `fetch_results.py` | the pipeline: API → `raw_data[_<race>].csv` |
| `build_fips_table.py` | regenerates `county_fips.json` (rarely) |
| `generate_mock_data.py` | fictional fixtures with the same schema |
| `map.html` | the whole UI, one static file |
| `legacy/` | the superseded Selenium scraper, kept as a fallback |
| `server.py` + `railway.toml` | the deployment: serves the page and refreshes the data on a timer |
| `DEPLOY.md` | click-by-click Railway setup |

## Deploying

`server.py` serves `map.html` plus the CSVs and refreshes them on a timer, in
one process — Railway volumes attach to a single service, so the refresher
lives inside the web server rather than in a cron job. Run it locally with:

```
DATA_DIR=./data REFRESH_SECONDS=900 NBC_CYCLE=2024 RACES=president python server.py
```

It seeds mock data for every race on a cold start so the site is never broken,
then fetches live results for whichever races `RACES` names.

| Variable | Default | |
|---|---|---|
| `DATA_DIR` | `./data` | where the CSVs live (a mounted volume in production) |
| `RACES` | all three | which races get *refreshed*. All three are always served and always seeded — this only controls fetching |
| `DEFAULT_RACE` | `president` | which race a visitor **lands on**. Unrelated to `RACES` |
| `REFRESH_SECONDS` | `900` | 60 on election night; floor is 30 |
| `REFRESH_ENABLED` | `1` | `0` pauses fetching entirely; the site stays up serving what it has |
| `DATA_YEAR` | race config | the cycle to pull, overriding `races.py` |
| `FETCH_TIMEOUT` | `300` | hard kill per race | `/healthz` reports
per-race freshness, last success and last error. A failed refresh keeps serving
the last good data. See `DEPLOY.md` for the Railway steps.
