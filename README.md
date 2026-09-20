# USA Election Live Predictor 2028

Scrapes live results from NBC News — president, Senate, governor or House —
and extrapolates each reporting area's current vote share out to 100%
reporting, to get an early read on where a race is heading before all votes
are counted. `map.html` renders president/Senate/governor as a clickable
county map; House renders as a district map (see "House is different" below).

## How the prediction works

A county isn't done reporting just because it isn't at 100%. `estimate.js`
(loaded by `map.html`, runs entirely client-side — `fetch_results.py` only
ever writes real counts, never a projection) turns each area's partial count
into a projection for its full count, one of two ways:

- **With a historical baseline** (see "Historical baseline" below): compare
  the county's own counted-so-far split to how it voted last time, and treat
  the difference as a swing. That swing is trusted more as more of the state
  reports — both by vote share and by how many distinct counties have
  reported, so one large county reporting alone can't manufacture false
  confidence — and applied to the historical split for the vote still to
  come. At 0% counted there is nothing to extrapolate from, so no projection
  is shown at all; showing one would just be relabelling last cycle's result.
- **Without one** (no seat-year match, a non-county state, a
  redistricting-affected House district — see below): fall back to assuming
  the remaining vote splits exactly like the vote counted so far
  (`Predicted = Real × 100 / PercentIn`). This was the whole model before the
  historical baseline existed, and it's still what a plain live feed alone
  can tell you.

Either way, a single county's raw leader and its own projection can now
differ (the flat-only version couldn't: it just scaled both candidates by
the same constant). The bigger, more visible case is still a **state**
(or, for House, a **district**) whose projected winner differs from
whoever's "leading" on raw votes counted so far, once you combine counties
reporting at different rates and swings — the map outlines those. Hover any
county for the full breakdown: historical baseline, observed swing,
confidence, and the resulting projection.

### Historical baseline

`build_historical_baseline.py` builds `historical_<race>.json` once per cycle
from [MIT Election Data and Science Lab (MEDSL)](https://electionlab.mit.edu/)
returns, comparing each area to the *seat-correct* prior election — the same
Senate seat's last regular election (not a flat "6 years ago," which breaks
for special elections), the same governorship's last election (not "4 years
ago," which breaks for New Hampshire/Vermont's 2-year terms), the prior
presidential election, or the prior House election for that district. See
`races.py`'s `SENATE_LAST_CONTESTED`, `GOVERNOR_LAST_ELECTED`,
`PRESIDENT_LAST_ELECTED`, and `HOUSE_LAST_ELECTED`.

MEDSL's readily available data is **county-level for President and
district-level for House**, but **state-level only for Senate and
Governor** — there's no per-county historical split to compare against for
those two races, so their baseline is one number per state, applied
uniformly to every county in it. Areas with no valid comparator at all —
non-county states (Connecticut, Maine, Massachusetts, New Hampshire,
Vermont, Rhode Island, Alaska, DC), and House districts whose lines changed
in a mid-decade redistricting since the baseline year (`races.py`'s
`REDISTRICTING_AFFECTED_DISTRICTS`: Alabama, Georgia, Louisiana, New York,
North Carolina) — simply have no entry in the file, which is exactly the
signal `estimate.js` uses to fall back to the flat estimate for them.

**Known limitations, not bugs:** the model doesn't know what *kind* of vote
is still outstanding (mail vs. election-day timing can itself look like a
swing), doesn't adjust for a personal vote an open seat or a since-retired
incumbent would have carried, and hasn't been backtested against a real past
reporting sequence. `estimate.js`'s tests cover the math in isolation, not
these.

### Data sources & attribution

`build_historical_baseline.py` supports two input shapes (`--format`):

- **`medsl`** (default) — the **MIT Election Data and Science Lab (MEDSL)**
  long-format returns, Harvard Dataverse, CC0-licensed. This is the
  authoritative source for House/Senate/Governor and the one the script's
  column names are documented against. Its Dataverse API requires a
  Guestbook response before it serves a file, so there's no tokenless URL to
  automate the download - fetch it by hand. No attribution is legally
  required (CC0), but it's good practice to cite it anyway:

  > MIT Election Data and Science Lab, "U.S. House, Senate, Gubernatorial, and
  > Presidential Election Returns," Harvard Dataverse.

