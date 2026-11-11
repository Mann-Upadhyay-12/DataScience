# `forecast_pipeline.py`: Step 3, leak-safe features and ML price forecasts

**Purpose:** turn the raw downloads into one hourly feature table that only contains information
available at 09:45 on D-1. Then train price forecasting models day by day, the way they would have been
used live. Finally, prove where the forecasting skill comes from (ablation + placebo).

**Reads:** `data/raw/*` (from step 1).
**Writes:** `data/processed/model_table.parquet`, `forecast_{gbm,lasso,ens}_<HUB>.parquet`,
`forecast_metrics_<HUB>.csv`, `feature_importance_gbm_<HUB>.csv`, `ablation_<HUB>.csv`,
`data/reports/cleaning_log.csv`, `data/reports/build_report.json`.

```bash
python forecast_pipeline.py build
```

```bash
python forecast_pipeline.py train --hub HB_NORTH
```

```bash
python forecast_pipeline.py ablate --hub HB_NORTH
```

---

## The rule the whole file enforces

> A forecast for local day **D** is made at **09:45 US-Central on D-1**.

Every feature column must therefore carry a prefix from this whitelist (`ALLOWED_PREFIXES`):

| Prefix | Meaning | Why it is safe |
|---|---|---|
| `cal_` | hour, weekday, month, season (sin/cos), weekend, US holiday, post-RTC+B flag | Known years in advance |
| `lf_` | ERCOT load forecast per weather zone + system total | Pulled as published before the cutoff |
| `wind_` | ERCOT wind forecasts (COP HSL, STWPF, WGRPP) per region | Same |
| `solar_` | ERCOT solar forecasts (COP HSL, STPPF, PVGRPP) per region | Same |
| `nl_` | net load = load − wind − solar forecasts, its 1 h / 3 h ramps, wind and solar shares | Computed only from the three above |
| `wx_` | weather forecasts issued ~48 h before the valid hour | Always issued before the cutoff |
| `actlag2d_` | *actual* wind, solar, gas, storage output and load, **2 days earlier** | D-2 actuals are published by D-1 morning |
| `plag*`, `pday1_*` | the hub's own day-ahead prices from earlier days (added in `train`) | D-1's day-ahead prices are published on D-2 |

`wxd1unsafe_` exists only so the leak from 24 h-lead weather can be *measured*. It prints a warning
whenever it is used.

---

## How to read the file, top to bottom

### Section 1: Helpers

- `CleaningLog`: every cleaning step records rows in → rows out and the rule applied, and prints a line.
  The log is saved as `data/reports/cleaning_log.csv` (an audit trail for the report).
- `read_raw()`: loads a raw dataset and parses timestamps to UTC.
- `local_lag(s, days)`: the value at the **same local clock hour** `days` earlier. A plain
  `shift(24)` would be off by one hour on DST days, so it joins on (local date, local hour) instead.
- `cutoff_utc()`: for each target hour, the UTC timestamp of the D-1 09:45 cutoff (DST-aware).

### Section 2: `build`

`load_asof()` handles each ERCOT forecast dataset in four logged steps:

1. **leak-drop:** drop any row published after its cutoff. This is a second line of defence: the
   download already asked for as-of-cutoff vintages.
2. **dedupe:** keep one row per target hour, the latest publication before the cutoff.
3. **columns:** keep the columns matching a regex and drop columns that are >90% empty.
4. **align:** reindex to the hourly UTC grid. Gaps stay `NaN`. **Nothing is interpolated.**

`build_weather()` loads every zone's forecast file. It keeps per-zone temperature, 100 m wind speed and
solar radiation, plus zone averages of six variables (30 columns). Forecasts start on 2024-01-20, and
earlier hours stay `NaN`.

`cmd_build()` then:

1. Pivots day-ahead prices to one column per hub (`dam_HB_NORTH`, …) and flags missing hours.
2. Builds calendar features and `meta_local_date` / `meta_hour` (used for grouping, never as model inputs).
3. Adds the as-of forecasts, the net-load features, the 2-day-lagged actuals and the weather.
4. **Runs the leakage tests** and writes `build_report.json`:
   - `*_published_after_cutoff`: late rows found (and dropped). Committed result: **0** for all three.
   - `T2_unlisted_feature_columns`: features whose prefix isn't whitelisted. Committed result: **none**.
   - `T4_actual_columns_in_features`: any `gen_` column, i.e. actual generation. Committed result: **none**.
   - `T3_lag_spotcheck_passed_of_5`: 5 random rows of `actlag2d_wind` re-derived by hand from the raw
     series. Committed result: **5/5**.
   - If any test fails, the script **exits** and training cannot start on bad data.

