"""Scrape NBC News' results hub page for the list of per-state result pages.

Works for president, Senate, or governor races (--race) - they all share the
same "All {Race} races" hub page and {state}-{race}-results link pattern.
"""

import argparse
import json
import re
import sys

import requests
from bs4 import BeautifulSoup

from races import RACES, race_files

BASE_URL = "https://www.nbcnews.com"
# NBC 403s the default python-requests agent on some edges.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
TIMEOUT = (5, 30)  # (connect, read) seconds - without this a stalled socket hangs forever


def build_main_url(race, nbc_cycle):
    slug = RACES[race]["nbc_slug"]
    return f"{BASE_URL}/politics/{nbc_cycle}-elections/{slug}-results"


def fetch_state_slugs(url, race, nbc_cycle):
    """Return the ordered, de-duplicated list of state slugs linked from the results hub page."""
    slug = RACES[race]["nbc_slug"]
    link_pattern = re.compile(rf"/politics/{nbc_cycle}-elections/.+-{slug}-results")

    response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    slugs = []
    seen = set()
    start_collecting = False

    for element in soup.find_all(True):
        if element.name == "h2" and RACES[race]["hub_h2"] in element.get_text():
            start_collecting = True
            continue

        if not start_collecting or element.name != "a" or not element.has_attr("href"):
            continue

        href = element["href"]
        if not link_pattern.match(href):
            continue

        state_slug = href.split("/")[3].removesuffix(f"-{slug}-results")
        if state_slug not in seen:
            seen.add(state_slug)
            slugs.append(state_slug)

    return slugs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--race", default="president", choices=RACES.keys())
    parser.add_argument(
        "--nbc-cycle", "--cycle", dest="nbc_cycle",
        help="Year in NBC's URL path (default: the race's nbc_cycle in races.py)",
    )
    parser.add_argument("--output", help="Where to write the state slug list (default: nbc_states[_<race>].json)")
    args = parser.parse_args()

    nbc_cycle = args.nbc_cycle or RACES[args.race]["nbc_cycle"]
    output = args.output or race_files(args.race)["nbc_states_file"]
    url = build_main_url(args.race, nbc_cycle)
    slugs = fetch_state_slugs(url, args.race, nbc_cycle)

    if not slugs:
        # Almost always means NBC changed the hub heading or the link pattern.
        # Writing an empty list here would silently wipe a good file and make
        # the next scrape a no-op, so refuse instead.
        sys.exit(
            f"No state links found on {url}.\n"
            f"Expected an h2 containing {RACES[args.race]['hub_h2']!r} followed by "
            f"/politics/{nbc_cycle}-elections/<state>-{RACES[args.race]['nbc_slug']}-results links.\n"
            f"{output} left untouched."
        )

    with open(output, "w", encoding="utf-8") as file:
        json.dump(slugs, file, indent=4)

    print(f"Saved {len(slugs)} states to {output}")


if __name__ == "__main__":
    main()