- **`wide`** — a pre-aggregated, one-row-per-county CSV, freely downloadable
  with no gate. **`historical_president.json`, as checked into this repo,
  was built from this**: [tonmcg/US_County_Level_Election_Results_08-24](https://github.com/tonmcg/US_County_Level_Election_Results_08-24)
  (MIT-licensed), which compiles 2024 county-level results from Fox News'
  election-night reporting - real, comprehensive (~3,150 counties), but
  compiled from a news scrape rather than certified official returns, so
  treat individual close counties with appropriate skepticism. President
  only - no equivalent wide file exists for House/Senate/Governor.

**Per-race status, honestly:**
- **President** — real, comprehensive (`wide` source above).
- **Senate** — real and comprehensive too, just via a different route than
  planned: MEDSL's own ["U.S. Senate statewide 1976–2024"](https://doi.org/10.7910/DVN/PEJ5QU)
  turned out to have **no Guestbook gate** (unlike the datasets this file's
  docstring originally assumed), so `historical_senate.json` is built
  straight from it with `--format medsl` - no workaround needed. One state
  is missing on purpose, not by mistake: MEDSL's own 2020 file leaves
  Wyoming's Senate candidates unclassified by party (`party_detailed` and
  `party_simplified` both blank/"OTHER" for that state/year specifically),
  so `statewide_baseline` correctly finds no two-party split to record and
  Wyoming's Senate race falls back to the flat estimate - not hand-patched,
  since that would mean asserting a party affiliation the source data
  itself doesn't provide.
- **House and Governor — ballot COUNT only, not a party split.** Neither has
  a real party-share baseline (see the gating story below), but both now
  have a real total-ballots figure per area, which is a materially
  weaker but still genuinely real claim: `estimate.js`'s
  `expectedTotalVotes()` only needs an entry's `votes` field, so a
  `{votes, year}` entry with no `demShare`/`repShare` still powers the "X
  total ballots" figure honestly while correctly falling back to the flat
  estimate for the D/R projection (no assumed party lean where we have none).
  - **House**: `historical_house.json` is built with
    `--format house-county-weighted` - no real per-district total was
    available (see below), so each district's total is built by summing its
    actual constituent counties' real 2024 presidential votes (the same
    `wide` source as President), using the Census Bureau's own
    [county↔congressional-district relationship file](https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.2020.html)
    ("119th Congressional District to County") to know which counties are
    in which district - a real geographic join, not an assumption that
    every district in a state is equal in size the way an earlier version
    of this (`--format house-state-apportioned`, still available) did. A
    county entirely inside one district contributes its whole real total
    exactly. A county split across multiple districts (~13% of them) is
    divided by each district's REMAINING population quota, not land area:
    redistricting law requires near-exactly equal population per district
    within a state, so a district's fair share of the state's total is
    `state_total / num_districts`, and whatever it doesn't already get from
    whole counties is what it still needs from the split ones. An earlier
    version of this split by land-area share instead, which badly
    distorted counties like Maricopa, AZ - it spans both dense Phoenix
    suburbs and vast empty desert, so an area-weighted split starved the
    urban districts of nearly all of Maricopa's real vote count and handed
    it to whichever district happened to grab the empty desert. Connecticut's
    5 districts are the one remaining gap: it abolished counties for
    government purposes in 2022, so the Census file uses modern planning
    regions there while our vote data still uses legacy county FIPS - those
    5 fall back to the flat estimate like any other area with no historical
    entry.
  - **Governor**: hand-extracted, one fetch per state, from each state's own
    "20XX \<State\> gubernatorial election" Wikipedia article (the omnibus
    "20XX United States gubernatorial elections" summary page is too long
    to extract cleanly in one pass). Several figures are a two-major-party
    sum rather than a certified exact total - Wikipedia's own infobox didn't
    always surface minor-party/write-in figures in what got fetched - so
    treat these as accurate to within a percent or two, not exact.
  - Neither of these is comprehensive party-share data: House's actual MEDSL
    file (["U.S. House 1976–2024"](https://doi.org/10.7910/DVN/IG0UN2)) IS
    Guestbook-gated (confirmed by trying), and no ungated mirror covering our
    needed year (2024) turned up - it's likely buildable from MEDSL's real,
    ungated 2024 precinct-level files instead, just real aggregation work
    not yet done. Governor doesn't even have an equivalent long-format MEDSL
    dataset to gate in the first place - gubernatorial elections aren't
    federally standardized the way President/Senate/House are, and MEDSL's
    own "State Elections" Dataverse collection only goes back to 2016
    single-year snapshots, one of which is itself gated.

See <https://electionlab.mit.edu/data> for the individual dataset DOIs.

## Requirements

- Python 3.9+ and `pip install -r requirements.txt` (just `requests`)
- Network access for `map.html`'s jsDelivr assets (d3, topojson-client, and
  the us-atlas county boundaries) plus the same-origin `districts-albers-10m.json`
  (checked in - see "House is different")

Google Chrome and the extra packages in `legacy/requirements.txt` are only
needed for the superseded scraper in that folder.

## Data source

Everything comes from NBC News' election-night results pages,
`nbcnews.com/politics/<year>-elections/...`. `races.py` is the config for all
four race types and splits two years that are easy to confuse:

- `election_year` — the election being reported on (2028 president, 2026
  midterms). This is what the UI displays.
- `nbc_cycle` — the year in NBC's URL path. NBC only mints a path once
  results exist, so today all four races point at `"2024"`; the 2026 and
  2028 paths are 404s. Bump `nbc_cycle` on election night (or override it
  per-run with `--nbc-cycle`).

Expect some scraping selectors to need small fixes between elections — NBC
can change their markup.

## Pipeline

```
pip install -r requirements.txt
python fetch_results.py --race president     # or senate / governor / house
```

That's the whole thing — about ten seconds for all 51 states (president/
senate/governor) or a couple of seconds for House (one national request - see
"House is different"). It writes `raw_data.csv` (president) or
`raw_data_<race>.csv`, one row per reporting area, plus a `.meta.json` sidecar
recording where the numbers came from and when. Flags: `--nbc-cycle 2024` to
override the year, `--skip alaska,hawaii` (ignored for House, which has no
per-state fetch to skip), `--out-dir` to write elsewhere.

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

