# `ercot_pipeline.py`: Step 1, getting trustworthy data

**Purpose:** download every input the project needs (prices, ERCOT's own load/wind/solar forecasts,
actual system data, weather) and prove that the data is complete and leak-free *before* any modelling.

**Reads:** the Grid Status API (needs `GRIDSTATUS_API_KEY`) and the Open-Meteo API (free, no key).
**Writes:** `data/raw/<dataset>/<start>_<end>.parquet`, `data/reports/manifest.json`,
`data/reports/gaps_*.csv`, `data/reports/cleaning_log_seed.csv`, and optionally `data/ercot.duckdb`.

---

## How to read the file, top to bottom

The file has seven numbered sections. Read them in order.

### Section 1: CONFIG (lines ~60–180)

This is the only part you are meant to edit.

| Setting | Value | Why it matters |
|---|---|---|
| `MARKET_TZ` | `America/Chicago` | ERCOT runs on Central time. All storage is UTC; local time is derived when needed. |
| `DEFAULT_START` | `2024-01-01` | Open-Meteo's archive of *past forecasts* starts around then. Without archived weather forecasts you can't build leak-free weather features, so the price history starts there too. |
| `HUBS` | 6 trading hubs | The price locations that are downloaded and backtested. |
| `PRIMARY_HUB` | `HB_NORTH` | Only this hub gets 15-minute real-time prices, to save row budget. |
| `CUTOFF_LOCAL`, `CUTOFF_DAYS_BEFORE` | `09:45`, 1 | **The most important constant in the project.** Day-ahead bids close at 10:00 Central on D-1. Anything published after 09:45 on D-1 is treated as unknowable. The 15-minute margin is a safety buffer. |
| `RTCB_GO_LIVE` | 2025-12-05 | ERCOT's real-time co-optimization launch, treated as a structural break. |
| Budget constants | rows/requests | The free plan allows ~500k rows and ~250 requests a month. The script plans chunks of ~45k rows to stay under the 50k per-response cap. |

**`DatasetSpec` and `SPECS`:** each downloaded dataset is declared once as data, not code.
The important fields are:

- `group`: `prices`, `system`, `forecasts` or `context`. `pull --what <group>` downloads one group.
- `publish_time` + `vintage=True`: the forecast is requested **as it looked at the cutoff**
  (`latest_before:-1 day@09:45:00`). This one setting stops the model from seeing forecast
  revisions made after bids closed.
- `verified`: whether the Grid Status dataset ID was confirmed in the docs or inferred. The `check` command confirms inferred IDs.

The wind/solar *actual* datasets use `publish_time="latest"` (the final value). They are marked
"evaluation only, never a feature".

**Weather (`ZONES`, `FCST_LEADS`):** one representative city per ERCOT weather zone. The key comment
is on `FCST_LEADS`: a weather forecast issued ~48 h before the valid hour (`previous_day2`) was always
issued before the D-1 09:45 cutoff, so it is safe. One issued ~24 h before (`previous_day1`) is *after*
the cutoff for afternoon and evening hours, so it leaks. Both are downloaded so the leak can be measured
in an ablation, but only `d2` is used by default.

### Section 2: SMALL UTILITIES

- `month_chunks` / `quarter_chunks`: split a date window into calendar pieces.
- `write_table` / `read_table`: Parquet, with a `.csv.gz` fallback if `pyarrow` is missing.
- `RowBudget`: counts rows and requests spent in this run.
- **Caching logic:** `parse_range_name`, `covered_ranges`, `missing_ranges`. Each raw file is named
  after the date range it covers. The script reads the file names, merges overlapping ranges, and
  downloads only the gaps. That is what makes `pull` resumable and safe to re-run.
- `chunk_days`, `requests_for`: size each request so it stays under the per-response row cap.

### Section 3: GRID STATUS ACCESS

