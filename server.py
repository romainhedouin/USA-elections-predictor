#!/usr/bin/env python3
"""Always-on web server + in-process refresher for the USA election map.

Serves map.html (from the repo directory, baked into the image) and the
raw_data*.csv files (from DATA_DIR, which on Railway is a mounted volume),
and refreshes those CSVs on a timer from a background thread. Also proxies
GET /house-district/<geoid> - a single House district's own county breakdown,
fetched from NBC live, on demand, when someone opens that district's
drill-down - see _house_district_payload for why that's not on the timer too.

WHY ONE SERVICE AND NOT A CRON JOB
    A Railway volume can only be attached to a single service, and Railway's
    native cron floors at 5-minute intervals - too slow for election night.
    So the refresher lives inside the web process, next to the volume.

WHY THE STDLIB AND NOT FLASK
    This app has a handful of routes, most of which are "send a file from disk".
    Flask alone would still want gunicorn in front of it for production, and
    gunicorn's default multi-worker model would fork N copies of this process
    - which means N copies of the scheduler thread, all racing to write the
    same CSVs on the same volume. Avoiding that would mean an external lock or
    --workers 1, at which point gunicorn is buying nothing. http.server's
    ThreadingHTTPServer is a single process with a thread per request: exactly
    one scheduler, no coordination problem, and the deploy's dependency list
    stays at `requests` (which fetch_results.py needs anyway). The traffic
    profile here is a static page plus three CSVs, which ThreadingHTTPServer
    handles comfortably; if this ever needs to survive a real traffic spike,
    put a CDN in front rather than adding a WSGI stack behind.

ENVIRONMENT
    PORT             int, default 8000. Railway injects this.
    DATA_DIR         where the CSVs live, default ./data. Absolute paths work
                     (Railway mounts the volume at an absolute path).
    REFRESH_SECONDS  int, default 900.
    RACES            comma-separated subset of president,senate,governor,house.
                     Default: all four.
    DATA_YEAR        optional; the cycle to pull. Also accepts NBC_CYCLE.
    FETCH_TIMEOUT    int seconds, hard kill for one fetch, default 300.
    SEED_ON_BOOT     "0" disables the cold-start mock seed. Default on.
    DEFAULT_RACE     which race a visitor lands on: president (default),
                     senate, governor or house. Injected into the page; it
                     does not affect what is fetched - that is RACES.
    REFRESH_ENABLED  "0" pauses fetching entirely. The site stays up and keeps
                     serving whatever is already on the volume; nothing is
                     pulled from upstream. Useful out of season, and the switch
                     you want if you need to stop traffic in a hurry.
    DISTRICT_CACHE_SECONDS  int, default 60. How long a House district's
                     on-demand county breakdown (GET /house-district/<geoid>)
                     is cached in memory before the next click re-fetches it
                     from NBC. Unrelated to REFRESH_SECONDS: this is fetched
                     per click, not on the scheduler, which is the whole point
                     - see /house-district/<geoid> below.
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import nbc_api
# Aliased: this module already uses RACES for its own "which races are
# actively refreshed" tuple (see ALL_RACES/_parse_races below) - importing
# races.py's config dict under the same name would silently shadow it.
from races import RACES as RACE_CONFIG

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# The repo directory: wherever this file lives. map.html, fetch_results.py,
# generate_mock_data.py and races.py are all its neighbours. This is baked
# into the image at build time and is NOT the volume.
REPO_DIR = Path(__file__).resolve().parent

# Checked-in reference data (county FIPS lookup, district topology, historical
# baselines) that map.html fetches over HTTP. Distinct from DATA_DIR below,
# which holds the CSVs a refresh overwrites.
STATIC_DATA_DIR = REPO_DIR / "static"

PORT = int(os.environ.get("PORT", "8000"))
DATA_DIR = Path(os.environ.get("DATA_DIR", REPO_DIR / "data")).resolve()
REFRESH_SECONDS = max(30, int(os.environ.get("REFRESH_SECONDS", "900")))
# House's per-district county breakdown (unlike the scheduled CSVs) is fetched
# from NBC on demand, one request per click on the House tab's drill-down -
# see _house_district_payload. This just guards against a burst of visitors
# opening the same close district at once.
DISTRICT_CACHE_SECONDS = max(15, int(os.environ.get("DISTRICT_CACHE_SECONDS", "60")))
FETCH_TIMEOUT = max(30, int(os.environ.get("FETCH_TIMEOUT", "300")))
# NBC_CYCLE kept as an alias so an already-deployed service keeps working.
DATA_YEAR = (os.environ.get("DATA_YEAR") or os.environ.get("NBC_CYCLE") or "").strip()
SEED_ON_BOOT = os.environ.get("SEED_ON_BOOT", "1") not in ("0", "false", "no")
REFRESH_ENABLED = os.environ.get("REFRESH_ENABLED", "1") not in ("0", "false", "no")

ALL_RACES = ("president", "senate", "governor", "house")


def _parse_races(raw):
    """RACES env -> validated tuple. Unknown names are dropped with a warning."""
    if not raw or not raw.strip():
        return ALL_RACES
    wanted, unknown = [], []
    for name in raw.split(","):
        name = name.strip().lower()
        if not name:
            continue
        (wanted if name in ALL_RACES else unknown).append(name)
    if unknown:
        logging.warning("ignoring unknown race(s) in RACES: %s", ", ".join(unknown))
    return tuple(dict.fromkeys(wanted)) or ALL_RACES


RACES = _parse_races(os.environ.get("RACES"))


def _parse_default_race(raw):
    """Which race the page opens on. Unrelated to which races get refreshed."""
    name = (raw or "").strip().lower()
    if not name:
        return "president"
    if name not in ALL_RACES:
        logging.warning("DEFAULT_RACE=%r is not one of %s - falling back to president",
                        raw, ", ".join(ALL_RACES))
        return "president"
    return name


DEFAULT_RACE = _parse_default_race(os.environ.get("DEFAULT_RACE"))


def csv_name(race):
    """President keeps the unsuffixed name, matching the repo's race_files()."""
    return "raw_data.csv" if race == "president" else f"raw_data_{race}.csv"


