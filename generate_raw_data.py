"""Scrape and parse NBC News county-level results for president, Senate, or governor races.

Two stages, run in order by default:
  1. grab_data(): drive Selenium over each state's results page (the county
     table is JS-rendered) and dump each county row's raw HTML to
     states/<race>/<state>/raw_div.txt.
  2. process_all(): parse that raw HTML into raw_data[_<race>].csv, one row
     per county, including a "predicted final count" that extrapolates each
     candidate's current vote share to 100% reporting.

--race selects president/senate/governor (races.py). President keeps the
original unsuffixed nbc_states.json / raw_data.csv names; the scrape cache is
always per-race so runs can't overwrite each other.

The CSV column names are fixed (Democrat/Republican), not candidate names:
NBC tags every candidate row with its party, so we resolve the leading
Democrat and the leading Republican per state and emit a stable schema that
generate_mock_data.py and map.html can both rely on. Their actual names ride
along in the trailing Name columns.
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from races import RACES, race_files

BASE_URL = "https://www.nbcnews.com"
CSV_HEADER = [
    "State", "County", "State Total Expected", "Total Votes", "Percent In",
    "Democrat Real", "Republican Real", "Democrat Predicted", "Republican Predicted",
    "Democrat Name", "Republican Name",
]


def _make_driver(headless):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")
    return webdriver.Chrome(options=options)


def grab_data(race, nbc_cycle, skip, headless):
    """Scrape each state's results page into states/<race>/<state>/raw_div.txt.

    Scrapes every slug in the race's states file minus --skip; it deliberately
    does NOT intersect with RACES[race]["weights"], because those tables are
    hand-maintained and would silently drop special elections NBC is actually
    publishing.
    """
    paths = race_files(race)
    states_file = paths["nbc_states_file"]
    if not states_file.is_file():
        sys.exit(f"{states_file} not found. Run: python list_states.py --race {race}")

    states = json.loads(states_file.read_text(encoding="utf-8"))
    todo = [state for state in states if state not in skip]

    driver = _make_driver(headless)
    failures = []
    try:
        for state in todo:
            if not _grab_state(driver, state, race, nbc_cycle, paths["states_dir"]):
                failures.append(state)
    finally:
        driver.quit()

    print(f"Scraped {len(todo) - len(failures)}/{len(todo)} states")
    if failures:
        # Loud, and non-zero: a silent partial scrape republishes stale data
        # from a previous run as if it were fresh.
        sys.exit(f"Failed to scrape: {', '.join(failures)}")


def _grab_state(driver, state, race, nbc_cycle, states_dir):
    """Save one state's county rows. Returns True on success."""
    slug = RACES[race]["nbc_slug"]
    label = RACES[race]["label"]
    driver.get(f"{BASE_URL}/politics/{nbc_cycle}-elections/{state}-{slug}-results")

    try:
        _expand_full_county_table(driver, slug)

        title = driver.find_element(By.CSS_SELECTOR, "h1.page-title.state-county-title").text
        state_name = title.split(f" {label} Results")[0]

        total_counted = int(
            driver.find_element(By.ID, f"{slug}-results-summary-grid")
            .find_element(By.ID, f"{slug}-results-summary-container")
            .find_element(By.CLASS_NAME, "rs-total-votes")
            .text.replace(",", "")
        )
        percent_in_text = driver.find_element(By.CLASS_NAME, "percent-in").text
        if "remaining " in percent_in_text:
            # Mid-count: "X% in (Y remaining)" - add the still-outstanding votes.
            estimated_remaining = int(percent_in_text.split("remaining ")[1].split(")")[0].replace(",", ""))
        else:
            # Race called/archived: "100% expected votes in", nothing outstanding.
            estimated_remaining = 0
        # NBC only publishes this estimate state-wide, so it is stamped onto
        # every county row and consumed as "State Total Expected" downstream.
        total_expected_tag = f'<div id="total-estimated">{total_counted + estimated_remaining}</div>'

        county_rows = driver.find_elements(By.CSS_SELECTOR, 'div[data-testid="county-row"]')
        if not county_rows:
            print(f"No county rows found for {state}")
            return False

        state_dir = states_dir / state_name
        state_dir.mkdir(parents=True, exist_ok=True)
        with open(state_dir / "raw_div.txt", "w", encoding="utf-8") as outfile:
            for row in county_rows:
                outfile.write(total_expected_tag + row.get_attribute("outerHTML") + "\n\n")

        print(f"Data saved for {state_name} ({len(county_rows)} county rows)")
        return True

    except (TimeoutException, NoSuchElementException, WebDriverException, ValueError) as e:
        print(f"An error occurred for {state}: {type(e).__name__}: {e}")
        return False


def _expand_full_county_table(driver, slug):
    """Click the "show all counties" toggle if the state page has one."""
    try:
        button = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.ID, f"{slug}-results-table-toggle"))
        )
    except TimeoutException:
        # Small states show every county without a toggle. Not an error.
        return
    driver.execute_script("arguments[0].click();", button)
    time.sleep(1)  # let the expanded table load


