# `analysis_report.py`: Step 4, audit and hypothesis tests

**Purpose:** answer the project's research questions with the backtest output, and add one leakage check
that no automated test can do: comparing real publish times against ERCOT's schedule by eye.

**Run after** `forecast_pipeline.py train` and `battery_backtest.py backtest --extra-forecast …`.
**Reads:** `data/raw/*_forecast_asof`, `data/raw/dam_spp_hubs`, `data/results/daily_revenue_*.parquet`,
`data/processed/forecast_*.parquet`.
**Writes:** `data/reports/publish_time_audit.csv`, `data/results/gap_decomposition.csv`,
`capture_by_month.csv`, `rank_vs_regret.parquet`, `fig_cumulative_regret.png`, `fig_monthly_capture.png`,
`fig_tau_vs_capture.png`.

---

## The hypotheses

| ID | Hypothesis | Tested by |
|---|---|---|
| H1 | An optimizer fed with a better price forecast earns significantly more than naive strategies | `battery_backtest.py` (paired tests vs S3) |
| H2 | A **rank** metric (Kendall τ) tracks lost revenue better than MAE/RMSE | `rank` |
| H3 | The lost revenue (regret) is concentrated on a few extreme days | `gap` |
| H4 | The RTC+B market redesign (2025-12-05) changed how much a forecast can capture | `gap` (pre/post split) |

H1 is never named in the code, but it is what the backtest's paired significance tests answer. The
dashboard labels its tab "H2–H4" for that reason.

---

## How to read the file, top to bottom

### Helpers

- `load_daily()`: reads the daily revenue file and finds the strategy columns (`S0…S6_*`, `ORACLE`).
- `read_raw()` and `cutoff_for()`: the same UTC and cutoff logic as the other scripts (duplicated on
  purpose so each script runs on its own; see the review doc).

### `audit`: were forecasts really public before the cutoff?

For each of the load, wind and solar forecast datasets it prints:

1. how many rows were published after the cutoff (should be 0),
2. the publish clock time distribution in Central time,
3. how many days before the target day the data was published (should be 1),
4. how stale the vintage the model actually used was (anything published >48 h before the cutoff is flagged),
5. a **random sample of days** with publish time and cutoff, saved to `publish_time_audit.csv`.

**Why it exists:** the placebo test proves the signal comes from the fundamentals. It cannot tell
whether a fundamental file was *timed* wrongly. If Grid Status had labelled an 11:00 revision as 09:30,
every automated test would pass. Comparing a few rows with ERCOT's posting schedule by hand closes that gap.

Committed result: load forecasts published at 09:30 (15 min before the cutoff), wind and solar at ~08:55
(about 50 min before). All are on D-1.

### `gap`: where does the distance to the oracle come from? (H3, H4)

For each strategy, regret = oracle − strategy per day. The command then computes:

- `share_of_regret_from_10_worst_days`
- `share_of_regret_on_10_best_oracle_days`
- `share_of_regret_on_top_decile_oracle_days`, compared with `share_of_days_in_top_decile` (≈0.10)
- capture before and after RTC+B
- `days_beating_oracle` is a bug detector and must be 0

It also writes a month × strategy capture table (`capture_by_month.csv`) with the oracle's mean $/day.

**Reading (GBM):** 48.8% of all regret falls on the 10% highest-value days, and 44.6% on just 10 days.
**H3 holds.** Every strategy captures less after RTC+B except fixed windows. **H4 holds**, with the
caveat that the post-RTC+B period is shorter and has a different season mix.

### `rank`: does ordering beat accuracy? (H2)

`daily_metrics()` computes, per day: Kendall τ, MAE, RMSE, and the actual and forecast daily range. These
are merged with each day's regret and capture. It reports Spearman correlations with regret and the mean
capture by τ quartile, and saves `rank_vs_regret.parquet`.

**Reading (GBM, 855 days):**

| | Spearman ρ vs regret | Pearson r vs regret |
|---|---:|---:|
| τ | −0.437 | −0.140 |
| MAE | +0.305 | **+0.857** |
| RMSE | +0.272 | +0.846 |

On a rank basis |ρ(τ)| > |ρ(MAE)|, so **H2 holds as stated**. On a linear dollar basis MAE wins
because spike days have huge errors *and* huge regret. Both views are shown on the dashboard.

### `figs`: report figures

Cumulative regret, monthly capture, and (if `rank` was run) a τ-vs-capture scatter. All are saved as PNG
in `data/results/`.
