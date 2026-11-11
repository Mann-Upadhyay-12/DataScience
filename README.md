# Texas Grid Battery Dispatch & Valuation Lab

**Question:** if you own a 100 MW / 200 MWh battery in Texas and have to commit tomorrow's
charge/discharge schedule by 10:00 today, how much money can you make on the ERCOT day-ahead market,
and how much of that depends on how well you forecast tomorrow's prices?

**Short answer (HB_NORTH, Jul 2024 – Nov 2026, 855 days):**

| Strategy | Revenue $/MW-yr | Share of perfect foresight |
|---|---:|---:|
| Perfect foresight ("Oracle", impossible ceiling) | $36,463 | 100% |
| ML forecast (gradient boosting) + optimizer | $33,931 | **93.1%** |
| Yesterday's prices + optimizer | $30,561 | 83.8% |
| Fixed clock windows, no forecast | $24,381 | 66.9% |

The ML edge comes from the wind/load/solar forecasts, not from leaked information: a placebo test that
scrambles those inputs collapses performance back to the naive level.

---

## Open the dashboard

Open `index.html` in a browser (it reads `data.js` from the same folder). No server or build step is
needed. The **Start Here** tab explains everything; [docs/05_dashboard.md](docs/05_dashboard.md) describes
every panel.

## How the project fits together

```
 ercot_pipeline.py      forecast_pipeline.py         battery_backtest.py        analysis_report.py
 ─────────────────      ────────────────────         ───────────────────        ──────────────────
 STEP 1: download   ──► STEP 3: clean, join,    ──►  STEP 2: optimize the  ──►  STEP 4: audit and
 prices, forecasts,     leak-safe features,          battery on each           hypothesis tests
 weather; validate      walk-forward ML forecasts    forecast, settle at       (H2 rank, H3 gap,
                                                     actual prices             H4 RTC+B), figures
      │                        │                            │                         │
      ▼                        ▼                            ▼                         ▼
  data/raw/  data/reports/   data/processed/            data/results/            data/results/
                                                                                       │
                                     dashboard_data.json  ──►  data.js  ──►  index.html (dashboard)
```

The step numbers come from the order the scripts were written. You can run the baseline backtest
(step 2) before any ML exists. The ML forecasts are then fed back into it.

## Documentation

| Doc | What it covers |
|---|---|
| [docs/01_ercot_pipeline.md](docs/01_ercot_pipeline.md) | Step 1: data download, API budgets, the as-of-cutoff rule, validation |
| [docs/02_forecast_pipeline.md](docs/02_forecast_pipeline.md) | Step 3: feature table, leakage tests, GBM / Lasso, walk-forward training, ablation |
| [docs/03_battery_backtest.md](docs/03_battery_backtest.md) | Step 2: battery model, LP optimizer, strategies S0–S6, bootstrap statistics |
| [docs/04_analysis_report.md](docs/04_analysis_report.md) | Step 4: publish-time audit, regret decomposition, rank-vs-regret test |
| [docs/05_dashboard.md](docs/05_dashboard.md) | `index.html`: every tab, chart and number, and how it is computed |
| [docs/06_data_files.md](docs/06_data_files.md) | Every file under `data/` and in the repo root: what produced it, what's in it |
| [docs/07_design_decisions.md](docs/07_design_decisions.md) | Why things were done this way, and the alternatives that were rejected |
| [docs/08_review_and_next_steps.md](docs/08_review_and_next_steps.md) | Known issues, caveats, and a prioritized list of improvements |
| [file.md](file.md) | Raw console output of the backtest runs |

## Reproduce from scratch

```bash
pip install numpy pandas pyarrow scipy scikit-learn lightgbm matplotlib requests gridstatusio duckdb
```

```bash
export GRIDSTATUS_API_KEY=your_key   # free account at gridstatus.io
```

```bash
python ercot_pipeline.py check && python ercot_pipeline.py probe
```

```bash
python ercot_pipeline.py pull --what prices && python ercot_pipeline.py pull --what forecasts && python ercot_pipeline.py pull --what system && python ercot_pipeline.py pull --what weather
```

```bash
python ercot_pipeline.py validate
```

```bash
python forecast_pipeline.py build && python forecast_pipeline.py train --hub HB_NORTH && python forecast_pipeline.py ablate --hub HB_NORTH
```

```bash
python battery_backtest.py dp-check
```

```bash
python battery_backtest.py backtest --hub HB_NORTH --extra-forecast gbm=data/processed/forecast_gbm_HB_NORTH.parquet --extra-forecast lasso=data/processed/forecast_lasso_HB_NORTH.parquet --extra-forecast ens=data/processed/forecast_ens_HB_NORTH.parquet
```

```bash
python battery_backtest.py backtest --hub all && python battery_backtest.py backtest --duration 4 --rte 0.88 --degr 3 && python battery_backtest.py voc
```

```bash
python analysis_report.py audit && python analysis_report.py gap --daily data/results/daily_revenue_HB_NORTH_2h_rte85_degr0.parquet
```

```bash
python analysis_report.py rank --daily data/results/daily_revenue_HB_NORTH_2h_rte85_degr0.parquet --forecast gbm=data/processed/forecast_gbm_HB_NORTH.parquet --forecast ens=data/processed/forecast_ens_HB_NORTH.parquet
```

The free Grid Status plan caps rows and requests per month, so the downloads may need two billing
periods. The pipeline resumes where it stopped. Raw data and Parquet files are git-ignored, so only
the small CSV/JSON results are committed.

> **Note:** no script in the repo writes `dashboard_data.json` / `data.js`. They were assembled from the
> results files and the (git-ignored) daily Parquet outputs. See
> [docs/08_review_and_next_steps.md](docs/08_review_and_next_steps.md).
