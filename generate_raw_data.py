"""Scrape and parse NBC News county-level presidential results.

Two stages, run in order by default:
  1. grab_data(): drive Selenium over each state's results page (the county
     table is JS-rendered) and dump each county row's raw HTML to
     states/<state>/raw_div.txt.
  2. process_all(): parse that raw HTML into raw_data.csv, one row per
     county, including a "predicted final count" that extrapolates each
     candidate's current vote share to 100% reporting.
"""

import argparse
import csv
import json
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://www.nbcnews.com"
STATES_DIR = Path("states")
NBC_STATES_FILE = Path("nbc_states.json")
EXCLUSIONS_FILE = Path("states_to_exclude.json")
OUTPUT_CSV = Path("raw_data.csv")


def load_excluded_states():
    with open(EXCLUSIONS_FILE) as file:
        exclusions = json.load(file)
    return {state for state, excluded in exclusions.items() if excluded}


def grab_data():
    """Scrape each non-excluded state's results page into states/<state>/raw_div.txt."""
    with open(NBC_STATES_FILE) as file:
        states = json.load(file)
    excluded = load_excluded_states()

    driver = webdriver.Chrome()
    try:
        for state in states:
            if state in excluded:
                continue
            _grab_state(driver, state)
    finally:
        driver.quit()


def _grab_state(driver, state):
    driver.get(f"{BASE_URL}/politics/2024-elections/{state}-president-results")

    try:
        _expand_full_county_table(driver)

        title = driver.find_element(By.CSS_SELECTOR, "h1.page-title.state-county-title").text
        state_name = title.split(" President Results")[0]

        total_counted = int(
            driver.find_element(By.ID, "president-results-summary-grid")
            .find_element(By.ID, "president-results-summary-container")
            .find_element(By.CLASS_NAME, "rs-total-votes")
            .text.replace(",", "")
        )
        percent_in_text = driver.find_element(By.CLASS_NAME, "percent-in").text
        if "remaining " in percent_in_text:
            # Mid-count: "X% in (Y remaining)" — add the still-outstanding votes.
            estimated_remaining = int(percent_in_text.split("remaining ")[1].split(")")[0].replace(",", ""))
        else:
            # Race called/archived: "100% expected votes in", nothing outstanding.
            estimated_remaining = 0
        total_expected_tag = f'<div id="total-estimated">{total_counted + estimated_remaining}</div>'

        county_rows = driver.find_elements(By.CSS_SELECTOR, 'div[data-testid="county-row"]')

        state_dir = STATES_DIR / state_name
        state_dir.mkdir(parents=True, exist_ok=True)
        with open(state_dir / "raw_div.txt", "w") as outfile:
            for row in county_rows:
                outfile.write(total_expected_tag + row.get_attribute("outerHTML") + "\n\n")

        print(f"Data saved for {state_name}")

    except Exception as e:
        print(f"An error occurred for {state}: {e}")


def _expand_full_county_table(driver):
    """Click the "show all counties" toggle if the state page has one."""
    try:
        button = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.ID, "president-results-table-toggle"))
        )
        driver.execute_script("arguments[0].click();", button)
        time.sleep(1)  # let the expanded table load
    except Exception:
        print("No county-table toggle found. Continuing...")


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

    total_expected = int(soup.find("div", {"id": "total-estimated"}).text.strip().replace(",", ""))
    county_total_votes = int(
        soup.find("span", {"data-testid": "state-results-table-area-votes"}).text.split()[0].replace(",", "")
    )
    percent_in = float(soup.find("span", {"class": "percent-in"}).text.split("%")[0])
    if percent_in in (95.0, 0):
        # NBC caps the displayed figure at 95% and shows 0% before any data
        # loads; both mean "treat as fully reported" for our extrapolation.
        percent_in = 100.0

    votes = {}
    for candidate_row in county_row.find("div", {"class": "county-table"}).find_all("tr", {"class": "row"}):
        name = candidate_row.find("span", class_="cand-cell-name").find("span", {"data-testid": "text--m"}).text.strip()
        count = int(candidate_row.find("td", {"data-type": "votes"}).text.strip().replace(",", ""))
        votes[name] = count

    return {
        "county_name": county_name,
        "total_expected": total_expected,
        "county_total_votes": county_total_votes,
        "percent_in": percent_in,
        "votes": votes,
    }


def _iter_county_results(state_dir):
    raw_div_path = state_dir / "raw_div.txt"
    if not raw_div_path.is_file():
        print(f"{raw_div_path} does not exist.")
        return

    for line in raw_div_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            result = _parse_county_row(line)
        except Exception as e:
            print(f"Error processing a county row in {state_dir.name}. Exception: {e}")
            continue
        if result is not None:
            yield result


def process_all(states_dir=STATES_DIR, output_csv=OUTPUT_CSV):
    """Parse every scraped state's raw_div.txt into a single CSV of county-level results."""
    state_dirs = sorted(d for d in states_dir.iterdir() if d.is_dir())

    all_results = [(state_dir.name.replace("_", " "), result) for state_dir in state_dirs for result in _iter_county_results(state_dir)]

    total_votes_by_candidate = {}
    for _, result in all_results:
        for name, votes in result["votes"].items():
            total_votes_by_candidate[name] = total_votes_by_candidate.get(name, 0) + votes

    ranked_candidates = sorted(total_votes_by_candidate, key=total_votes_by_candidate.get, reverse=True)
    if len(ranked_candidates) > 2:
        print(f"Warning: found more than two candidates, using the top two by vote count: {ranked_candidates[:2]}")
    candidates = ranked_candidates[:2]

    with open(output_csv, "w", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(
            ["State", "County", "Total Expected", "Total Votes", "Percent In"]
            + [f"{name} Real" for name in candidates]
            + [f"{name} Predicted" for name in candidates]
        )

        for state_name, result in all_results:
            real_votes = [result["votes"].get(name, 0) for name in candidates]
            predicted_votes = [round(v * 100 / result["percent_in"]) for v in real_votes]
            writer.writerow(
                [state_name, result["county_name"], result["total_expected"], result["county_total_votes"], result["percent_in"]]
                + real_votes
                + predicted_votes
            )

    print(f"Wrote {len(all_results)} county rows to {output_csv}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-grab", action="store_true", help="Skip scraping and only reprocess already-saved raw HTML")
    args = parser.parse_args()

    if not args.no_grab:
        grab_data()
    process_all()


if __name__ == "__main__":
    main()