Result: 24,912 hourly rows × 96 fundamental features (106 once price lags are added).

### Section 3: `train` (walk-forward)

1. `hub_price_features()` adds price lags at 1, 2 and 7 days, the 7-day same-hour average (needs ≥5 of
   7 days), and yesterday's mean, max, min, std and spread. It also adds "yesterday minus the 7-day
   average", a momentum signal.
2. **Target transform:** the model learns `asinh(price / 50)`. That is roughly linear near $0–100 and
   logarithmic for spikes, so a single $5,000 hour doesn't dominate training. `to_usd()` inverts it.
3. **Models:**
   - `Gbm`: LightGBM with 400 trees, learning rate 0.03, 31 leaves, row/column subsampling, L2 = 1.
     Falls back to sklearn `HistGradientBoosting` if LightGBM is missing. Missing values are handled natively.
   - `HourlyLasso`: 24 separate Lasso regressions, one per local hour ("LEAR"-style, after Lago et al.).
     Features are standardised and clipped to ±4σ, and predictions are clipped to the training target range.
     Without the clipping, one extreme input produced an RMSE of $526/MWh in an earlier run. The penalty
     `alpha` is tuned once per hour by time-series cross-validation on the **first** training window only.
   - `ens`: the average of the two in asinh space.
4. `walk_forward()` splits the test period (from 2024-07-01) into 7-day blocks. For each block it trains
   on **all rows strictly before the block's first day** (expanding window), then predicts the block.
   No test-period information is ever used in training.
5. **Evaluation** on identical test days with ≥23 hours: MAE, RMSE, MAE in asinh space, worst miss,
   mean daily Kendall τ, MAE before and after RTC+B, and a **Diebold–Mariano test** vs the 7-day average
   (`dm_test`, Newey–West variance with 7 lags, on daily mean absolute errors).
6. Writes forecasts as `interval_start_utc, location, spp_fc` (the format `battery_backtest.py
   --extra-forecast` expects), the metrics CSV, and the top-30 GBM feature importances.

Committed result (`data/processed/forecast_metrics_HB_NORTH.csv`):

| Model | MAE | RMSE | Kendall τ | DM p vs avg7 |
|---|---:|---:|---:|---:|
| persistence | 12.20 | 50.2 | 0.619 | 0.53 |
| weekly naive | 16.42 | 65.1 | 0.578 | <0.001 (worse) |
| 7-day average | 12.74 | 48.1 | 0.673 | — |
| **GBM** | **6.93** | **28.4** | **0.800** | <0.001 (better) |
| Lasso | 10.67 | 45.8 | 0.633 | 0.044 (better) |
| Ensemble | 7.97 | 40.1 | 0.753 | <0.001 (better) |

### Section 4: `ablate`

`ablate` retrains GBM nine times (re-training every 28 days to keep the run time down) and measures how
much **battery revenue** is lost, not just forecast error. It uses `quick_capture()`, which imports the
real optimizer from `battery_backtest.py`.

| Variant | Inputs removed | Result (capture) | Reading |
|---|---|---:|---|
| full | — | 93.0% | reference |
| prices_and_calendar_only | every fundamental | 87.7% (−5.3) | the total value of fundamentals |
| no_load_forecast | `lf_`, `nl_` | 92.2% | small |
| **no_wind_forecast** | `wind_`, `nl_` | **88.9% (−4.0)** | the most valuable group |
| no_solar_forecast | `solar_`, `nl_` | 91.6% | moderate |
| no_net_load_features | `nl_` | 92.0% | mostly redundant |
| no_weather | `wx_` | 93.0% | ≈0: ERCOT's own forecasts already contain the weather |
| no_actual_lags | `actlag2d_` | 92.7% | small |
| **PLACEBO** | fundamentals shuffled within each hour of day | **85.4% (−7.6)** | collapses to the naive level, so the signal is real |

**How the placebo works:** `prepare(..., placebo="shuffle")` permutes every fundamental column across
days but within the same hour of day. The values stay realistic (a 3 p.m. load stays a 3 p.m. load) but
no longer belong to the right day. A model that was secretly getting signal from a mis-timed column would
barely notice. This one loses everything the fundamentals gave it.

---

## Design choices in this file

See [07_design_decisions.md](07_design_decisions.md#features-and-forecasting): the prefix whitelist,
no interpolation, asinh target, two contrasting model families, the expanding walk-forward window,
alpha tuned only once, and evaluating the ablation in dollars rather than MAE.