### House is different

The House is 435 single-seat districts rather than a subset of 50 states, and
NBC already reports it that way: one national payload
(`nbc_api.house_results()`) hands back every district's results in a single
request, so there's no per-state fetch loop the way there is for the other
three races - that's the whole national map, refreshed on the regular
schedule. Each row in `raw_data_house.csv` is a whole district, not a county,
so the map's per-district "mismatch" outline can never trigger from the
scheduled data alone - that only shows up once you aggregate sub-units
reporting at different rates, and a district's *national* row has no county
breakdown under it.

**The drill-down gets that breakdown a different way: on click, not on a
timer.** Clicking a district hits `server.py`'s `GET /house-district/<geoid>`
route, which calls `nbc_api.district_results()` for that one district only
(NBC's per-district page, county-level, same shape as the other races' own
drill-down) and returns it as JSON - map.html renders it into the same
overlay UI the other races use, mismatch outline included, once the county
data is actually in hand. Fetching NBC's per-district page for all 435 seats
on every refresh would be roughly 14× the requests the scheduled national
fetch makes; fetching it lazily, only for whichever district someone actually
opens, keeps the scheduled cost at one request while still getting the real
drill-down. A 60-second in-memory cache (`DISTRICT_CACHE_SECONDS`) absorbs a
burst of visitors opening the same district at once. This can't be a plain
client-side `fetch` straight to NBC - checked live, NBC sends no CORS
headers, so a browser blocks it - hence the proxy.

