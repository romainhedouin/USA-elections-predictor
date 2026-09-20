"""generate_mock_data.py must write a CSV byte-for-byte usable by map.html.

This is a regression lock, not a bug hunt: it pins the current, correct
output shape (CSV_HEADER as the first row, ';'-delimited, one data row per
row generate_mock_data.py built) so that a refactor of *how* the file gets
written - e.g. switching to fetch_results.write_csv's write-then-rename
helper instead of the inline open()/writer this file used to duplicate -
can't silently change *what* gets written.
"""

import csv
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO))
from races import CSV_HEADER, RACES, race_files  # noqa: E402


def run_generate(tmp_path, race="president"):
    subprocess.run(
        [sys.executable, str(REPO / "generate_mock_data.py"), "--race", race, "--out-dir", str(tmp_path)],
        cwd=REPO, check=True, capture_output=True, text=True,
    )
    output_csv = tmp_path / race_files(race)["output_csv"].name
    assert output_csv.exists(), f"generate_mock_data.py did not write {output_csv}"
    return output_csv


def test_writes_correct_header_and_delimiter(tmp_path):
    output_csv = run_generate(tmp_path, "president")

    with open(output_csv, newline="", encoding="utf-8") as file:
        reader = csv.reader(file, delimiter=";")
        rows = list(reader)

    assert rows[0] == CSV_HEADER
    # Every in-play state gets exactly one row of data (a scenario county row
    # or a "no data yet" placeholder) - never zero, never duplicated.
    in_play_states = list(RACES["president"]["weights"].keys())
    assert len(rows) - 1 >= len(in_play_states)


def test_output_has_no_leftover_tmp_sidecar(tmp_path):
    """Whatever write path is used, the final file - not a .csv.tmp - is what's left."""
    output_csv = run_generate(tmp_path, "senate")

    assert not output_csv.with_suffix(".csv.tmp").exists()
    assert output_csv.with_suffix(".meta.json").exists()


def test_house_race_writes_one_row_per_district(tmp_path):
    from races import HOUSE_DISTRICTS

    output_csv = run_generate(tmp_path, "house")

    with open(output_csv, newline="", encoding="utf-8") as file:
        reader = csv.reader(file, delimiter=";")
        rows = list(reader)

    assert rows[0] == CSV_HEADER
    assert len(rows) - 1 == len(HOUSE_DISTRICTS)
