# Data and output files

Everything under `data/raw/`, `*.parquet` in `data/processed/` and `data/results/`, and `data/ercot.duckdb`
is git-ignored (large and re-downloadable). The CSV, JSON and PNG outputs below are committed.

## Repo root

| File | Produced by | Content |
|---|---|---|
| `index.html` | hand-written | The dashboard ([05_dashboard.md](05_dashboard.md)) |
| `data.js` | assembled (no script) | `window.TEXAS_GRID_DATA = {…}`, identical to `dashboard_data.json`. A `.js` file is used so the page works from `file://` without a web server (browsers block `fetch()` of local JSON). |
| `dashboard_data.json` | assembled (no script) | The same data as plain JSON |
| `sim_sample_profiles.json` | assembled | The 6 what-if days (also inside `data.js`) |
| `file.md` | copied console output | `dp-check` and every `backtest` run |
| `push.sh` | hand-written | Sets the git remote and pushes `main` after confirmation |
| `*.py` | — | The four pipeline scripts, see docs 01–04 |

## `data/raw/` (git-ignored), written by `ercot_pipeline.py pull`

| Folder | Content | Used as |
|---|---|---|
| `dam_spp_hubs/` | Hourly day-ahead settlement point prices, 6 hubs | **Target** and price-lag features |
| `rt_spp_15min_primary/` | 15-min real-time prices, HB_NORTH | Not used yet |
| `load_system_hourly/` | Actual system load | `actlag2d_load` |
| `load_weather_zones/` | Actual load by weather zone | Not used yet |
| `fuel_mix_hourly/` | Generation by fuel | `actlag2d_{wind,solar,gas,storage}` |
| `load_forecast_asof/` | ERCOT load forecast as of D-1 09:45 | `lf_*` |
| `wind_forecast_asof/`, `solar_forecast_asof/` | ERCOT wind/solar forecasts as of cutoff | `wind_*`, `solar_*` |
| `wind_actual_latest/`, `solar_actual_latest/` | Final reports = actual output | Not used yet (evaluation only) |
| `weather_obs/<ZONE>/` | ERA5 observed weather | Exploration only |
| `weather_fcst/<ZONE>/` | Archived 24 h / 48 h-lead weather forecasts | `wx_*` |

## `data/reports/` (committed)

| File | Produced by | What to look for |
|---|---|---|
| `manifest.json` | `ercot_pipeline.py validate` | Per dataset: rows, duplicates, null %, date range, gaps per series, DST-day row counts, **leakage guard** (0 late rows everywhere) |
| `gaps_<dataset>.csv` | `validate` | The exact missing timestamps (4 in real-time, 3 DST-day hours in wind/solar actuals) |
| `cleaning_log_seed.csv` | `validate` | Raw → deduplicated row counts |
| `cleaning_log.csv` | `forecast_pipeline.py build` | Every cleaning step: rows in → out and the rule |
| `build_report.json` | `build` | Feature count, NaN share per feature group, **leakage test results** |
| `schema.txt` | `battery_backtest.py schema` | Columns, types and sample rows of every raw dataset |
| `publish_time_audit.csv` | `analysis_report.py audit` | Random vintages with publish time vs cutoff |

## `data/processed/` (committed: CSVs only)

| File | Produced by | Columns |
|---|---|---|
| `forecast_metrics_HB_NORTH.csv` | `train` | model, MAE, RMSE, MAE_asinh, worst_abs_error, kendall_tau, MAE_pre/post_RTCB, DM_vs_avg7_stat/p |
| `ablation_HB_NORTH.csv` | `ablate` | variant, features, MAE, RMSE, kendall_tau, capture_of_oracle, capture_change_vs_full |
| *(ignored)* `model_table.parquet` | `build` | The full hourly feature table |
| *(ignored)* `forecast_<model>_<hub>.parquet` | `train` | interval_start_utc, location, spp_fc |

## `data/results/` (committed: CSVs and PNGs)

| File | Produced by | Content |
|---|---|---|
| `summary_<tag>.csv` | `backtest` | One row per strategy (see [03](03_battery_backtest.md)). Tags: `HB_*_2h_rte85_degr0` per hub, plus `HB_NORTH_4h_rte88_degr3` |
| `paired_vs_persistence_<tag>.csv` | `backtest` | strategy, reference, mean_daily_gain, ci_low, ci_high, significant |
| `summary_all_hubs.csv` | `backtest --hub all` | All hubs stacked (1,030 days, no ML) |
| `value_of_forecast_HB_NORTH.csv` | `voc` | noise_sigma (or `blend_persistence_α`), capture |
| `gap_decomposition.csv` | `analysis_report.py gap` | Regret concentration and pre/post capture per strategy |
| `capture_by_month.csv` | `gap` | Month × strategy capture, plus the oracle's mean $/day |
| `fig_cumulative_revenue_<tag>.png`, `fig_capture_rate_<tag>.png` | `backtest` | Static report figures |
| `fig_value_of_forecast_HB_NORTH.png` | `voc` | |
| `fig_cumulative_regret.png`, `fig_monthly_capture.png`, `fig_tau_vs_capture.png` | `analysis_report.py figs` | |
| *(ignored)* `daily_revenue_<tag>.parquet` | `backtest` | One row per day: revenue and cycles per strategy, plus regime. The source of `daily` in `data.js` |

**Units:** revenue in the summaries is $/MW-year. Daily values are $/day for the whole 100 MW battery.
`cum_*` and `regret_*` in `data.js` are in $ million.
