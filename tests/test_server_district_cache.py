"""server.py's House district drill-down cache (_house_district_payload).

Unlike test_house.py (which drives the real page through Selenium against a
stubbed HTTP proxy), these tests call server.py's cache function directly -
no browser, no sockets - to lock in two invariants the module's own comments
claim but don't enforce in code:

  * _district_cache is bounded to the 435 real House districts (the comment
    above it says so explicitly), so a geoid that isn't one of those 435
    must never be stored in it, no matter how many times it's requested.
  * concurrent first-time requests for the same still-uncached geoid must
    collapse into a single upstream nbc_api.district_results() call, not one
    per requester - that's the "burst of visitors opening the same close
    district at once" guard the DISTRICT_CACHE_SECONDS docstring describes.
"""

import threading
import time

import server


def _known_geoid():
    """Any real geoid from static/house_districts.json, for tests that need
    one that actually reaches nbc_api.district_results()."""
    return next(iter(server.nbc_api._HOUSE_DISTRICTS))


def _fake_district_payload(geoid):
    return {
        "geoid": geoid,
        "label": "Test District",
        "state": "Testland",
        "geography": "counties",
        "county_level": True,
        "total_expected": 1000,
        "percent_in": 100.0,
        "candidates": {"dem": "A", "gop": "B"},
        "areas": [],
        "last_modified": None,
    }


def test_geoid_outside_the_435_known_districts_is_not_cached(monkeypatch):
    """A geoid that matches DISTRICT_ROUTE's ([0-9]{4}) pattern but is not one
    of the 435 real districts (e.g. "9999") must not end up as an entry in
    _district_cache - the comment above the cache says its size is bounded to
    435 "by construction"; that's only true if unknown geoids are rejected
    before they're written in."""
    bogus_geoid = "9999"
    assert bogus_geoid not in server.nbc_api._HOUSE_DISTRICTS

    monkeypatch.setattr(server.nbc_api, "district_results", lambda geoid, cycle: None)
    server._district_cache.clear()
    try:
        status, _body = server._house_district_payload(bogus_geoid)
        assert status == 404
        assert bogus_geoid not in server._district_cache, (
            "an unknown geoid was written into _district_cache - the cache's "
            "435-entry bound no longer holds, since any of the 10,000 "
            "possible 4-digit paths can add one"
        )
    finally:
        server._district_cache.clear()


def test_concurrent_requests_for_same_uncached_geoid_fetch_upstream_once(monkeypatch):
    """Several requests for the same not-yet-cached district arriving at once
    (the exact scenario DISTRICT_CACHE_SECONDS's docstring calls out - "a
    burst of visitors opening the same close district at once") must share a
    single upstream fetch, not fire one nbc_api.district_results() call per
    requester."""
    geoid = _known_geoid()
    calls = []
    calls_lock = threading.Lock()

    def fake_district_results(g, cycle):
        with calls_lock:
            calls.append(g)
        time.sleep(0.25)  # wide enough that concurrent callers overlap
        return _fake_district_payload(g)

    monkeypatch.setattr(server.nbc_api, "district_results", fake_district_results)
    server._district_cache.clear()
    try:
        results = [None] * 8
        errors = []

        def worker(i):
            try:
                results[i] = server._house_district_payload(geoid)
            except Exception as exc:  # noqa: BLE001 - surfaced via `errors` below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(results))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"worker thread(s) raised: {errors}"
        assert all(r is not None for r in results), "a worker never returned"
        assert len(calls) == 1, (
            f"expected exactly one upstream fetch for a burst of requests on "
            f"the same uncached geoid, got {len(calls)}"
        )
        assert all(r == (200, results[0][1]) for r in results)
    finally:
        server._district_cache.clear()
