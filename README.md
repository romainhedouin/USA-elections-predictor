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

- Python 3.9+ and `pip install -r requirements.txt`
- Google Chrome installed (Selenium 4 resolves its own chromedriver)
- Network access for `map.html`'s three jsDelivr assets (d3, topojson-client,
  and the us-atlas county boundaries)

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

1. `pip install -r requirements.txt`
2. `python list_states.py [--race president|senate|governor]` — scrapes NBC's
   results hub page for the list of per-state result pages, saved to
   `nbc_states.json` (president) or `nbc_states_<race>.json`.
3. `python generate_raw_data.py [--race ...]` — for each state:
   - drives headless Chrome via Selenium to load the state's results page
     (the county table is JS-rendered) and saves each county row's raw HTML
     under `states/<race>/<state>/raw_div.txt`;
   - parses that raw HTML into `raw_data.csv` / `raw_data_<race>.csv`, one
     row per county, with real vote counts plus a "predicted" final count per
     party (extrapolating the current vote share to 100% reporting).

   Useful flags: `--no-grab` reprocesses already-saved `states/` data without
   touching the network (handy while iterating on the parsing logic);
   `--skip alaska,hawaii` resumes a partial run; `--show-browser` runs Chrome
   visibly.

NBC tags every candidate row with its party, so the CSV columns are always
`Democrat`/`Republican` regardless of who's running — the candidates' actual
names ride along in the trailing `Democrat Name` / `Republican Name` columns.
`State Total Expected` is NBC's state-wide estimate of final turnout, which
they only publish per state, so it's repeated on every row of that state.

## Trying it without scraping

`python generate_mock_data.py [--race ...]` writes a fictional CSV (see "How
the prediction works" above) without touching NBC at all. It covers states
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
tabs; click or tab-and-Enter a state to drill into its counties. Solid fill
means that region's own count is effectively done, a hatch means it's still
counting, grey means no data yet, and a dashed outline marks a state where
the raw leader disagrees with the extrapolated winner. The scoreboard totals
electoral votes or seats, split certain vs projected.

`RACE_META` in `map.html` mirrors `races.py`'s weight tables by hand, since
the page is deliberately a single static file with no build step. If you edit
one, edit the other.

That's it, unless I forgot something or NBC's website has changed, in which
case good luck!