def _parse_county_row(row_html):
    """Parse one county's raw HTML into a result dict, or None if it should be skipped."""
    soup = BeautifulSoup(row_html, "html.parser")
    county_row = soup.find("div", {"data-testid": "county-row"})

    county_name_element = county_row.find("span", class_="dib dn-m")
    county_name = county_name_element.text.strip() if county_name_element else "Unknown County"
    if " EV " in county_name:
        # Maine/Nebraska split electoral votes by congressional district and NBC
        # renders an extra "X EV" summary row alongside the real county rows.
        return None

    state_total_expected = int(soup.find("div", {"id": "total-estimated"}).text.strip().replace(",", ""))
    county_total_votes = int(
        soup.find("span", {"data-testid": "state-results-table-area-votes"}).text.split()[0].replace(",", "")
    )
    percent_in = float(soup.find("span", {"class": "percent-in"}).text.split("%")[0])
    if percent_in == 0:
        # Nothing to extrapolate from (and 100/0 would blow up). NBC also
        # rounds, so a county at 0.4% displays "0% in"; either way there is no
        # usable share yet. Absent counties already render as "no data".
        return None

    votes = []
    for candidate_row in county_row.find("div", {"class": "county-table"}).find_all("tr", {"class": "row"}):
        name = candidate_row.find("span", class_="cand-cell-name").find("span", {"data-testid": "text--m"}).text.strip()
        party = candidate_row.find("td", {"data-type": "party"}).get_text(strip=True)
        count = int(candidate_row.find("td", {"data-type": "votes"}).text.strip().replace(",", ""))
        votes.append({"name": name, "party": party, "votes": count})

    return {
        "county_name": county_name,
        "state_total_expected": state_total_expected,
        "county_total_votes": county_total_votes,
        "percent_in": percent_in,
        "votes": votes,
    }


def _iter_county_results(state_dir):
    raw_div_path = state_dir / "raw_div.txt"
    if not raw_div_path.is_file():
        print(f"{raw_div_path} does not exist.")
        return

    for line in raw_div_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            result = _parse_county_row(line)
        except Exception as e:
            print(f"Error processing a county row in {state_dir.name}. Exception: {e}")
            continue
        if result is not None:
            yield result


def _leading_candidate(results, party):
    """The candidate of `party` with the most votes state-wide, or None."""
    totals = {}
    for result in results:
        for candidate in result["votes"]:
            if candidate["party"] == party:
                totals[candidate["name"]] = totals.get(candidate["name"], 0) + candidate["votes"]
    if not totals:
        return None
    return max(totals, key=totals.get)


def process_all(states_dir, output_csv):
    """Parse every scraped state's raw_div.txt into a single CSV of county-level results."""
    if not states_dir.is_dir():
        sys.exit(f"{states_dir} does not exist - nothing to process. Run without --no-grab first.")

    rows = []
    for state_dir in sorted(d for d in states_dir.iterdir() if d.is_dir()):
        state_name = state_dir.name
        results = list(_iter_county_results(state_dir))
        if not results:
            continue

        # Party is resolved per state, not nationally: a Senate map has 35
        # different contests, and ranking candidates by national vote total
        # would zero out every state but the biggest.
        dem = _leading_candidate(results, "D")
        rep = _leading_candidate(results, "R")
        if dem is None or rep is None:
            missing = "Democrat" if dem is None else "Republican"
            print(f"Warning: no {missing} candidate found in {state_name} - that column will be 0")

        for result in results:
            by_name = {candidate["name"]: candidate["votes"] for candidate in result["votes"]}
            real = [by_name.get(dem, 0), by_name.get(rep, 0)]
            predicted = [round(v * 100 / result["percent_in"]) for v in real]
            rows.append(
                [state_name, result["county_name"], result["state_total_expected"],
                 result["county_total_votes"], result["percent_in"]]
                + real + predicted + [dem or "", rep or ""]
            )

    if not rows:
        sys.exit(f"Parsed 0 county rows from {states_dir} - refusing to overwrite {output_csv}.")

    # Write-then-rename so a crash mid-write can't leave a half-written CSV
    # that the map would happily render as real results.
    tmp = output_csv.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    os.replace(tmp, output_csv)

    print(f"Wrote {len(rows)} county rows to {output_csv}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--race", default="president", choices=RACES.keys())
    parser.add_argument(
        "--nbc-cycle", "--cycle", dest="nbc_cycle",
        help="Year in NBC's URL path (default: the race's nbc_cycle in races.py)",
    )
    parser.add_argument("--no-grab", action="store_true", help="Skip scraping and only reprocess already-saved raw HTML")
    parser.add_argument("--skip", default="", help="Comma-separated state slugs to skip, e.g. alaska,hawaii (handy for resuming a partial run)")
    parser.add_argument("--show-browser", action="store_true", help="Run Chrome visibly instead of headless")
    args = parser.parse_args()

    nbc_cycle = args.nbc_cycle or RACES[args.race]["nbc_cycle"]
    paths = race_files(args.race)

    if not args.no_grab:
        skip = {s.strip() for s in args.skip.split(",") if s.strip()}
        grab_data(args.race, nbc_cycle, skip, headless=not args.show_browser)
    process_all(paths["states_dir"], paths["output_csv"])


if __name__ == "__main__":
    main()
