#!/usr/bin/env python3
"""
ercot_pipeline.py - data collection, sanity checks and a first look for the
Texas Grid project (forecast ERCOT prices -> schedule a battery -> measure revenue).

SETUP
    pip install pandas pyarrow requests gridstatusio duckdb
    export GRIDSTATUS_API_KEY=your_key     # free account at gridstatus.io

RECOMMENDED ORDER
    python ercot_pipeline.py check         # key, quota, dataset IDs, columns, Open-Meteo reachable?
    python ercot_pipeline.py plan          # estimated rows (free tier has a monthly row cap!)
    python ercot_pipeline.py probe         # proves the 'as of cutoff' forecast vintage works
    python ercot_pipeline.py pull --what prices
    python ercot_pipeline.py pull --what weather
    python ercot_pipeline.py pull --what forecasts
    python ercot_pipeline.py pull --what system
    python ercot_pipeline.py validate      # gaps, duplicates, DST days, leakage guard -> data/reports/
    python ercot_pipeline.py quicklook     # first EDA numbers (tails, negatives, spreads, pre/post RTC+B)
    python ercot_pipeline.py build-db      # DuckDB views over the Parquet files

FREE-PLAN LIMITS (read from your account at run time; shown by `check`)
    ~500k rows/month, only ~250 API requests/month, 50k rows per response, 1 request/second.
    So the script downloads in LARGE chunks (about 45k rows each, ~10-20 requests in total),
    sleeps between calls, and stops before it can exceed your remaining rows or requests.

DESIGN RULES (they exist to protect your results)
    * Raw files are never edited. One Parquet file per downloaded date range: data/raw/<dataset>/<start>_<end>.parquet
    * Pulls are incremental: covered date ranges are skipped, only the missing ranges are downloaded.
    * Row AND request budgets stop the run before you burn the free-tier monthly caps.
    * Forecast datasets are pulled "as of the cutoff" (D-1 CUTOFF_LOCAL, US Central) so you never
      train on information the market could not have had. `validate` re-checks this on the data.
    * Everything is stored in UTC. Local time is derived later, never stored as truth.

DATASET STATUS (as of 2026-11-05)
    Confirmed in Grid Status docs: ercot_spp_day_ahead_hourly, ercot_spp_real_time_15_min,
    ercot_load, ercot_load_forecast, ercot_fuel_mix, ercot_as_prices.
    Inferred from Grid Status table naming, so run `check` before trusting them:
    wind/solar *_actual_and_forecast_by_geo_region_hourly, ercot_load_by_weather_zone.
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger("ercot")

# ============================================================================
# 1. CONFIG  - edit this block, not the code below it
# ============================================================================
DATA_DIR = Path(os.environ.get("ERCOT_DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"
REPORT_DIR = DATA_DIR / "reports"

MARKET_TZ = ZoneInfo("America/Chicago")  # ERCOT's Central Prevailing Time
# Weather forecast archives (Open-Meteo previous runs) start around Jan 2024, so we start there.
# Prices go back further; change this if you want a longer price history.
DEFAULT_START = "2024-01-01"

# Hub names are ERCOT settlement points. `check` prints a sample so you can confirm the strings.
HUBS = ["HB_BUSAVG", "HB_HOUSTON", "HB_NORTH", "HB_PAN", "HB_SOUTH", "HB_WEST"]
PRIMARY_HUB = "HB_NORTH"  # gets full 15-minute real-time prices; change after your EDA

# Forecast cutoff: what is known when the day-ahead decision is made (D-1, local time).
# Check ERCOT's current day-ahead market deadline and leave a safety margin.
CUTOFF_LOCAL = "09:45:00"
CUTOFF_DAYS_BEFORE = 1

# ERCOT's real-time co-optimization (RTC+B) went live on this date: a structural break.
RTCB_GO_LIVE = pd.Timestamp("2025-12-05", tz=MARKET_TZ)

MAX_ROWS_DEFAULT = 400_000  # per run; free tier documentation says ~500k rows per month
CHUNK_FINAL_AFTER_DAYS = 3  # weather chunks ending earlier than this are cached forever
END_MARGIN_DAYS = 2         # default --end is today minus this, so late ERCOT postings land before we cache a day

# Free-plan defaults, overwritten by what your account reports (client.get_api_usage()).
DEFAULT_PAGE_SIZE = 50_000          # max rows per response
DEFAULT_ROWS_LEFT = 500_000
DEFAULT_REQUESTS_LEFT = 250
ROWS_PER_REQUEST_TARGET = 45_000    # chunk size target: stay under the per-response cap
ROW_SAFETY = 5_000
REQUEST_SAFETY = 15
SLEEP_BETWEEN_CALLS = 1.3           # plan allows 1 request per second


# ---- Grid Status datasets ---------------------------------------------------
@dataclass(frozen=True)
class DatasetSpec:
    key: str                       # folder name under data/raw
    dataset_id: str                # Grid Status dataset id
    group: str                     # prices | system | forecasts | context
    freq: str                      # expected spacing for gap checks ("1h", "15min")
    est_rows_per_day: int          # rough, for the budget (plan prints totals)
    filter_column: str | None = None
    filter_values: tuple = ()
    resample: str | None = None    # server-side aggregation (cuts rows)
    publish_time: str | None = None
    vintage: bool = False          # True -> pulled as-of cutoff, leakage guard applies
    verified: bool = True
    note: str = ""


ASOF_CUTOFF = f"latest_before:-{CUTOFF_DAYS_BEFORE} day@{CUTOFF_LOCAL}"

SPECS: list[DatasetSpec] = [
    # --- prices -------------------------------------------------------------
    DatasetSpec("dam_spp_hubs", "ercot_spp_day_ahead_hourly", "prices", "1h", 24 * len(HUBS),
                "location", tuple(HUBS), note="Primary forecast target (hourly day-ahead settlement point prices)."),
    DatasetSpec("rt_spp_15min_primary", "ercot_spp_real_time_15_min", "prices", "15min", 96,
                "location", (PRIMARY_HUB,), note="Real-time 15-minute prices at the primary hub only (row budget)."),
    # --- system state (actuals, no vintage issue) -----------------------------
    DatasetSpec("load_system_hourly", "ercot_load", "system", "1h", 24, resample="1 hour",
                note="Actual system load, resampled hourly on the server."),
    DatasetSpec("load_weather_zones", "ercot_load_by_weather_zone", "system", "1h", 24,
                verified=False, note="Actual load by weather zone. ID inferred; confirm with `check`."),
    DatasetSpec("fuel_mix_hourly", "ercot_fuel_mix", "system", "1h", 24, resample="1 hour",
                note="Generation by fuel, hourly mean."),
    # --- forecasts as published before the cutoff (no leakage) -----------------
    DatasetSpec("load_forecast_asof", "ercot_load_forecast_by_weather_zone", "forecasts", "1h", 24,
                publish_time=ASOF_CUTOFF, vintage=True, verified=True, note="Load forecast as of the D-1 cutoff."),
    DatasetSpec("wind_forecast_asof", "ercot_wind_actual_and_forecast_by_geo_region_hourly", "forecasts", "1h", 24,
                publish_time=ASOF_CUTOFF, vintage=True, verified=False, note="Wind forecast as of cutoff."),
    DatasetSpec("solar_forecast_asof", "ercot_solar_actual_and_forecast_by_geo_region_hourly", "forecasts", "1h", 24,
                publish_time=ASOF_CUTOFF, vintage=True, verified=False, note="Solar forecast as of cutoff."),
    # --- actuals that live inside the wind/solar reports -------------------------
    DatasetSpec("wind_actual_latest", "ercot_wind_actual_and_forecast_by_geo_region_hourly", "forecasts", "1h", 24,
                publish_time="latest", verified=False,
                note="Latest report per hour = actual wind generation (use for evaluation, never as a feature)."),
    DatasetSpec("solar_actual_latest", "ercot_solar_actual_and_forecast_by_geo_region_hourly", "forecasts", "1h", 24,
                publish_time="latest", verified=False,
                note="Latest report per hour = actual solar generation (evaluation only)."),
    # --- context (off by default) ---------------------------------------------
    DatasetSpec("as_prices", "ercot_as_prices", "context", "1h", 24 * 6, verified=True,
                note="Ancillary service prices. Out of project scope; context for the limitations slide."),
]
SPEC_BY_KEY = {s.key: s for s in SPECS}


# ---- Weather (Open-Meteo, free, no key) ------------------------------------
@dataclass(frozen=True)
class Zone:
    name: str
    city: str
    lat: float
    lon: float


# One representative point per ERCOT weather zone. Your choice - document it in the report.
ZONES = [
    Zone("COAST", "Houston", 29.7604, -95.3698),
    Zone("EAST", "Tyler", 32.3513, -95.3011),
    Zone("FAR_WEST", "Midland", 31.9973, -102.0779),
    Zone("NORTH", "Wichita Falls", 33.9137, -98.4934),
    Zone("NORTH_CENTRAL", "Dallas", 32.7767, -96.7970),
    Zone("SOUTH_CENTRAL", "San Antonio", 29.4241, -98.4936),
    Zone("SOUTHERN", "Corpus Christi", 27.8006, -97.3964),
    Zone("WEST", "Abilene", 32.4487, -99.7331),
]
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
OBS_VARS = ["temperature_2m", "apparent_temperature", "relative_humidity_2m", "dew_point_2m",
            "wind_speed_100m", "wind_direction_100m", "shortwave_radiation", "cloud_cover", "precipitation"]
FCST_VARS = ["temperature_2m", "apparent_temperature", "relative_humidity_2m",
             "wind_speed_100m", "shortwave_radiation", "cloud_cover"]
# previous_day2 = forecast issued ~48h before valid time -> always before the D-1 morning cutoff (safe).
# previous_day1 = issued ~24h before -> for afternoon/evening hours it is AFTER the cutoff (leaks).
FCST_LEADS = [2, 1]
WEATHER_FORECAST_MODEL: str | None = None  # None = Open-Meteo best match; "ecmwf_ifs025" = consistent model
OBS_LAG_DAYS = 6  # ERA5 on Open-Meteo lags about 5 days


# ============================================================================
# 2. SMALL UTILITIES
# ============================================================================
def parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def default_end() -> str:
    return (date.today() - timedelta(days=END_MARGIN_DAYS)).isoformat()  # exclusive


def month_chunks(start: date, end: date):
    """Yield (chunk_start, chunk_end_exclusive) per calendar month inside [start, end)."""
    cur = start
    while cur < end:
        nxt = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        yield cur, min(nxt, end)
        cur = nxt


def quarter_chunks(start: date, end: date):
    """Yield (chunk_start, chunk_end_exclusive) per calendar quarter inside [start, end)."""
    cur = start
    while cur < end:
        q_end_month = ((cur.month - 1) // 3 + 1) * 3
        nxt = date(cur.year + (q_end_month == 12), (q_end_month % 12) + 1, 1)
        yield cur, min(nxt, end)
        cur = nxt


def is_final(chunk_end: date) -> bool:
    return chunk_end <= date.today() - timedelta(days=CHUNK_FINAL_AFTER_DAYS)


def write_table(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False)
        return path
    except ImportError:
        alt = path.with_suffix(".csv.gz")
        df.to_csv(alt, index=False)
        return alt


def read_table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def chunk_path(folder: Path, name: str) -> Path | None:
    for ext in (".parquet", ".csv.gz"):
        p = folder / f"{name}{ext}"
        if p.exists():
            return p
    return None


def to_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


class RowBudget:
    """Tracks rows and requests spent in this run against what the account still allows."""

    def __init__(self, max_rows: int, max_requests: int = 10**9):
        self.max_rows, self.max_requests = max_rows, max_requests
        self.used_rows = 0
        self.used_requests = 0

    @property
    def used(self) -> int:  # rows (kept for log lines)
        return self.used_rows

    def can_afford(self, rows: int, requests: int) -> bool:
        return self.used_rows + rows <= self.max_rows and self.used_requests + requests <= self.max_requests


# ---- date-range bookkeeping (what is already on disk) ----
def parse_range_name(path: Path) -> tuple[date, date]:
    a, b = path.name.split(".")[0].split("_")
    return date.fromisoformat(a), date.fromisoformat(b)


def covered_ranges(folder: Path) -> list[tuple[date, date]]:
    rngs = []
    for f in folder.glob("*"):
        try:
            rngs.append(parse_range_name(f))
        except Exception:
            continue
    rngs.sort()
    merged: list[tuple[date, date]] = []
    for a, b in rngs:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def missing_ranges(start: date, end: date, covered: list[tuple[date, date]]) -> list[tuple[date, date]]:
    gaps, cur = [], start
    for a, b in covered:
        if b <= cur:
            continue
        if a >= end:
            break
        if a > cur:
            gaps.append((cur, min(a, end)))
        cur = max(cur, b)
        if cur >= end:
            break
    if cur < end:
        gaps.append((cur, end))
    return gaps


def pieces(a: date, b: date, days: int):
    cur = a
    while cur < b:
        nxt = min(cur + timedelta(days=days), b)
        yield cur, nxt
        cur = nxt


def chunk_days(spec: "DatasetSpec", page_size: int) -> int:
    days = max(1, int(min(page_size, ROWS_PER_REQUEST_TARGET) // max(1, spec.est_rows_per_day)))
    if spec.resample:
        days = min(days, 365)
    return days


def requests_for(est_rows: int, page_size: int) -> int:
    return max(1, -(-est_rows // page_size))  # ceil division


# ============================================================================
# 3. GRID STATUS ACCESS
# ============================================================================
def get_client():
    if not os.environ.get("GRIDSTATUS_API_KEY"):
        sys.exit("Set GRIDSTATUS_API_KEY first (free key from gridstatus.io).")
    try:
        from gridstatusio import GridStatusClient
    except ImportError:
        sys.exit("pip install gridstatusio")
    return GridStatusClient()  # the client reads GRIDSTATUS_API_KEY itself


def read_limits(client) -> dict:
    """Remaining rows / requests and the per-response row cap, from your own account."""
    out = dict(rows_left=DEFAULT_ROWS_LEFT, requests_left=DEFAULT_REQUESTS_LEFT, page_size=DEFAULT_PAGE_SIZE, raw=None)
    try:
        usage = client.get_api_usage()
        lim, cur = usage.get("limits", {}) or {}, usage.get("current_period_usage", {}) or {}
        out["raw"] = usage
        if lim.get("api_rows_per_response_limit"):
            out["page_size"] = int(lim["api_rows_per_response_limit"])
        out["rows_left"] = (int(lim["api_rows_returned_limit"]) - int(cur.get("total_api_rows_returned", 0))
                            if lim.get("api_rows_returned_limit") else 10**12)
        out["requests_left"] = (int(lim["api_requests_limit"]) - int(cur.get("total_requests", 0))
                                if lim.get("api_requests_limit") else 10**9)
    except Exception as exc:
        log.warning("could not read plan limits (%s); assuming free-plan defaults", exc)
    return out


def fetch_chunk(client, spec: DatasetSpec, cs: date, ce: date, page_size: int = DEFAULT_PAGE_SIZE,
                retries: int = 3) -> pd.DataFrame:
    kwargs: dict = dict(dataset=spec.dataset_id, start=cs.isoformat(), end=ce.isoformat(), verbose=False,
                        page_size=page_size)
    if spec.filter_column:
        vals = list(spec.filter_values)
        if len(vals) == 1:
            kwargs.update(filter_column=spec.filter_column, filter_value=vals[0])
        else:
            kwargs.update(filter_column=spec.filter_column, filter_value=vals, filter_operator="in")
    if spec.resample:
        kwargs.update(resample=spec.resample, resample_function="mean")
    if spec.publish_time:
        kwargs["publish_time"] = spec.publish_time
        kwargs["timezone"] = "market"
    for attempt in range(1, retries + 1):
        try:
            return client.get_dataset(**kwargs)
        except Exception as exc:  # network, rate limit, plan limit...
            log.warning("%s %s..%s attempt %d/%d failed: %s", spec.key, cs, ce, attempt, retries, exc)
            if attempt == retries:
                raise
            time.sleep(10 * attempt)
    raise RuntimeError("unreachable")


def pull_specs(client, specs, start: date, end: date, budget: RowBudget, page_size: int = DEFAULT_PAGE_SIZE,
               force: bool = False) -> dict:
    summary: dict = {}
    for spec in specs:
        folder = RAW_DIR / spec.key
        todo = [(start, end)] if force else missing_ranges(start, end, covered_ranges(folder))
        days = chunk_days(spec, page_size)
        pulled = deferred = rows = 0
        for a, b in todo:
            for cs, ce in pieces(a, b, days):
                est = spec.est_rows_per_day * (ce - cs).days
                reqs = requests_for(est, page_size)
                if not budget.can_afford(est, reqs):
                    deferred += 1
                    continue
                time.sleep(SLEEP_BETWEEN_CALLS)
                df = fetch_chunk(client, spec, cs, ce, page_size)
                budget.used_requests += max(1, requests_for(len(df) if df is not None else 0, page_size))
                if df is None or len(df) == 0:
                    log.warning("%s %s..%s: 0 rows returned (not saved)", spec.key, cs, ce)
                    continue
                df = df.copy()
                df["_pulled_at_utc"] = datetime.now(timezone.utc).isoformat()
                write_table(df, folder / f"{cs.isoformat()}_{ce.isoformat()}.parquet")
                budget.used_rows += len(df)
                rows += len(df)
                pulled += 1
                log.info("%-22s %s -> %s  %7d rows | run total: %d rows, %d requests",
                         spec.key, cs, ce, len(df), budget.used_rows, budget.used_requests)
        have = covered_ranges(folder)
        summary[spec.key] = dict(downloaded_chunks=pulled, deferred_chunks=deferred, rows=rows,
                                 covered=[f"{a}..{b}" for a, b in have][:3])
    return summary


# ============================================================================
# 4. WEATHER ACCESS
# ============================================================================
def http_get_json(url: str, params: dict, retries: int = 4) -> dict:
    import requests

    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            if r.status_code == 400:
                raise RuntimeError(f"Open-Meteo rejected the request: {r.text[:300]}")
            r.raise_for_status()
            if not r.text.strip():
                raise RuntimeError("Empty response body from Open-Meteo")
            return r.json()
        except Exception as exc:
            if attempt == retries:
                raise
            wait = 5 * attempt
            log.warning("weather request failed (%s), retrying in %ds", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _weather_frame(payload: dict, zone: Zone, kind: str) -> pd.DataFrame:
    hourly = payload["hourly"]
    df = pd.DataFrame(hourly)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df.insert(1, "zone", zone.name)
    df["_kind"] = kind
    return df


def pull_weather(start: date, end: date, force: bool = False, sleep: float = 0.4) -> dict:
    out = {"obs": 0, "fcst": 0, "cached": 0}
    base = dict(hourly=None, timezone="UTC", wind_speed_unit="ms")
    obs_end = min(end, date.today() - timedelta(days=OBS_LAG_DAYS))
    for zone in ZONES:
        for cs, ce in quarter_chunks(start, end):
            # ---- observations (ERA5 reanalysis): for EDA and for evaluating what actually happened
            if cs < obs_end:
                folder = RAW_DIR / "weather_obs" / zone.name
                name = f"{cs:%Y}-Q{(cs.month - 1) // 3 + 1}"
                if chunk_path(folder, name) and is_final(ce) and not force:
                    out["cached"] += 1
                else:
                    p = dict(base, latitude=zone.lat, longitude=zone.lon, start_date=cs.isoformat(),
                             end_date=(min(ce, obs_end) - timedelta(days=1)).isoformat(),
                             hourly=",".join(OBS_VARS), models="era5")
                    df = _weather_frame(http_get_json(ARCHIVE_URL, p), zone, "era5_obs")
                    write_table(df, folder / f"{name}.parquet")
                    out["obs"] += len(df)
                    time.sleep(sleep)
            # ---- forecasts issued ~24h / ~48h before valid time (see FCST_LEADS comment)
            folder = RAW_DIR / "weather_fcst" / zone.name
            name = f"{cs:%Y}-Q{(cs.month - 1) // 3 + 1}"
            if chunk_path(folder, name) and is_final(ce) and not force:
                out["cached"] += 1
                continue
            variables = [f"{v}_previous_day{d}" for v in FCST_VARS for d in FCST_LEADS]
            p = dict(base, latitude=zone.lat, longitude=zone.lon, start_date=cs.isoformat(),
                     end_date=(ce - timedelta(days=1)).isoformat(), hourly=",".join(variables))
            if WEATHER_FORECAST_MODEL:
                p["models"] = WEATHER_FORECAST_MODEL
            df = _weather_frame(http_get_json(PREVIOUS_RUNS_URL, p), zone, "forecast_lead")
            write_table(df, folder / f"{name}.parquet")
            out["fcst"] += len(df)
            log.info("weather %-14s %s  obs/fcst rows so far %d/%d", zone.name, name, out["obs"], out["fcst"])
            time.sleep(sleep)
    return out


# ============================================================================
# 5. LOADING + CHECKS
# ============================================================================
def load_dataset(key: str, dedupe: bool = True) -> pd.DataFrame:
    """Concatenate all monthly files of one dataset. All *_utc columns become tz-aware UTC."""
    folder = RAW_DIR / key
    files = sorted(list(folder.glob("*.parquet")) + list(folder.glob("*.csv.gz")))
    if not files:
        raise FileNotFoundError(f"No files for {key} in {folder}")
    df = pd.concat([read_table(f) for f in files], ignore_index=True)
    for c in df.columns:
        if c.endswith("_utc") and not c.startswith("_"):
            df[c] = to_utc(df[c])
    if dedupe:
        keys = [c for c in ("interval_start_utc", "location", "publish_time_utc") if c in df.columns]
        if keys:
            df = df.sort_values(keys + ["_pulled_at_utc"]).drop_duplicates(keys, keep="last")
    return df.reset_index(drop=True)


def dst_days(first: pd.Timestamp, last: pd.Timestamp) -> dict:
    """Local days whose length is not 24h in the market time zone: {date: hours}."""
    out = {}
    for d in pd.date_range(first.tz_convert(MARKET_TZ).date(), last.tz_convert(MARKET_TZ).date(), freq="D"):
        s = pd.Timestamp(d.date().isoformat(), tz=MARKET_TZ)
        e = pd.Timestamp((d + pd.Timedelta(days=1)).date().isoformat(), tz=MARKET_TZ)
        hours = (e.tz_convert("UTC") - s.tz_convert("UTC")).total_seconds() / 3600
        if hours != 24:
            out[d.date().isoformat()] = int(hours)
    return out


def leakage_summary(df: pd.DataFrame, time_col: str, pub_col: str,
                    cutoff_local: str = CUTOFF_LOCAL, days_before: int = CUTOFF_DAYS_BEFORE) -> dict:
    """For each row: was it published before the D-N cutoff of the day it forecasts?"""
    t = to_utc(df[time_col])
    p = to_utc(df[pub_col])
    hh, mm, ss = (int(x) for x in cutoff_local.split(":"))
    local_day = t.dt.tz_convert(MARKET_TZ).dt.tz_localize(None).dt.normalize()
    cutoff_naive = local_day - pd.Timedelta(days=days_before) + pd.Timedelta(hours=hh, minutes=mm, seconds=ss)
    cutoff = cutoff_naive.dt.tz_localize(MARKET_TZ, ambiguous=True, nonexistent="shift_forward").dt.tz_convert("UTC")
    late = p > cutoff
    lead_hours = (cutoff - p).dt.total_seconds() / 3600
    return dict(
        rows=int(len(df)),
        rows_published_after_cutoff=int(late.sum()),
        share_after_cutoff=round(float(late.mean()), 6) if len(df) else 0.0,
        max_hours_late=round(float((-lead_hours[late]).max()), 2) if late.any() else 0.0,
        median_hours_before_cutoff=round(float(lead_hours.median()), 2) if len(df) else None,
        min_hours_before_cutoff=round(float(lead_hours.min()), 2) if len(df) else None,
    )


def gap_report(df: pd.DataFrame, freq: str, time_col: str = "interval_start_utc") -> tuple[pd.DataFrame, dict]:
    """Missing intervals per series (per location when present). Returns (missing rows, summary)."""
    groups = [("all", df)] if "location" not in df.columns else list(df.groupby("location"))
    rows, summary = [], {}
    for name, g in groups:
        times = pd.DatetimeIndex(to_utc(g[time_col]).dropna().unique()).sort_values()
        if len(times) == 0:
            continue
        expected = pd.date_range(times.min(), times.max(), freq=freq)
        missing = expected.difference(times)
        summary[str(name)] = dict(expected=int(len(expected)), present=int(len(times)), missing=int(len(missing)))
        rows += [dict(series=str(name), missing_start_utc=m.isoformat()) for m in missing[:5000]]
    return pd.DataFrame(rows), summary


# ============================================================================
# 6. COMMANDS
# ============================================================================
def cmd_check(args) -> None:
    client = get_client()
    print("== Grid Status plan / usage ==")
    limits = read_limits(client)
    print(json.dumps(limits["raw"], indent=2, default=str) if limits["raw"] else "  (could not read usage)")
    print(f"  -> rows left this period: {limits['rows_left']:,} | requests left: {limits['requests_left']:,} "
          f"| rows per response: {limits['page_size']:,}")
    print("\n== Configured datasets (1 metadata call each, spaced out for the rate limit) ==")
    known_ids = None
    seen: dict = {}
    for spec in SPECS:
        time.sleep(SLEEP_BETWEEN_CALLS)
        try:
            meta = seen.get(spec.dataset_id) or client.get_dataset_metadata(spec.dataset_id)
            seen[spec.dataset_id] = meta
        except Exception as exc:
            print(f"  [MISSING] {spec.key:<22} {spec.dataset_id}  ({str(exc)[:80]})")
            if known_ids is None:
                try:
                    known_ids = [m["id"] for m in client.list_datasets(filter_term="ercot", return_list=True)]
                except Exception:
                    known_ids = []
            close = difflib.get_close_matches(spec.dataset_id, known_ids, n=3, cutoff=0.5)
            if close:
                print("            closest real IDs:", ", ".join(close))
            continue
        cols = [c["name"] for c in meta.get("all_columns", [])]
        flag = "ok" if spec.verified else "ok (was unverified)"
        print(f"  [{flag}] {spec.key:<22} {spec.dataset_id}")
        print(f"            data {meta.get('earliest_available_time_utc')} -> {meta.get('latest_available_time_utc')}"
              f" | ~{meta.get('number_of_rows_approximate')} rows | time col: {meta.get('time_index_column')}"
              f" | publish col: {meta.get('publish_time_column')}")
        if spec.filter_column and spec.filter_column not in cols:
            print(f"            WARNING: filter column '{spec.filter_column}' not in columns")
        if spec.vintage and not meta.get("publish_time_column"):
            print("            WARNING: dataset has no publish-time column, as-of pulls will not work")
        if args.columns:
            print("            columns:", ", ".join(cols))
    print("\n== Hub names (tiny query: max 150 rows) ==")
    try:
        time.sleep(SLEEP_BETWEEN_CALLS)
        d0 = date.today() - timedelta(days=4)
        sample = client.get_dataset("ercot_spp_day_ahead_hourly", start=d0.isoformat(),
                                    end=(d0 + timedelta(days=1)).isoformat(), filter_column="location",
                                    filter_value=HUBS, filter_operator="in", limit=150, verbose=False)
        found = sorted(set(sample["location"]))
        print("  hubs found:", found, "| missing:", [h for h in HUBS if h not in found])
    except Exception as exc:
        print("  sample failed:", exc)
    print("\n== Open-Meteo ==")
    try:
        z = ZONES[0]
        p = dict(latitude=z.lat, longitude=z.lon, start_date=(date.today() - timedelta(days=20)).isoformat(),
                 end_date=(date.today() - timedelta(days=19)).isoformat(), hourly="temperature_2m", timezone="UTC")
        n = len(http_get_json(ARCHIVE_URL, p)["hourly"]["time"])
        print(f"  archive API reachable ({n} hourly rows)")
        p2 = dict(p, hourly="temperature_2m_previous_day2")
        n2 = len(http_get_json(PREVIOUS_RUNS_URL, p2)["hourly"]["time"])
        print(f"  previous-runs API reachable ({n2} hourly rows)")
    except Exception as exc:
        print("  FAILED:", exc)


def estimate_rows(specs, start: date, end: date) -> dict:
    days = (end - start).days
    return {s.key: s.est_rows_per_day * days for s in specs}


def cmd_plan(args) -> None:
    start, end = parse_day(args.start), parse_day(args.end)
    page, rows_left, reqs_left = DEFAULT_PAGE_SIZE, DEFAULT_ROWS_LEFT, DEFAULT_REQUESTS_LEFT
    if os.environ.get("GRIDSTATUS_API_KEY"):
        lim = read_limits(get_client())
        page, rows_left, reqs_left = lim["page_size"], lim["rows_left"], lim["requests_left"]
    print(f"Window {start} -> {end} ({(end - start).days} days). Rough estimates:")
    tot_rows = tot_reqs = 0
    for s in SPECS:
        days = (end - start).days
        rows = s.est_rows_per_day * days
        reqs = sum(requests_for(s.est_rows_per_day * (b - a).days, page) for a, b in pieces(start, end, chunk_days(s, page)))
        tag = "(context, off by default)" if s.group == "context" else ""
        print(f"  {s.key:<24} {rows:>10,} rows {reqs:>3} requests  {s.group:<9} {tag}")
        if s.group != "context":
            tot_rows += rows
            tot_reqs += reqs
    print(f"  {'TOTAL (excl. context)':<24} {tot_rows:>10,} rows {tot_reqs:>3} requests")
    print(f"\nYour account (or free-plan defaults): {rows_left:,} rows and {reqs_left:,} requests left, "
          f"{page:,} rows per response.")
    if tot_rows > rows_left - ROW_SAFETY:
        print("Rows exceed what is left: pull `prices` first, the rest after your usage period resets (cache keeps progress).")
    wsteps = len(ZONES) * len(list(quarter_chunks(start, end))) * 2
    print(f"Weather: about {wsteps} Open-Meteo requests (separate free service, no key).")


def cmd_probe(args) -> None:
    """Fetch 2 days of the load forecast as-of the cutoff and test the leakage rule."""
    client = get_client()
    spec = SPEC_BY_KEY["load_forecast_asof"]
    s = date.today() - timedelta(days=10)
    df = client.get_dataset(spec.dataset_id, start=s.isoformat(), end=(s + timedelta(days=2)).isoformat(),
                            publish_time=spec.publish_time, timezone="market", verbose=False, limit=200)
    if df is None or len(df) == 0:
        sys.exit("Probe returned no rows.")
    pub = find_col(df, ["publish_time_utc", "publish_time"])
    tcol = find_col(df, ["interval_start_utc", "interval_start"])
    print(df[[c for c in (tcol, pub) if c]].head(12).to_string(index=False))
    if not (pub and tcol):
        sys.exit("Could not find the time / publish columns. Run `check --columns`.")
    res = leakage_summary(df, tcol, pub)
    print("\n", json.dumps(res, indent=2))
    if res["rows_published_after_cutoff"] > 0:
        print(f"\nLEAK: some forecasts were published AFTER your cutoff ({CUTOFF_LOCAL} US Central, D-1).")
        print("Edit CUTOFF_LOCAL / ASOF_CUTOFF (the API may read the clock in UTC) before pulling anything.")
    else:
        print("\nOK: no row was published after the cutoff.")
        print(f"Median lead before cutoff: {res['median_hours_before_cutoff']}h. If that is ~6h the API probably "
              "reads the clock in UTC (safe but conservative).")


def cmd_pull(args) -> None:
    start, end = parse_day(args.start), parse_day(args.end)
    what = args.what
    summary: dict = {}
    if what in ("prices", "system", "forecasts", "context", "all"):
        client = get_client()
        lim = read_limits(client)
        max_rows = max(0, min(args.max_rows, lim["rows_left"] - ROW_SAFETY))
        max_reqs = max(0, lim["requests_left"] - REQUEST_SAFETY)
        log.info("Window %s -> %s | what=%s | this run may use up to %s rows and %s requests "
                 "(account left: %s rows, %s requests; page size %s)", start, end, what, f"{max_rows:,}",
                 f"{max_reqs:,}", f"{lim['rows_left']:,}", f"{lim['requests_left']:,}", f"{lim['page_size']:,}")
        groups = {"all": ("prices", "system", "forecasts")}.get(what, (what,))
        specs = [s for s in SPECS if s.group in groups]
        summary = pull_specs(client, specs, start, end, RowBudget(max_rows, max_reqs), lim["page_size"],
                             force=args.force)
    if what in ("weather", "all"):
        summary["weather"] = pull_weather(start, end, force=args.force)
    print("\n== Summary ==")
    for k, v in summary.items():
        print(f"  {k:<24} {v}")
    if any(isinstance(v, dict) and v.get("deferred_chunks") for v in summary.values()):
        print("\nSome date ranges were deferred to protect your row/request allowance. Re-run the same "
              "command after your usage period resets (or with a higher --max-rows); covered ranges are skipped.")


def cmd_validate(args) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    manifest, seed = {}, []
    for spec in SPECS:
        if not (RAW_DIR / spec.key).exists():
            continue
        df = load_dataset(spec.key, dedupe=False)
        tcol = find_col(df, ["interval_start_utc", "interval_start"]) or df.columns[0]
        entry: dict = dict(dataset_id=spec.dataset_id, rows=int(len(df)))
        keys = [c for c in (tcol, "location" if "location" in df.columns else None,
                            find_col(df, ["publish_time_utc", "publish_time"])) if c]
        dups = int(df.duplicated(keys).sum())
        entry["duplicate_rows_on_keys"] = dups
        nulls = (df.isna().mean() * 100).round(2)
        entry["null_pct_top5"] = nulls[nulls > 0].sort_values(ascending=False).head(5).to_dict()
        t = to_utc(df[tcol])
        entry["first_interval_utc"], entry["last_interval_utc"] = str(t.min()), str(t.max())
        if "location" in df.columns:
            entry["locations"] = sorted(df["location"].unique().tolist())[:20]
        dedup = df.drop_duplicates(keys)
        gaps, gsum = gap_report(dedup, spec.freq, tcol)
        entry["gaps"] = gsum
        if len(gaps):
            gaps.to_csv(REPORT_DIR / f"gaps_{spec.key}.csv", index=False)
        if spec.freq == "1h" and t.notna().any():
            days = dst_days(t.min(), t.max())
            check = {}
            for d, hours in days.items():
                lo = pd.Timestamp(d, tz=MARKET_TZ).tz_convert("UTC")
                hi = lo + pd.Timedelta(hours=hours)
                sub = dedup[(to_utc(dedup[tcol]) >= lo) & (to_utc(dedup[tcol]) < hi)]
                per = sub.groupby("location").size() if "location" in sub.columns else pd.Series([len(sub)])
                check[d] = dict(expected_hours=hours, min_rows_per_series=int(per.min()) if len(per) else 0)
            entry["dst_days"] = check
        if spec.vintage:
            pub = find_col(df, ["publish_time_utc", "publish_time"])
            if pub:
                entry["leakage_guard"] = leakage_summary(df, tcol, pub)
                if entry["leakage_guard"]["rows_published_after_cutoff"] > 0:
                    print(f"  LEAK WARNING in {spec.key}: {entry['leakage_guard']}")
                    if args.strict:
                        sys.exit(2)
            else:
                entry["leakage_guard"] = "no publish-time column found"
        manifest[spec.key] = entry
        seed += [
            dict(step="raw_rows", dataset=spec.key, rows_in=len(df), rows_out=len(df), rule="as downloaded"),
            dict(step="drop_exact_duplicates", dataset=spec.key, rows_in=len(df), rows_out=len(dedup),
                 rule=f"keys={keys}"),
            dict(step="flag_missing_intervals", dataset=spec.key, rows_in=len(dedup), rows_out=len(dedup),
                 rule=f"gaps listed in gaps_{spec.key}.csv; never interpolate prices"),
        ]
    (REPORT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    pd.DataFrame(seed).to_csv(REPORT_DIR / "cleaning_log_seed.csv", index=False)
    print(f"Wrote {REPORT_DIR/'manifest.json'} and cleaning_log_seed.csv")
    for k, v in manifest.items():
        missing = sum(x["missing"] for x in v["gaps"].values()) if v.get("gaps") else 0
        print(f"  {k:<24} rows={v['rows']:>9,} dups={v['duplicate_rows_on_keys']:>6} missing_intervals={missing:>6}")


def cmd_quicklook(args) -> None:
    df = load_dataset("dam_spp_hubs")
    df["local_date"] = df["interval_start_utc"].dt.tz_convert(MARKET_TZ).dt.date
    g = df.groupby("location")["spp"]
    stats = pd.DataFrame({
        "n": g.size(), "mean": g.mean(), "std": g.std(), "min": g.min(), "p01": g.quantile(0.01),
        "p99": g.quantile(0.99), "max": g.max(), "pct_negative": g.apply(lambda s: 100 * (s < 0).mean()),
    }).round(2)
    print("== Day-ahead price distribution by hub ($/MWh) ==\n", stats.to_string())
    daily = df.groupby(["location", "local_date"])["spp"].agg(lambda s: s.max() - s.min()).rename("spread")
    d = daily.reset_index()
    print("\n== Daily max-min spread ($/MWh): the raw arbitrage opportunity ==")
    print(d.groupby("location")["spread"].describe(percentiles=[0.5, 0.9, 0.99]).round(1).to_string())
    d["regime"] = pd.to_datetime(d["local_date"]).dt.tz_localize(MARKET_TZ).map(
        lambda x: "post RTC+B" if x >= RTCB_GO_LIVE else "pre RTC+B")
    print("\n== Mean daily spread before vs after RTC+B (2025-12-05) ==")
    print(d.pivot_table(index="location", columns="regime", values="spread", aggfunc="mean").round(1).to_string())
    top = d.sort_values("spread", ascending=False).head(10)
    print("\n== 10 biggest-spread days (all hubs) ==\n", top.to_string(index=False))


def cmd_build_db(args) -> None:
    try:
        import duckdb
    except ImportError:
        sys.exit("pip install duckdb")
    path = DATA_DIR / "ercot.duckdb"
    con = duckdb.connect(str(path))
    n = 0
    for folder in sorted(p for p in RAW_DIR.rglob("*") if p.is_dir() and any(p.glob("*.parquet"))):
        name = "_".join(folder.relative_to(RAW_DIR).parts).lower()
        glob = str(folder / "*.parquet").replace("\\", "/")
        con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{glob}', union_by_name=true)")
        n += 1
    con.close()
    print(f"Created {n} views in {path}. Example: SELECT * FROM dam_spp_hubs LIMIT 5;")


# ============================================================================
# 7. CLI
# ============================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ERCOT data pipeline for the Texas Grid project")
    sub = p.add_subparsers(dest="cmd", required=True)

    def window(sp):
        sp.add_argument("--start", default=DEFAULT_START, help="YYYY-MM-DD (inclusive)")
        sp.add_argument("--end", default=default_end(), help="YYYY-MM-DD (exclusive; default today minus 2 days)")

    c = sub.add_parser("check", help="verify key, quota, dataset IDs, columns, weather API")
    c.add_argument("--columns", action="store_true", help="print every column of every dataset")
    c.set_defaults(fn=cmd_check)
    c = sub.add_parser("plan", help="estimate rows before downloading")
    window(c)
    c.add_argument("--max-rows", type=int, default=MAX_ROWS_DEFAULT)
    c.set_defaults(fn=cmd_plan)
    c = sub.add_parser("probe", help="test the as-of-cutoff forecast vintage on a tiny sample")
    c.set_defaults(fn=cmd_probe)
    c = sub.add_parser("pull", help="download data into data/raw (resumable)")
    window(c)
    c.add_argument("--what", choices=["prices", "system", "forecasts", "context", "weather", "all"], default="prices")
    c.add_argument("--max-rows", type=int, default=MAX_ROWS_DEFAULT)
    c.add_argument("--force", action="store_true", help="re-download even finished months")
    c.set_defaults(fn=cmd_pull)
    c = sub.add_parser("validate", help="gaps, duplicates, DST days, leakage guard")
    c.add_argument("--strict", action="store_true", help="exit with an error if any forecast leaks")
    c.set_defaults(fn=cmd_validate)
    c = sub.add_parser("quicklook", help="first EDA numbers from the day-ahead prices")
    c.set_defaults(fn=cmd_quicklook)
    c = sub.add_parser("build-db", help="DuckDB views over the Parquet files")
    c.set_defaults(fn=cmd_build_db)
    return p


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