def meta_name(race):
    return csv_name(race).replace(".csv", ".meta.json")


CSV_NAMES = {csv_name(r) for r in ALL_RACES}  # every race is servable; only RACES gets refreshed
# The sidecars fetch_results.py writes next to each CSV. map.html reads them to
# say whether it is showing real results or fixtures, and how stale they are.
# They live on the volume with the CSVs, NOT in the repo, so they have to be
# routed alongside them - otherwise they 404 and the page loses its provenance.
META_NAMES = {meta_name(r) for r in ALL_RACES}

# --------------------------------------------------------------------------
# Logging: one line per event, key=value tail so it greps well in Railway's
# log viewer. No timestamp of our own - Railway stamps every line already -
# but we keep the level, which it does not.
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-5s %(message)s",
    stream=sys.stdout,
)
# Railway reads stdout; line buffering makes logs show up live instead of in
# 8KB bursts.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except AttributeError:  # pragma: no cover - Python < 3.7
    pass

log = logging.getLogger("electionmap")

# --------------------------------------------------------------------------
# Shared health state
# --------------------------------------------------------------------------

_state_lock = threading.Lock()
STATE = {
    race: {
        "last_success": None,       # ISO-8601 UTC of last CSV successfully replaced
        "last_attempt": None,
        "last_error": None,         # str, cleared on success
        "last_duration_seconds": None,
        "consecutive_failures": 0,
        "source": None,             # "fetch" | "mock-seed"
    }
    for race in ALL_RACES
}
BOOT_TIME = time.time()
STOP = threading.Event()


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _record(race, *, ok, duration, error=None, source="fetch"):
    with _state_lock:
        entry = STATE[race]
        entry["last_attempt"] = _now_iso()
        entry["last_duration_seconds"] = round(duration, 2)
        if ok:
            entry["last_success"] = _now_iso()
            entry["last_error"] = None
            entry["consecutive_failures"] = 0
            entry["source"] = source
        else:
            entry["last_error"] = error
            entry["consecutive_failures"] += 1


