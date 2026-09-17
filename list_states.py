"""Scrape NBC News' presidential-results hub page for the list of per-state result pages."""

import argparse
import json
import re

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.nbcnews.com"
MAIN_URL = f"{BASE_URL}/politics/2024-elections/president-results"
STATE_LINK_PATTERN = re.compile(r"/politics/2024-elections/.+-president-results")


def fetch_state_slugs(url):
    """Return the ordered, de-duplicated list of state slugs linked from the results hub page."""
    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    slugs = []
    seen = set()
    start_collecting = False

    for element in soup.find_all(True):
        if element.name == "h2" and "All Presidential races" in element.get_text():
            start_collecting = True
            continue

        if not start_collecting or element.name != "a" or not element.has_attr("href"):
            continue

        href = element["href"]
        if not STATE_LINK_PATTERN.match(href):
            continue

        slug = href.split("/")[3].removesuffix("-president-results")
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    return slugs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="nbc_states.json", help="Where to write the state slug list")
    args = parser.parse_args()

    slugs = fetch_state_slugs(MAIN_URL)

    with open(args.output, "w") as file:
        json.dump(slugs, file, indent=4)

    print(f"Saved {len(slugs)} states to {args.output}")


if __name__ == "__main__":
    main()