`us-atlas` (the CDN package the county/state map uses) has no congressional-
district layer, and none exists anywhere on npm or jsDelivr, so
`districts-albers-10m.json` is a same-origin file built once by
`build_district_topology.sh` from the Census Bureau's `cb_2024_us_cd119`
shapefile, reprojected into the exact Albers USA `us-atlas` itself uses so the
two line up. It's checked into the repo like `county_fips.json` is; re-run the
script after the next redistricting (the 119th Congress's lines hold through
the 2026 midterms). `house_districts.json`, a GEOID → state/label table built
by the same script, is `races.py`'s source for `HOUSE_DISTRICTS` - and also
fixes a real NBC quirk: NBC numbers an at-large state's lone district "01"
where the Census GEOID standard (and this map) uses "00" - see
`nbc_api._fix_at_large_geoid` (and its mirror image for URLs,
`nbc_api.district_results`'s own district-number lookup).

## Trying it without scraping

`python generate_mock_data.py [--race ...]` writes a fictional CSV (see "How
the prediction works" above) without touching NBC at all. Its metadata sidecar
marks it as mock, and the page shows a "Demo data" badge accordingly. It covers states
where the projected winner matches the raw leader — both nearly-counted
("certain") and barely-counted ("projected"), for both parties — and two
states where they disagree. Every other in-play state gets a "no data yet"
placeholder. House mock data covers the same "certain"/"projected" match cases
per district, real GEOIDs and all, but has no mismatch case - see "House is
different" above for why one national-map row can't produce one on its own.

One asymmetry worth knowing: the House drill-down always calls the real
`/house-district/<geoid>` proxy, live, regardless of whether the national map
is showing mock data or real results - there's no mock version of that route.
Click a district on the House "Demo" tab and you'll see today's actual NBC
numbers for it, not fiction.

The CSVs are gitignored, so run it once per race before opening the map:

```
python generate_mock_data.py --race president
python generate_mock_data.py --race senate
python generate_mock_data.py --race governor
python generate_mock_data.py --race house
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

It opens on a national map with President / Senate / Governor / House tabs;
click or tab-and-Enter a state (or, on the House tab, a district) to drill
into its counties - see "House is different" for how that drill-down gets its
data differently from the other three. It re-pulls the active race's national
map once a minute, and the subtitle says what is on screen and how old it is.
Solid fill means that region's own count is effectively done, a hatch means
it's still counting, grey means no data yet, and a dashed outline marks a
state or district where the raw leader disagrees with the extrapolated
winner. The scoreboard totals electoral votes or seats, split certain vs
projected.

`RACE_META` in `map.html` mirrors `races.py`'s weight tables by hand, since
the page is deliberately a single static file with no build step - except
House's, which is 435 entries and gets filled in from the district topology
at load time instead (see `start()`). If you edit `races.py`'s weight tables,
edit `RACE_META` to match.

That's it, unless I forgot something or NBC's website has changed, in which
case good luck!

## Layout

| | |
|---|---|
| `races.py` | race config: electoral votes, which states are in play, House's 435 districts, and the two different years (`election_year` vs `nbc_cycle`) |
| `nbc_api.py` | client for NBC's results API |
| `fetch_results.py` | the pipeline: API → `raw_data[_<race>].csv` (real counts only - no projection) |
| `build_fips_table.py` | regenerates `county_fips.json` (rarely) |
| `build_district_topology.sh` | regenerates `districts-albers-10m.json` and `house_districts.json` (rarely - see "House is different") |
| `build_historical_baseline.py` | regenerates `historical_<race>.json` from MEDSL data (once per cycle - see "Historical baseline") |
| `estimate.js` | the projection math (flat and historical-swing) - runs client-side, loaded by `map.html` |
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
| `RACES` | all four | which races get *refreshed*. All four are always served and always seeded — this only controls fetching |
| `DEFAULT_RACE` | `president` | which race a visitor **lands on**. Unrelated to `RACES` |
| `REFRESH_SECONDS` | `900` | 60 on election night; floor is 30 |
| `REFRESH_ENABLED` | `1` | `0` pauses fetching entirely; the site stays up serving what it has |
| `DATA_YEAR` | race config | the cycle to pull, overriding `races.py` |
| `FETCH_TIMEOUT` | `300` | hard kill per race | `/healthz` reports
per-race freshness, last success and last error. A failed refresh keeps serving
the last good data. See `DEPLOY.md` for the Railway steps.

## License

MIT - see `LICENSE`. Prior-cycle results used for the historical baseline are
from MEDSL, separately CC0-licensed - see "Data sources & attribution" above.
