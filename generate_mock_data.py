"""Generate a fictional raw_data[_<race>].csv exercising the extrapolation mechanic.

Writes the same schema fetch_results.py produces, so it's a drop-in
stand-in for testing or visualization.

Candidate names are the generic "Democrat"/"Republican" placeholders since
this is fictional 2028/2026 data, not a claim about real candidates or
results.

--race selects president/senate/governor/house (races.py), which determines
which states (or, for house, districts) are actually in play. A handful of
scenario states cover:
  - "match" states, where the extrapolated (Predicted) winner agrees with
    whoever is currently leading on raw votes counted (a landslide state
    where reporting order doesn't matter) - both high-reporting ("certain")
    and low-reporting ("projected") examples, for both parties.
  - "mismatch" states, where it doesn't: a handful of large counties for
    one party are mostly counted while a handful of large counties for the
    other party have barely started, so the raw leader and the extrapolated
    winner disagree. This is the scenario the whole tool exists to catch.
    House has no mismatch scenario: each row is already one whole district
    race with no sub-units to aggregate, so its own leader can't flip - see
    HOUSE_SCENARIOS below.
  - Every other in-play state (or district) gets a single "no data yet"
    placeholder row.

Only a few real counties are populated per scenario state - exactly like an
actual election night, where most counties haven't reported yet. Any county
of that state we don't list here is simply absent, which downstream
consumers (e.g. a county map) should already treat as "no data".
"""

import argparse
import csv
from pathlib import Path

from fetch_results import write_meta
from races import CSV_HEADER, HOUSE_DISTRICTS, RACES, race_files

# Per-county scenario data, keyed by state name (races.py's weight tables use
# the same proper names). Each race only uses the subset of these states it
# actually has a race in this cycle (see main()) - e.g. Pennsylvania has no
# 2026 Senate race, so the Senate mock skips it automatically.
#
# Tuples are (county, FIPS, expected votes, % in, Democrat share, Republican
# share). The FIPS codes are real, so the map joins these rows to real county
# shapes; the votes are invented. (Real Vermont is reported by township, not
# county, so its county rows here are a demo convenience.)
SCENARIO_STATES = {
    "Vermont": [  # match, high reporting: Democrat leads raw and predicted everywhere, race is "certain"
        ("Chittenden", "50007", 90000, 96, 0.70, 0.30),
        ("Washington", "50023", 40000, 100, 0.65, 0.35),
        ("Windham", "50025", 25000, 97, 0.68, 0.32),
    ],
    "Wyoming": [  # match, low reporting: Republican leads raw and predicted everywhere, still "projected"
        ("Natrona", "56025", 40000, 50, 0.30, 0.70),
        ("Laramie", "56021", 55000, 100, 0.40, 0.60),
        ("Campbell", "56005", 20000, 10, 0.25, 0.75),
    ],
    "Idaho": [  # match, high reporting: Republican leads raw and predicted everywhere, race is "certain"
        ("Ada", "16001", 220000, 97, 0.42, 0.58),
        ("Canyon", "16027", 100000, 96, 0.35, 0.65),
        ("Kootenai", "16055", 90000, 98, 0.32, 0.68),
    ],
    "Illinois": [  # match, low reporting: Democrat leads raw and predicted everywhere, still "projected"
        ("Cook", "17031", 1200000, 40, 0.68, 0.32),
        ("DuPage", "17043", 300000, 50, 0.55, 0.45),
        ("Lake", "17097", 250000, 30, 0.58, 0.42),
    ],
    "Pennsylvania": [  # mismatch ("red mirage"): Democrat leads raw, Republican leads predicted
        ("Philadelphia", "42101", 650000, 95, 0.82, 0.18),
        ("Allegheny", "42003", 600000, 92, 0.60, 0.40),
        ("York", "42133", 480000, 4, 0.32, 0.68),
        ("Lancaster", "42071", 550000, 4, 0.34, 0.66),
        ("Westmoreland", "42129", 400000, 6, 0.28, 0.72),
        ("Bucks", "42017", 450000, 5, 0.40, 0.60),
        ("Chester", "42029", 400000, 5, 0.44, 0.56),
    ],
    "Georgia": [  # mismatch ("blue mirage"): Republican leads raw, Democrat leads predicted
        ("Forsyth", "13117", 150000, 90, 0.35, 0.65),
        ("Hall", "13139", 90000, 92, 0.30, 0.70),
        ("Cherokee", "13057", 140000, 88, 0.33, 0.67),
        ("Fulton", "13121", 500000, 5, 0.72, 0.28),
        ("DeKalb", "13089", 380000, 6, 0.78, 0.22),
        ("Gwinnett", "13135", 420000, 5, 0.62, 0.38),
    ],
}