1. `get_client()` exits early with a clear message if the key or the package is missing.
2. `read_limits()` asks *your account* how many rows and requests are left, falling back to free-plan defaults.
3. `fetch_chunk()` builds one API call. It adds a hub filter, server-side hourly resampling (fewer rows),
   and the as-of-cutoff `publish_time` for forecast datasets. It retries with back-off.
4. `pull_specs()` is the download loop: for each dataset → each missing range → each chunk, it checks the
   budget, sleeps 1.3 s (rate limit), fetches, stamps `_pulled_at_utc`, and writes the file.
   Chunks it can't afford are **deferred**, not dropped. Re-run next month and they are fetched.

### Section 4: WEATHER ACCESS

`pull_weather()` loops over zones × calendar quarters and fetches two things:

- **ERA5 observations** (`weather_obs/`): what the weather actually was. Used for exploration only, never as
  a feature. ERA5 lags ~5 days, hence `OBS_LAG_DAYS`.
- **Archived forecasts** (`weather_fcst/`): the `previous_day1/2` variables described above.

A quarter that ended more than 3 days ago is treated as final and cached forever.

### Section 5: LOADING + CHECKS

- `load_dataset()`: concatenates all files of a dataset, converts `*_utc` columns to timezone-aware UTC,
  and de-duplicates on (time, location, publish time), keeping the latest pull.
- `dst_days()`: finds the 23-hour (spring) and 25-hour (fall) local days. ERCOT data on these days is a
  classic source of silent bugs.
- `leakage_summary()`: for every forecast row, computes the cutoff of the day it targets and counts rows
  published after it. It also reports how close to the cutoff publications typically are.
- `gap_report()`: builds the expected time grid per series and lists the missing timestamps.

### Section 6: COMMANDS (one function per CLI verb)

| Command | What it does | What you should see |
|---|---|---|
| `check` | Plan usage, then for each dataset: exists? date range? publish-time column? Suggests the closest real ID if one is wrong. Also checks hub names and Open-Meteo. | All `[ok]`, all 6 hubs found. |
| `plan` | Estimates rows and requests for a window *before* spending any. | A total below your remaining rows. |
| `probe` | Downloads 2 days of load forecast as-of-cutoff and runs the leakage check on it. | `OK: no row was published after the cutoff.` If the median lead is ~6 h, the API is reading the clock in UTC. |
| `pull --what …` | The download (resumable). | Per-chunk log lines, then a summary. |
| `validate [--strict]` | Writes `manifest.json` (rows, duplicates, nulls, gaps, DST days, leakage guard) and `gaps_*.csv`. `--strict` exits with an error on any leak. | Zero duplicates, zero late forecast rows. |
| `quicklook` | First exploration numbers: price distribution per hub, daily max–min spread (the raw arbitrage opportunity), spread before and after RTC+B, and the 10 biggest-spread days. | — |
| `build-db` | Creates DuckDB views over all Parquet folders for ad-hoc SQL. | — |

### Section 7: CLI

`argparse` wiring. Every command that downloads takes `--start` (inclusive) and `--end` (exclusive).
The default end is two days ago, so late ERCOT postings arrive before a day is cached.

---

## What the outputs tell you (from the committed reports)

- `dam_spp_hubs`: 149,472 rows = 24,912 hours × 6 hubs, **0 gaps, 0 duplicates**.
- `load/wind/solar_forecast_asof`: 24,912 rows each, **0 published after the cutoff**. The median lead
  before the cutoff is 0.25 h for load (published 09:30) and ~0.8 h for wind and solar.
- `rt_spp_15min_primary`: 4 missing 15-minute intervals in almost 100k.
- `wind/solar_actual_latest`: 3 missing hours, one on each fall-back DST day (2024-11-03, 2025-11-02,
  2026-11-01). This is a quirk of the source reports on the 25-hour day, not lost data.

## Design choices in this file

See [07_design_decisions.md](07_design_decisions.md#data-collection) for the reasoning behind: raw files
never being edited, one file per date range, UTC storage, as-of vintages instead of filtering
afterwards, and hard API budgets.
