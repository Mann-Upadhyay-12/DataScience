# Review findings and next steps

## What is solid

- **Leakage control:** as-of-cutoff vintages, a second filter in `build`, a prefix whitelist, the T2/T3/T4
  tests, the placebo, and a hand audit. This is unusually thorough for a price-forecasting backtest.
- **Optimizer correctness:** the LP is cross-checked against an independent DP (0.12% mean gap, LP ≥ DP every time).
- **Statistics:** identical days, paired differences, block bootstrap, Diebold–Mariano.
- **DST handling:** local days of 23/24/25 hours, `local_lag` joining on clock hour, explicit DST-day checks in `validate`.

## Known issues and caveats (ordered by impact on the headline numbers)

1. **Simultaneous charge/discharge is allowed in the committed results.** The LP may charge and discharge
   in the same negative-price hour, which inflates revenue a little. `file.md` reports 15 such solves at
   HB_NORTH and 85 at HB_PAN (out of ~4,100 optimizer solves per hub: 1,030 days × 4 optimized strategies). **Fix:** re-run with `--no-simul` and publish both.
   HB_PAN is the hub most likely to move. (The old dashboard claimed this was prohibited. That claim has
   been removed.)
2. **The load-forecast margin is thin:** the vintage used is published at 09:30 against a 09:45 cutoff. If
   ERCOT ever posts late, the as-of query silently falls back to the previous day's vintage. That is safe
   (no leak), but the model would be slightly worse in live use than in the backtest. **Fix:** report how
   often the fallback happens (the `audit` "stale vintage" line already counts it).
3. **ML exists for HB_NORTH only.** The siting comparison ranks hubs on naive strategies. HB_PAN has the
   most "left on the table" and is where ML would matter most. **Fix:** `train`/`backtest` for every hub (the code already supports `--hub`).
4. **The post-RTC+B sample is short and has a different season mix:** 333 days, Dec 2025 – Nov 2026. Part of the H4 drop
   could be seasonal. **Fix:** compare the same calendar months year over year.
5. **The dashboard data is not reproducible from the repo.** `dashboard_data.json`/`data.js` were
   assembled by hand from the results. **Fix:** add a `build_dashboard_data.py` (see below).
6. **The cutoff/timezone logic is duplicated** in three scripts (`cutoff_utc`, `cutoff_for`,
   `leakage_summary`), along with `HUBS`, `TZ` and `RTCB` constants. It is consistent today, but it could drift.
   **Fix:** a small shared `common.py`.
7. **There are no automated tests or pinned dependencies** (`requirements.txt`). Library changes, e.g. the
   pandas `include_groups` argument or sklearn renaming `n_alphas`, have already required workarounds in the code.
8. **Downloaded but unused data:** real-time 15-min prices, load by weather zone, wind/solar actuals, ERA5
   observations, and AS prices (configured, off by default).
9. `push.sh` hardcodes the `main` branch and an SSH remote.

## Suggested next steps

### Quick wins (hours)

- [ ] `python battery_backtest.py backtest --hub all --no-simul` and compare with the current numbers (issue 1).
- [ ] `build_dashboard_data.py`: read `data/results/*.csv`, `daily_revenue_*.parquet` and
      `rank_vs_regret.parquet`, then write `dashboard_data.json` and `data.js`, so the dashboard is regenerated in one command.
- [ ] `requirements.txt` with pinned versions, and a `Makefile` (or `run_all.sh`) that runs the full chain.
- [ ] `common.py` for the shared constants and the cutoff function.
- [ ] `pytest` tests: cutoff on DST days, `local_lag` across DST, LP vs DP on synthetic prices, the leak tests on a planted leak.

### Dashboard (hours to a day)

- [ ] **Any-day picker:** ship actual + GBM forecast prices for all 855 days (~200 KB) so the what-if tab works on every day, not just 6.
- [ ] Feature-importance chart (from `feature_importance_gbm_HB_NORTH.csv`, which isn't committed yet).
- [ ] A "download this table as CSV" button per table.
- [ ] A calendar heatmap of daily regret, to see at a glance which days and seasons cost money.

### Modelling (days)

- [ ] **ML for all six hubs**, then redo the siting with GBM.
- [ ] **Decision-focused training:** H2 shows ordering matters more than dollars, so train on a pairwise
      ranking loss or directly on regret (e.g. "smart predict-then-optimize"), not on MAE.
- [ ] **Probabilistic forecasts + stochastic optimisation:** quantile GBM → scenarios → maximise expected
      revenue (or a risk-adjusted version). This should help most on the spike days that drive H3.
- [ ] **Spike-day classifier:** predict "tomorrow is a top-decile day" and plan more conservatively or aggressively.
- [ ] Rolling vs expanding training window after RTC+B. Nested walk-forward hyperparameter tuning.

### Scope extensions (weeks)

- [ ] **Real-time market:** settle day-ahead commitments plus real-time deviations using the 15-min prices already downloaded.
- [ ] **Ancillary services** (`as_prices` spec already exists): co-optimise energy and reserves, the bulk of real ERCOT battery revenue.
- [ ] Multi-day state of charge (carry energy overnight), and cycle-dependent degradation.
- [ ] Multi-hub portfolio: does owning batteries at two hubs diversify revenue?
