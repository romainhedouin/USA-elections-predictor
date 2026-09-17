# USA Election Live Predictor 2028

Scrapes live, county-level presidential results from NBC News and extrapolates
each county's current vote share out to 100% reporting, to get an early read
on where a state is heading before all votes are counted.

## Data source

Everything comes from `nbcnews.com/politics/2024-elections/...` (NBC News'
election-night results pages). Note the URL still says `2024-elections` —
update `MAIN_URL`/`BASE_URL` in `list_states.py` and `generate_raw_data.py`
once NBC stands up their 2028 pages, and expect some scraping selectors to
need small fixes since NBC can change their markup between elections.

## Pipeline

1. `pip install -r requirements.txt`
2. `python list_states.py` — scrapes NBC's results hub page for the list of
   per-state result pages, saved to `nbc_states.json`.
3. `python generate_raw_data.py` — for each state (unless excluded, see
   below):
   - drives a real Chrome browser via Selenium to load the state's results
     page (the county table is JS-rendered) and saves each county row's raw
     HTML under `states/<state>/raw_div.txt`;
   - parses that raw HTML into `raw_data.csv`, one row per county, with real
     vote counts plus a "predicted" final count per candidate (extrapolating
     the current vote share to 100% reporting).

   Pass `--no-grab` to re-run just the parsing step against already-saved
   `states/` data (handy while iterating on the parsing logic).

You can skip scraping specific states by setting them to `true` in
`states_to_exclude.json` — useful for resuming after a partial run.

That's it, unless I forgot something or NBC's website has changed, in which
case good luck!
