"""Client for NBC News' election-results JSON API.

The results pages are a Next.js app that fetches this API itself; the endpoint
is unauthenticated and needs no cookies or query parameters. Going straight to
it instead of driving a browser is ~25x faster and, more importantly, it hands
us structured party codes and county FIPS that the rendered HTML never exposed.

    /firecracker/api/v2/national-results/{cycle}-elections/{race}-results
    /firecracker/api/v2/state-results/{cycle}-elections/{state}-{race}-results

`cycle` is the year in NBC's own URL path, which is the year of the election
being reported - see races.py, where it is kept separate from the election we
are notionally predicting.

GEOGRAPHY: NBC does not report every state by county. 43 states report by
county or parish; Connecticut, Maine, Massachusetts, New Hampshire, Vermont
report by township, Rhode Island by municipality, Alaska by legislative
district and DC by ward. Those eight have no county-level numbers at all, so
the honest thing is to say so rather than paint a county with one town's
votes. `payload["geography"]` tells us which case we are in.
"""

import json
from pathlib import Path

import requests

BASE_URL = "https://www.nbcnews.com/firecracker/api/v2"
# NBC's edge is happy with a plain client; identify honestly rather than
# impersonating a browser.
USER_AGENT = "usa-elections-predictor/1.0 (+https://github.com/romainhedouin/USA-elections-predictor)"
TIMEOUT = (5, 30)  # (connect, read) seconds

# Geographies whose reporting units are actual counties, and therefore line up
# with the county boundaries the map draws.
COUNTY_GEOGRAPHIES = {"counties", "parishes"}

# (state slug) -> {NBC area name: 5-digit FIPS}. Built once by
# build_fips_table.py; see that script for why this is a table and not a
# name match done at runtime.
FIPS_TABLE = json.loads((Path(__file__).parent / "county_fips.json").read_text(encoding="utf-8"))


def _get(url):
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def state_slugs(race_slug, cycle):
    """Every state slug NBC publishes for this race, in the order it lists them."""
    payload = _get(f"{BASE_URL}/national-results/{cycle}-elections/{race_slug}-results")

    # The state list is nested a few levels down inside the page payload and
    # NBC has moved it before, so walk for the shape rather than a fixed path.
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "stateName" in node and "href" in node:
                found.append(node["href"].rsplit("/", 1)[-1].removesuffix(f"-{race_slug}-results"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return list(dict.fromkeys(found))  # NBC repeats states across page sections


def state_results(state_slug, race_slug, cycle):
    """One state's results, normalised.

    Returns None if NBC has no race of this type in this state (a 404), which
    is routine: most states have no Senate or governor race in a given cycle.
    """
    url = f"{BASE_URL}/state-results/{cycle}-elections/{state_slug}-{race_slug}-results"
    try:
        payload = _get(url)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return None
        raise

    races = payload.get("races") or []
    if not races:
        return None
    # Maine and Nebraska split their electoral votes, so NBC publishes a second
    # race holding just the congressional districts. The state-wide one is the
    # one carrying every reporting unit.
    race = max(races, key=lambda r: len(r.get("areas") or []))
    summary = race.get("summary") or {}

    geography = payload.get("geography")
    fips_by_name = FIPS_TABLE.get(state_slug, {}) if geography in COUNTY_GEOGRAPHIES else {}

    remaining = (summary.get("estimatedVotesRemaining") or {}).get("value") or 0
    areas = []
    for area in race.get("areas") or []:
        areas.append({
            "name": area["name"],
            "fips": fips_by_name.get(area["name"], ""),
            "percent_in": float(area.get("percentIn") or 0),
            "votes": int(area.get("votes") or 0),
            "by_party": _votes_by_party(area.get("candidates") or []),
        })

    return {
        "state": payload.get("stateName") or state_slug,
        "geography": geography,
        "county_level": geography in COUNTY_GEOGRAPHIES,
        "total_expected": int(summary.get("votes") or 0) + int(remaining),
        "percent_in": float(summary.get("percentIn") or 0),
        "last_modified": payload.get("lastModified"),
        "candidates": _leading_by_party(summary.get("candidates") or []),
        "areas": areas,
    }


def _votes_by_party(candidates):
    """Votes keyed by NBC's party code ("dem" / "gop" / "other" / ...)."""
    totals = {}
    for candidate in candidates:
        party = candidate.get("party")
        totals[party] = totals.get(party, 0) + int(candidate.get("votes") or 0)
    return totals


def _leading_by_party(candidates):
    """The leading candidate's name per party, state-wide.

    Party comes from NBC rather than from candidate order: ranking by votes
    would label whoever happens to be ahead as the Democrat, and a same-party
    runoff would silently drop one side entirely.
    """
    best = {}
    for candidate in candidates:
        party = candidate.get("party")
        votes = int(candidate.get("votes") or 0)
        if party not in best or votes > best[party][1]:
            best[party] = (candidate.get("name", ""), votes)
    return {party: name for party, (name, _) in best.items()}
