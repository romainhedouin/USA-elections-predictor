"""legacy/generate_raw_data.py's process_all() must write rows matching
races.CSV_HEADER position-for-position, not just carry that header as a
label on top of an older, differently-shaped row.

This is a regression lock for a real bug: process_all() used to build rows
in the pre-FIPS/Geography shape (State, County, StateTotalExpected,
CountyTotalVotes, PercentIn, DemReal, RepReal, DemPredicted, RepPredicted,
DemName, RepName) but wrote them under the current 11-column CSV_HEADER
(State, Area, FIPS, Geography, State Total Expected, Total Votes, Percent
In, Democrat Real, Republican Real, Democrat Name, Republican Name) - both
lists have 11 elements so csv.writer never complained, but 7 of the 11
columns ended up holding the wrong value under the wrong label (e.g.
"Democrat Real" held a flat-extrapolated *predicted* count, not the real
one; "FIPS" held the state's total-expected vote count).

Builds a fake states/<state>/raw_div.txt - the same raw HTML _grab_state()
would have saved - and drives process_all() directly (no Selenium, no
network) so this only exercises the CSV-writing logic.
"""

import csv
import importlib.util
from pathlib import Path

import pytest

from races import CSV_HEADER

# legacy/generate_raw_data.py imports bs4 at module level.
pytest.importorskip("bs4")

REPO = Path(__file__).resolve().parent.parent
LEGACY = REPO / "legacy"


def _load_generate_raw_data():
    """Import legacy/generate_raw_data.py as a module.

    It does `from races import ...`; conftest.py puts the repo root on sys.path.
    """
    spec = importlib.util.spec_from_file_location("generate_raw_data", LEGACY / "generate_raw_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# One county's worth of raw HTML in the shape _grab_state() writes to
# raw_div.txt: the state-wide "total-estimated" tag NBC only publishes once,
# immediately followed by the county's own <div data-testid="county-row">.
# Percent-in is deliberately not 100 (50%) so that a flat extrapolation
# ("predicted") would differ from the real count - that's what distinguishes
# the old, buggy column values from the correct ones.
_RAW_DIV_LINE = (
    '<div id="total-estimated">9999</div>'
    '<div data-testid="county-row">'
    '<span class="dib dn-m">Test County</span>'
    '<span data-testid="state-results-table-area-votes">555 votes</span>'
    '<span class="percent-in">50% in</span>'
    '<div class="county-table">'
    '<tr class="row">'
    '<td><span class="cand-cell-name"><span data-testid="text--m">Jane Dem</span></span></td>'
    '<td data-type="party">D</td>'
    '<td data-type="votes">100</td>'
    "</tr>"
    '<tr class="row">'
    '<td><span class="cand-cell-name"><span data-testid="text--m">John Rep</span></span></td>'
    '<td data-type="party">R</td>'
    '<td data-type="votes">80</td>'
    "</tr>"
    "</div>"
    "</div>"
)


def test_process_all_writes_rows_matching_csv_header_positions(tmp_path):
    generate_raw_data = _load_generate_raw_data()

    states_dir = tmp_path / "states"
    state_dir = states_dir / "TestState"
    state_dir.mkdir(parents=True)
    (state_dir / "raw_div.txt").write_text(_RAW_DIV_LINE + "\n\n", encoding="utf-8")

    output_csv = tmp_path / "raw_data.csv"
    generate_raw_data.process_all(states_dir, output_csv)

    with open(output_csv, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file, delimiter=";")
        assert reader.fieldnames == CSV_HEADER
        rows = list(reader)

    assert len(rows) == 1
    row = rows[0]
    assert row["State"] == "TestState"
    assert row["Area"] == "Test County"
    assert row["State Total Expected"] == "9999"
    assert row["Total Votes"] == "555"
    assert row["Percent In"] == "50.0"
    # The real counts, not a 100/PercentIn-extrapolated "predicted" number
    # (100 -> 200, 80 -> 160 would be the old, buggy values).
    assert row["Democrat Real"] == "100"
    assert row["Republican Real"] == "80"
    assert row["Democrat Name"] == "Jane Dem"
    assert row["Republican Name"] == "John Rep"