# --------------------------------------------------------------------------
# Refresh: run the CLI as a subprocess, into a staging dir, then swap.
# --------------------------------------------------------------------------

def _run(argv, timeout, label):
    """Run a child process with a hard timeout, killing the whole process group.

    subprocess.run(timeout=...) only kills the direct child; a fetcher that
    spawned something of its own would leave orphans holding the CPU. Railway
    bills by the second and an unbounded task run is the classic footgun, so
    we start a new session and kill the group.
    """
    proc = subprocess.Popen(
        argv,
        cwd=str(REPO_DIR),          # the CLI imports races.py from the repo dir
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,     # own process group, so killpg is safe
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, (out or "").strip()
    except subprocess.TimeoutExpired:
        log.error("%s exceeded hard timeout of %ss - killing process group", label, timeout)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            out, _ = proc.communicate(timeout=10)
        except Exception:
            out = ""
        return -9, (out or "").strip()


def _looks_like_csv(path):
    """Cheap sanity check: a header line and at least one data row.

    A fetcher that dies halfway through can leave a truncated file. We only
    promote a staged file that passes this, so a bad run can never replace
    good data with garbage.
    """
    try:
        if path.stat().st_size < 32:
            return False, "file too small"
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            header = handle.readline()
            if "State" not in header or ";" not in header:
                return False, "missing/odd header row"
            rows = sum(1 for _ in handle)
        if rows < 1:
            return False, "header only, no data rows"
        return True, rows
    except OSError as exc:
        return False, f"unreadable: {exc}"


def refresh_race(race, *, use_mock=False):
    """Refresh one race's CSV. Returns True on success.

    Never raises: every failure path logs and returns False, leaving whatever
    CSV is already on the volume untouched. Stale data beats no data.
    """
    started = time.monotonic()
    label = f"{'mock' if use_mock else 'fetch'} race={race}"
    staging = DATA_DIR / ".staging"
    target = DATA_DIR / csv_name(race)

    try:
        staging.mkdir(parents=True, exist_ok=True)
        script = "generate_mock_data.py" if use_mock else "fetch_results.py"
        argv = [sys.executable, str(REPO_DIR / script), "--race", race,
                "--out-dir", str(staging)]
        if DATA_YEAR and not use_mock:
            argv += ["--nbc-cycle", DATA_YEAR]

        code, output = _run(argv, FETCH_TIMEOUT, label)
        duration = time.monotonic() - started

        if code != 0:
            tail = output.splitlines()[-1] if output else "(no output)"
            _record(race, ok=False, duration=duration,
                    error=f"{script} exited {code}: {tail[:400]}")
            log.error("refresh race=%s status=failed exit=%s duration=%.1fs kept=%s | %s",
                      race, code, duration, target.exists(), tail[:400])
            return False

        staged = staging / csv_name(race)
        if not staged.exists():
            _record(race, ok=False, duration=duration,
                    error=f"{script} exited 0 but wrote no {csv_name(race)} "
                          f"(does it support --out-dir?)")
            log.error("refresh race=%s status=failed reason=no-output-file duration=%.1fs kept=%s",
                      race, duration, target.exists())
            return False

        ok, detail = _looks_like_csv(staged)
        if not ok:
            _record(race, ok=False, duration=duration, error=f"staged CSV rejected: {detail}")
            log.error("refresh race=%s status=failed reason=%s duration=%.1fs kept=%s",
                      race, detail, duration, target.exists())
            staged.unlink(missing_ok=True)
            return False

        # os.replace is atomic within a filesystem, and .staging lives inside
        # DATA_DIR precisely so it is the same filesystem as the volume.
        # A reader either sees the whole old file or the whole new one.
        os.replace(staged, target)

        # The .meta.json sidecar rides along with its CSV. Promote it second
        # and only on success: a stale sidecar next to fresh numbers would have
        # the page reporting the wrong provenance and the wrong age, which is
        # worse than having no sidecar at all (the page degrades quietly then).
        staged_meta = staging / meta_name(race)
        if staged_meta.exists():
            os.replace(staged_meta, DATA_DIR / meta_name(race))
        else:
            log.warning("refresh race=%s wrote no %s - the page will show no "
                        "source or freshness for this race", race, meta_name(race))

        duration = time.monotonic() - started
        _record(race, ok=True, duration=duration,
                source="mock-seed" if use_mock else "fetch")
        log.info("refresh race=%s status=ok rows=%s bytes=%s duration=%.1fs source=%s",
                 race, detail, target.stat().st_size, duration,
                 "mock" if use_mock else "fetch")
        return True

    except Exception as exc:  # noqa: BLE001 - the whole point is to not die
        duration = time.monotonic() - started
        _record(race, ok=False, duration=duration, error=f"{type(exc).__name__}: {exc}")
        log.exception("refresh race=%s status=crashed duration=%.1fs kept=%s",
                      race, duration, target.exists())
        return False


def seed_if_empty():
    """Cold start: a fresh volume has no CSVs, so map.html would render its
    error page. Generate mock data first (no network, ~instant) so the site is
    coherent from the first request; the real fetch overwrites it moments
    later for whichever races this service refreshes."""
    # Seed EVERY race, not just the refreshed ones. The page has a tab per
    # race and fetches that race's CSV on click, so a race left unseeded is a
    # tab that 404s. Deliberately useful: pointing RACES at president alone
    # keeps the live pull to the one race that has real results today, while
    # Senate, Governor and House sit on clearly-labelled mock fixtures until
    # the 2026 general election is published.
    missing = [r for r in ALL_RACES if not (DATA_DIR / csv_name(r)).exists()]
    if not missing:
        log.info("boot seed skipped - CSVs already present in %s", DATA_DIR)
        return
    if not SEED_ON_BOOT:
        log.warning("boot seed disabled (SEED_ON_BOOT=0); missing: %s", ", ".join(missing))
        return
    log.info("boot seed starting for %s (no CSVs found in %s)", ", ".join(missing), DATA_DIR)
    for race in missing:
        refresh_race(race, use_mock=True)


def scheduler_loop():
    """Refresh every race, sleep, repeat - forever, whatever happens.

    Wrapped so that no exception can end the thread: if the loop body itself
    blows up we log it and keep going, because a dead scheduler on election
    night is a silently frozen map.
    """
    if not REFRESH_ENABLED:
        log.warning("scheduler PAUSED (REFRESH_ENABLED=0) - serving existing "
                    "data, not fetching. Unset it to resume.")
        return
    log.info("scheduler started interval=%ss races=%s timeout=%ss",
             REFRESH_SECONDS, ",".join(RACES), FETCH_TIMEOUT)
    while not STOP.is_set():
        cycle_started = time.monotonic()
        try:
            results = {race: refresh_race(race) for race in RACES}
            ok = sum(1 for v in results.values() if v)
            log.info("cycle done ok=%d/%d duration=%.1fs next_in=%ss",
                     ok, len(results), time.monotonic() - cycle_started, REFRESH_SECONDS)
        except Exception:  # noqa: BLE001
            log.exception("scheduler cycle crashed - continuing")
        # Interruptible sleep so SIGTERM doesn't wait out the full interval.
        STOP.wait(REFRESH_SECONDS)
    log.info("scheduler stopped")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
# Same character class as SAFE_NAME, one directory level down - the only
# subdirectory this server ever serves out of, so no general traversal logic.
SAFE_STATIC_PATH = re.compile(r"^static/[A-Za-z0-9._-]+$")

# path -> (mtime_ns, bytes). Bounded by construction: only CSV/meta/static
# paths the router already whitelists ever get read, so at most a handful of
# entries (a few races' worth of CSV + meta, well under 1MB total).
_FILE_CACHE = {}

DISTRICT_ROUTE = re.compile(r"^/house-district/([0-9]{4})$")
# geoid -> (fetched_at monotonic, status, body bytes). Bounded by
# construction: there are only 435 possible districts, so this can never grow
# past that even under a full-scale election-night audience.
_district_cache = {}
_district_cache_lock = threading.Lock()


def _house_district_payload(geoid):
    """A district's own county-level (or equivalent) breakdown, as JSON.

    Fetched from NBC live, in the request thread, on every cache miss - unlike
    the scheduled races, this is deliberately NOT part of refresh_race()/
    scheduler_loop(). The whole point of drilling down client-side is to keep
    NBC's per-district page (there are 435 of them) out of the timer entirely
    and only ever fetch the one district a visitor actually clicks.

    Returns (status_code, body_bytes).
    """
    now = time.monotonic()
    with _district_cache_lock:
        cached = _district_cache.get(geoid)
        if cached and now - cached[0] < DISTRICT_CACHE_SECONDS:
            return cached[1], cached[2]

    cycle = DATA_YEAR or RACE_CONFIG["house"]["nbc_cycle"]
    try:
        result = nbc_api.district_results(geoid, cycle)
    except Exception as exc:  # noqa: BLE001 - one bad district must not 500 the process
        log.warning("house-district geoid=%s status=error err=%s", geoid, exc)
        status, body = 502, json.dumps({"error": "upstream fetch failed"}).encode("utf-8")
    else:
        if result is None:
            status, body = 404, json.dumps({"error": "no such district or no NBC page for it yet"}).encode("utf-8")
        else:
            # Every county NBC lists for this district, reporting or not -
            # map.html shows "no data yet" for the ones at 0%, the same way
            # it already does for the other three races' county maps/tables,
            # rather than silently omitting them. Real counts only - no
            # Predicted here either; estimate.js projects these the same way
            # it projects the CSV-sourced areas.
            areas = [
                {
                    "name": area["name"],
                    "fips": area["fips"],
                    "percentIn": area["percent_in"],
                    "votes": area["votes"],
                    "demReal": area["by_party"].get("dem", 0),
                    "repReal": area["by_party"].get("gop", 0),
                }
                for area in result["areas"]
            ]
            payload = {
                "geoid": result["geoid"],
                "label": result["label"],
                "state": result["state"],
                "geography": result["geography"],
                "countyLevel": result["county_level"],
                "totalExpected": result["total_expected"],
                "percentIn": result["percent_in"],
                "demName": result["candidates"].get("dem", "Democrat"),
                "repName": result["candidates"].get("gop", "Republican"),
                "lastModified": result["last_modified"],
                "areas": areas,
            }
            status, body = 200, json.dumps(payload).encode("utf-8")

    with _district_cache_lock:
        _district_cache[geoid] = (now, status, body)
    return status, body


def health_payload():
    """Per-race: last success, last error, and how old the CSV on disk is."""
    races = {}
    with _state_lock:
        snapshot = {race: dict(entry) for race, entry in STATE.items()}
    now = time.time()
    servable = 0
    for race in ALL_RACES:
        path = DATA_DIR / csv_name(race)
        entry = snapshot[race]
        try:
            st = path.stat()
            present, age, size = True, round(now - st.st_mtime, 1), st.st_size
        except OSError:
            present, age, size = False, None, 0
        if present:
            servable += 1
        last_success = entry["last_success"]
        races[race] = {
            "csv": csv_name(race),
            "refreshed_by_this_service": race in RACES,
            "csv_present": present,
            "csv_age_seconds": age,
            "csv_bytes": size,
            "last_success": last_success,
            "last_attempt": entry["last_attempt"],
            "last_error": entry["last_error"],
            "last_duration_seconds": entry["last_duration_seconds"],
            "consecutive_failures": entry["consecutive_failures"],
            "source": entry["source"],
        }

    tracked = [races[r] for r in RACES]
    all_present = all(r["csv_present"] for r in tracked)
    any_failing = any(r["consecutive_failures"] > 0 for r in tracked)
    # 200 as long as we can still serve every configured race, even if the
    # refreshes are failing - a stale map is a working map, and a health check
    # that fails on stale data would roll back a perfectly serviceable deploy.
    status = "ok" if all_present and not any_failing else ("degraded" if all_present else "unhealthy")
    return (200 if all_present else 503), {
        "status": status,
        "now": _now_iso(),
        "uptime_seconds": round(time.time() - BOOT_TIME, 1),
        "data_dir": str(DATA_DIR),
        "repo_dir": str(REPO_DIR),
        "default_race": DEFAULT_RACE,
        "refresh_enabled": REFRESH_ENABLED,
        "refresh_seconds": REFRESH_SECONDS,
        "fetch_timeout_seconds": FETCH_TIMEOUT,
        "data_year": DATA_YEAR or None,
        "races_refreshed": list(RACES),
        "races": races,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "electionmap"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    _index_html = None  # lazily-built, cached copy of map.html + injected config

    # -- helpers ---------------------------------------------------------
    def _send(self, code, body, content_type, extra_headers=None, head_only=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_file(self, path, content_type, extra_headers=None, head_only=False):
        """Serve a file from DATA_DIR or the repo, reading it from disk only
        when it has actually changed since the last request.

        The CSVs and their .meta.json sidecars only change once per refresh
        cycle (every REFRESH_SECONDS, default 900s), but map.html polls its
        active race's CSV every 60s per visitor and Railway's health check
        stats every configured CSV too - so on a quiet day the overwhelming
        majority of requests for a given path see the same bytes. Caching on
        mtime turns that from an open+read+close into a single stat() call.
        """
        try:
            st = path.stat()
        except FileNotFoundError:
            return self._not_found(head_only)
        except OSError as exc:
            log.error("stat failed path=%s err=%s", path, exc)
            return self._send(500, "500 internal error\n", "text/plain; charset=utf-8",
                              head_only=head_only)

        cached = _FILE_CACHE.get(path)
        if cached is not None and cached[0] == st.st_mtime_ns:
            body = cached[1]
        else:
            try:
                body = path.read_bytes()
            except OSError as exc:
                log.error("read failed path=%s err=%s", path, exc)
                return self._send(500, "500 internal error\n", "text/plain; charset=utf-8",
                                  head_only=head_only)
            _FILE_CACHE[path] = (st.st_mtime_ns, body)

        headers = {"Last-Modified": self.date_time_string(int(st.st_mtime))}
        headers.update(extra_headers or {})
        self._send(200, body, content_type, headers, head_only)

    def _send_index(self, head_only=False):
        """map.html, with the runtime config substituted into its placeholder.

        The page carries its own defaults, so the unsubstituted file is still a
        working page - which is what the tests and a plain static server get.

        The substitution result is cached on the class (DEFAULT_RACE is fixed
        for the life of the process and map.html is baked into the image), so
        a request serving the single busiest route on the site does not re-read
        a 69KB file from disk and re-run a string replace every single time.
        """
        html = Handler._index_html
        if html is None:
            try:
                raw = (REPO_DIR / "map.html").read_text(encoding="utf-8")
            except OSError as exc:
                log.error("read failed path=map.html err=%s", exc)
                return self._send(500, "500 internal error\n", "text/plain; charset=utf-8",
                                  head_only=head_only)
            config = json.dumps({"defaultRace": DEFAULT_RACE})
            html = raw.replace("<!--CONFIG-->", f"<script>window.__config={config};</script>", 1)
            Handler._index_html = html
        self._send(200, html, "text/html; charset=utf-8",
                   {"Cache-Control": "no-cache"}, head_only)

    def _not_found(self, head_only=False):
        self._send(404, "404 not found\n", "text/plain; charset=utf-8", head_only=head_only)

    # -- routing ---------------------------------------------------------
    def _route(self, head_only=False):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        name = path.lstrip("/")

        if path in ("/", "/index.html", "/map.html"):
            return self._send_index(head_only)

        if path == "/healthz":
            code, payload = health_payload()
            return self._send(code, json.dumps(payload, indent=2) + "\n",
                              "application/json; charset=utf-8",
                              {"Cache-Control": "no-store"}, head_only)

        district_match = DISTRICT_ROUTE.match(path)
        if district_match:
            code, body = _house_district_payload(district_match.group(1))
            return self._send(code, body, "application/json; charset=utf-8",
                              {"Cache-Control": "no-store"}, head_only)

        # /static/<file>: checked-in reference data (county FIPS, district
        # topology, historical baselines). One fixed directory level, same
        # character class as SAFE_NAME below it, so this can't walk anywhere
        # else.
        if SAFE_STATIC_PATH.match(name):
            filename = name.split("/", 1)[1]
            suffix = Path(filename).suffix.lower()
            candidate = STATIC_DATA_DIR / filename
            if suffix in STATIC_TYPES and candidate.is_file():
                # historical_<race>.json is rebuilt by
                # scripts/build_historical_baseline.py, rarely in production
                # but often during development - no-cache avoids a stale copy
                # surviving a hard refresh through an intermediate proxy.
                cache = "no-cache" if filename.startswith("historical_") else "public, max-age=300"
                return self._send_file(candidate, STATIC_TYPES[suffix],
                                       {"Cache-Control": cache}, head_only)
            return self._not_found(head_only)

        # Anything else must be a plain filename - no slashes, no "..", so
        # there is no way to walk out of the directories we intend to serve.
        if not SAFE_NAME.match(name):
            return self._not_found(head_only)

        # CSVs come from the volume; they change constantly, so no-cache and
        # an explicit text/csv (http.server would otherwise guess
        # application/octet-stream on some platforms).
        if name in CSV_NAMES:
            return self._send_file(DATA_DIR / name, "text/csv; charset=utf-8",
                                   {"Cache-Control": "no-cache"}, head_only)

        if name in META_NAMES:
            return self._send_file(DATA_DIR / name, "application/json; charset=utf-8",
                                   {"Cache-Control": "no-cache"}, head_only)

        # Everything else: static assets shipped in the repo next to map.html.
        suffix = Path(name).suffix.lower()
        if suffix in STATIC_TYPES:
            candidate = REPO_DIR / name
            if candidate.is_file():
                return self._send_file(candidate, STATIC_TYPES[suffix],
                                       {"Cache-Control": "public, max-age=300"}, head_only)
        return self._not_found(head_only)

    def do_GET(self):
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client hung up mid-response; nothing to do and nothing to log

    def do_HEAD(self):
        try:
            self._route(head_only=True)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        # One compact line per request, on stdout with everything else.
        log.info("http %s %s", self.address_string(), fmt % args)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info("boot repo_dir=%s data_dir=%s port=%s races=%s refresh=%ss default_race=%s",
             REPO_DIR, DATA_DIR, PORT, ",".join(RACES), REFRESH_SECONDS, DEFAULT_RACE)
    if not (REPO_DIR / "map.html").is_file():
        log.error("map.html not found in %s - '/' will 404", REPO_DIR)

    # Synchronous, so the very first request already has data to render.
    # Mock generation is local and fast; a real fetch would not be.
    seed_if_empty()

    scheduler = threading.Thread(target=scheduler_loop, name="refresher", daemon=True)
    scheduler.start()

    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True

    def shutdown(signum, _frame):
        # Railway sends SIGTERM before replacing a deploy; exit promptly
        # instead of being killed mid-request.
        log.info("received signal %s - shutting down", signum)
        STOP.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, shutdown)

    log.info("listening on 0.0.0.0:%s", PORT)
    try:
        httpd.serve_forever()
    finally:
        STOP.set()
        httpd.server_close()
        log.info("stopped")


if __name__ == "__main__":
    main()