# A handful of real districts (races.py's HOUSE_DISTRICTS uses the same
# GEOIDs), covering "match" scenarios only - both high-reporting ("certain")
# and low-reporting ("projected"), for both parties. There is no "mismatch"
# scenario for House in this pass: a mismatch needs sub-units reporting at
# different rates to aggregate up into a flipped total (see
# build_state_rows below), and a district-level row has no such sub-units
# yet - see races.py's note on the deferred county-level drill-down.
#
# Tuples are (total expected votes, % in, Democrat share, Republican share).
HOUSE_SCENARIOS = {
    "5000": (350000, 96, 0.68, 0.32),   # Vermont At-Large: match, high reporting, Democrat
    "5600": (280000, 20, 0.25, 0.75),   # Wyoming At-Large: match, low reporting, Republican
    "1601": (320000, 97, 0.30, 0.70),   # Idaho District 1: match, high reporting, Republican
    "1707": (300000, 15, 0.82, 0.18),   # Illinois District 7: match, low reporting, Democrat
}


def build_house_row(geoid, total_expected, percent_in, dem_share, rep_share):
    info = HOUSE_DISTRICTS[geoid]
    total_votes = round(total_expected * percent_in / 100)
    dem_real = round(total_votes * dem_share)
    rep_real = round(total_votes * rep_share)
    return [
        info["state"], info["label"], geoid, "districts",
        total_expected, total_votes, float(percent_in),
        dem_real, rep_real,
        "Democrat", "Republican",
    ]


def build_house_no_data_row(geoid):
    info = HOUSE_DISTRICTS[geoid]
    return [info["state"], info["label"], geoid, "districts", 0, 0, 0.0, 0, 0, "Democrat", "Republican"]


def build_state_rows(state_name, counties):
    """One CSV row per populated county of a scenario state.

    "State Total Expected" is state-wide and repeated on every row, matching
    what the real scraper writes (NBC only publishes that estimate per state,
    never per county).
    """
    state_total_expected = sum(total_expected for _, _, total_expected, _, _, _ in counties)

    rows = []
    for county, fips, total_expected, percent_in, dem_share, rep_share in counties:
        county_total_votes = round(total_expected * percent_in / 100)
        dem_real = round(county_total_votes * dem_share)
        rep_real = round(county_total_votes * rep_share)
        rows.append([
            state_name, county, fips, "counties",
            state_total_expected, county_total_votes, float(percent_in),
            dem_real, rep_real,
            "Democrat", "Republican",
        ])
    return rows


def build_no_data_row(state_name):
    return [state_name, "Statewide", "", "counties", 0, 0, 0.0, 0, 0, "Democrat", "Republican"]


def print_state_summary(state_name, rows):
    # Predicted is no longer a CSV column (see races.CSV_HEADER) - estimate.js
    # computes it client-side now, using the historical-swing model where a
    # baseline exists. None of these mock rows have one, so replicate its
    # flat fallback here just for this debug summary.
    real_d = sum(row[7] for row in rows)
    real_r = sum(row[8] for row in rows)
    predicted_d = sum(row[7] * 100 / row[6] for row in rows if row[6])
    predicted_r = sum(row[8] * 100 / row[6] for row in rows if row[6])
    raw_leader = "Democrat" if real_d > real_r else "Republican"
    predicted_leader = "Democrat" if predicted_d > predicted_r else "Republican"
    flip = "MISMATCH" if raw_leader != predicted_leader else "match"
    print(f"{state_name}: raw leader {raw_leader} ({real_d} vs {real_r}), predicted leader {predicted_leader} ({predicted_d} vs {predicted_r}) -> {flip}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--race", default="president", choices=RACES.keys())
    parser.add_argument("--out-dir", default=".", help="Where to write the CSV (default: current directory)")
    args = parser.parse_args()

    output_csv = Path(args.out_dir) / race_files(args.race)["output_csv"].name

    if args.race == "house":
        # Districts, not states - HOUSE_DISTRICTS/HOUSE_SCENARIOS are both
        # keyed by GEOID, so this is its own small loop rather than forcing
        # county-shaped build_state_rows()/build_no_data_row() to fit.
        rows = [
            build_house_row(geoid, *HOUSE_SCENARIOS[geoid]) if geoid in HOUSE_SCENARIOS
            else build_house_no_data_row(geoid)
            for geoid in HOUSE_DISTRICTS
        ]
        data_states = HOUSE_SCENARIOS
        in_play_states = HOUSE_DISTRICTS
    else:
        in_play_states = list(RACES[args.race]["weights"].keys())
        data_states = {name: SCENARIO_STATES[name] for name in in_play_states if name in SCENARIO_STATES}

        rows = []
        for state_name in in_play_states:
            if state_name not in data_states:
                rows.append(build_no_data_row(state_name))
                continue

            state_rows = build_state_rows(state_name, data_states[state_name])
            print_state_summary(state_name, state_rows)
            rows.extend(state_rows)

    with open(output_csv, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)

    race = RACES[args.race]
    write_meta(output_csv, args.race, race["nbc_cycle"], rows, source="mock")
    unit = "districts" if args.race == "house" else "states"
    print(f"Wrote {len(rows)} rows ({len(data_states)} {unit} with data, {len(in_play_states) - len(data_states)} with none yet) to {output_csv}")
    print(f"Mock {race['label']} {race['election_year']} data - not a real result.")


if __name__ == "__main__":
    main()
