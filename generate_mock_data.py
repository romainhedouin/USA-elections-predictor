"""Generate a fictional raw_data[_<race>].csv exercising the extrapolation mechanic.

Writes the same schema generate_raw_data.py produces, so it's a drop-in
stand-in for testing or visualization.

Candidate names are the generic "Democrat"/"Republican" placeholders since
this is fictional 2028/2026 data, not a claim about real candidates or
results.

--race selects president/senate/governor (races.py), which determines which
states are actually in play. A handful of scenario states cover:
  - "match" states, where the extrapolated (Predicted) winner agrees with
    whoever is currently leading on raw votes counted (a landslide state
    where reporting order doesn't matter) - both high-reporting ("certain")
    and low-reporting ("projected") examples, for both parties.
  - "mismatch" states, where it doesn't: a handful of large counties for
    one party are mostly counted while a handful of large counties for the
    other party have barely started, so the raw leader and the extrapolated
    winner disagree. This is the scenario the whole tool exists to catch.
  - Every other in-play state gets a single "no data yet" placeholder row.

Only a few real counties are populated per scenario state - exactly like an
actual election night, where most counties haven't reported yet. Any county
of that state we don't list here is simply absent, which downstream
consumers (e.g. a county map) should already treat as "no data".
"""

import argparse
import csv

from races import RACES, race_files
from generate_raw_data import CSV_HEADER

# Per-county scenario data, keyed by state name (races.py's weight tables use
# the same proper names). Each race only uses the subset of these states it
# actually has a race in this cycle (see main()) - e.g. Pennsylvania has no
# 2026 Senate race, so the Senate mock skips it automatically.
SCENARIO_STATES = {
    "Vermont": [  # match, high reporting: Democrat leads raw and predicted everywhere, race is "certain"
        ("Chittenden", 90000, 96, 0.70, 0.30),
        ("Washington", 40000, 100, 0.65, 0.35),
        ("Windham", 25000, 97, 0.68, 0.32),
    ],
    "Wyoming": [  # match, low reporting: Republican leads raw and predicted everywhere, still "projected"
        ("Natrona", 40000, 50, 0.30, 0.70),
        ("Laramie", 55000, 100, 0.40, 0.60),
        ("Campbell", 20000, 10, 0.25, 0.75),
    ],
    "Idaho": [  # match, high reporting: Republican leads raw and predicted everywhere, race is "certain"
        ("Ada", 220000, 97, 0.42, 0.58),
        ("Canyon", 100000, 96, 0.35, 0.65),
        ("Kootenai", 90000, 98, 0.32, 0.68),
    ],
    "Illinois": [  # match, low reporting: Democrat leads raw and predicted everywhere, still "projected"
        ("Cook", 1200000, 40, 0.68, 0.32),
        ("DuPage", 300000, 50, 0.55, 0.45),
        ("Lake", 250000, 30, 0.58, 0.42),
    ],
    "Pennsylvania": [  # mismatch ("red mirage"): Democrat leads raw, Republican leads predicted
        ("Philadelphia", 650000, 95, 0.82, 0.18),
        ("Allegheny", 600000, 92, 0.60, 0.40),
        ("York", 480000, 4, 0.32, 0.68),
        ("Lancaster", 550000, 4, 0.34, 0.66),
        ("Westmoreland", 400000, 6, 0.28, 0.72),
        ("Bucks", 450000, 5, 0.40, 0.60),
        ("Chester", 400000, 5, 0.44, 0.56),
    ],
    "Georgia": [  # mismatch ("blue mirage"): Republican leads raw, Democrat leads predicted
        ("Forsyth", 150000, 90, 0.35, 0.65),
        ("Hall", 90000, 92, 0.30, 0.70),
        ("Cherokee", 140000, 88, 0.33, 0.67),
        ("Fulton", 500000, 5, 0.72, 0.28),
        ("DeKalb", 380000, 6, 0.78, 0.22),
        ("Gwinnett", 420000, 5, 0.62, 0.38),
    ],
}


def build_state_rows(state_name, counties):
    """One CSV row per populated county of a scenario state.

    "State Total Expected" is state-wide and repeated on every row, matching
    what the real scraper writes (NBC only publishes that estimate per state,
    never per county).
    """
    state_total_expected = sum(total_expected for _, total_expected, _, _, _ in counties)

    rows = []
    for county, total_expected, percent_in, dem_share, rep_share in counties:
        county_total_votes = round(total_expected * percent_in / 100)
        dem_real = round(county_total_votes * dem_share)
        rep_real = round(county_total_votes * rep_share)
        rows.append([
            state_name, county, state_total_expected, county_total_votes, float(percent_in),
            dem_real, rep_real,
            round(dem_real * 100 / percent_in), round(rep_real * 100 / percent_in),
            "Democrat", "Republican",
        ])
    return rows


def build_no_data_row(state_name):
    return [state_name, "Statewide", 0, 0, 0.0, 0, 0, 0, 0, "Democrat", "Republican"]


def print_state_summary(state_name, rows):
    real_d = sum(row[5] for row in rows)
    real_r = sum(row[6] for row in rows)
    predicted_d = sum(row[7] for row in rows)
    predicted_r = sum(row[8] for row in rows)
    raw_leader = "Democrat" if real_d > real_r else "Republican"
    predicted_leader = "Democrat" if predicted_d > predicted_r else "Republican"
    flip = "MISMATCH" if raw_leader != predicted_leader else "match"
    print(f"{state_name}: raw leader {raw_leader} ({real_d} vs {real_r}), predicted leader {predicted_leader} ({predicted_d} vs {predicted_r}) -> {flip}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--race", default="president", choices=RACES.keys())
    args = parser.parse_args()

    in_play_states = list(RACES[args.race]["weights"].keys())
    data_states = {name: SCENARIO_STATES[name] for name in in_play_states if name in SCENARIO_STATES}
    output_csv = race_files(args.race)["output_csv"]

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

    print(f"Wrote {len(rows)} rows ({len(data_states)} states with data, {len(in_play_states) - len(data_states)} with none yet) to {output_csv}")


if __name__ == "__main__":
    main()
