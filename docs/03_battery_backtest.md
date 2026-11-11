# `battery_backtest.py`: Step 2, from a price forecast to dollars

**Purpose:** for every day, decide a charge/discharge schedule the day before from some price forecast,
pay it at the **actual** day-ahead prices, and compare strategies on identical days with honest
uncertainty.

**Reads:** `data/raw/dam_spp_hubs/*.parquet` and, optionally, ML forecasts from `data/processed/`.
**Writes:** `data/results/daily_revenue_<tag>.parquet`, `summary_<tag>.csv`,
`paired_vs_persistence_<tag>.csv`, `summary_all_hubs.csv`, `value_of_forecast_<hub>.csv`, figures.
`<tag>` encodes the run settings, e.g. `HB_NORTH_2h_rte85_degr0`.

```bash
python battery_backtest.py dp-check
```

```bash
python battery_backtest.py backtest --hub HB_NORTH --extra-forecast gbm=data/processed/forecast_gbm_HB_NORTH.parquet
```

---

## How to read the file, top to bottom

### Section 1: The battery model

```python
Battery(power_mw=100, duration_h=2, rte=0.85, degr=0.0, cycles=1.0, no_simul=False)
```

- `energy = power × duration`: 200 MWh by default.
- `eta = √rte`: efficiency loss is split evenly between charging and discharging (0.922 each way).
- `degr`: a wear cost in $ per MWh **discharged**.
- `cycles`: at most `cycles × energy` MWh may be discharged per day (1.0 = one full cycle).
- `no_simul`: forbid charging and discharging in the same hour (needs a MILP, slower).

### Section 2: Data, one `Day` object per local day

`load_days(hub)` groups hourly prices by **local** calendar date, so each `Day` has 23, 24 or 25 hours.
`is_valid` requires the correct hour count and no NaN. `by_label` maps a day onto 24 "clock hour" slots.
On the 25-hour day the repeated 1 a.m. keeps its first occurrence. On the 23-hour day the missing slot
is interpolated (only for building naive forecasts, never for settlement).

`forecast_from_lags(days, d, lags)` creates the naive forecasts: the mean of the same clock hour on days
`d − lag`. With `[1]` it is persistence, `[7]` is weekly naive, and `1..7` is the 7-day average.

### Section 3: The optimizer (the core)

`solve_day(price_fc, bat)` solves a **linear program** that maximises *forecast* profit:

```
maximise   Σ_t  p̂_t · (d_t − c_t) − degr · d_t
subject to SoC_t = Σ_{s≤t} (η·c_s − d_s/η)       (energy bookkeeping, built with a lower-triangular matrix)
           0 ≤ SoC_t ≤ E                           (can't go below empty or above full)
           SoC_T = 0                               (start and end each day empty)
           Σ_t d_t ≤ cycles · E                    (≤ 1 cycle per day)
           0 ≤ c_t, d_t ≤ P                         (inverter limit)
```

It uses SciPy's HiGHS solver. With `--no-simul` it becomes a MILP: a binary `u_t` per hour forces
charge-only or discharge-only.

> **Why simultaneous charge + discharge matters:** with a negative price, the LP can "charge" and
> "discharge" in the same hour and get paid to burn energy as efficiency losses. That is physically
> questionable. The committed results were run *without* `--no-simul`, and the console output in
> `file.md` reports how many solves did this (15 at HB_NORTH, 85 at HB_PAN). See
> [08_review_and_next_steps.md](08_review_and_next_steps.md).

`dp_value()` solves the same problem a completely different way: backward induction over 100
discretised charge levels, with no LP involved. `dp-check` runs both on 40 random days and requires them
to agree. Committed result: mean gap **0.12%**, max 2.4%, LP ≥ DP every time → **PASS**. This proves the
LP formulation (signs, efficiency, bounds) is right.

`settle(c, d, actual, bat)` pays the schedule at **actual** prices. This is the single place where
revenue is computed.

`run_rule()` simulates the two rule-based strategies: charge or discharge at full power in the chosen
hours until the battery is full or empty.

### Section 4: The backtest

`valid_days_for_all()` keeps only days where *every* strategy can run: the day is valid, the 1-, 7- and
1..7-day lags exist, and every ML forecast covers it. That is why every strategy is compared on
**exactly the same days**, so the differences between them are paired.

`run_backtest()` runs these strategies for each day:

| ID | Strategy | How the schedule is chosen |
|---|---|---|
| S0 | No battery | 0, the zero reference |
| S1 | Fixed windows | charge from 11:00, discharge from 19:00, for ⌈duration⌉ hours each |
| S2 | Yesterday rule | charge in yesterday's cheapest ⌈duration⌉ hours, discharge in its priciest |
| S3 | Persistence + LP | LP on yesterday's prices. **The reference for all paired tests** |
| S4 | Weekly naive + LP | LP on the same day last week |
| S5 | 7-day average + LP | LP on the 7-day same-hour average |
| S6_* | Your models + LP | LP on each `--extra-forecast` file |
| ORACLE | Perfect foresight + LP | LP on the actual prices: the upper bound |

For each optimizer strategy it also records the day's Kendall τ between forecast and actual prices.

`summarize()` produces one row per strategy:

- `revenue_per_mw_year` = total revenue / MW / (days / 365)
- `capture_of_oracle` = Σ strategy / Σ oracle, a **ratio of sums**, so big days weigh more (which is
  what a battery owner cares about)
- `capture_ci_low/high`: 95% interval from a **7-day block bootstrap** (2,000 resamples)
- `capture_pre_rtcb / post_rtcb`: the same ratio split at 2025-12-05
- `worst5pct_day_avg`, `top10_days_share`, `cycles_per_day`, `kendall_tau`

`paired_vs()` asks the key question: is strategy X really better than S3, or just lucky? It
bootstraps the **daily difference** X − S3 in 7-day blocks. `significant=True` means the 95% interval
excludes zero.

`make_plots()` draws the cumulative revenue (with an RTC+B marker) and a capture-rate bar chart with CIs.

### Section 5: Commands

| Command | Purpose |
|---|---|
| `backtest [--hub X\|all] [--duration --rte --degr --cycles --no-simul --start --end] [--extra-forecast name=path]` | The main experiment. `--hub all` also writes the siting table `summary_all_hubs.csv`. |
| `dp-check` | LP vs DP proof of correctness. |
| `voc` | **Value of forecast:** add Gaussian noise σ ∈ {0…160} $/MWh to the true prices, optimise on the noisy "forecast", and measure capture. Also blends truth with persistence. Shows how quickly money disappears as forecasts get worse. |
| `schema` | Dumps every raw dataset's columns → `data/reports/schema.txt`. |

---

## Committed headline results (HB_NORTH, 100 MW × 2 h, RTE 85%)

**855 days, with ML** (`data/results/summary_HB_NORTH_2h_rte85_degr0.csv`):

| Strategy | $/MW-yr | Capture | 95% CI | Gain vs S3 $/day | Significant |
|---|---:|---:|---|---:|---|
| ORACLE | 36,463 | 100% | — | +1,617 | yes |
| S6 GBM | 33,931 | 93.1% | 88.7–96.0% | +923 | **yes** |
| S6 Ensemble | 32,999 | 90.5% | 85.3–94.6% | +668 | yes |
| S5 7-day avg | 31,191 | 85.5% | 76.5–91.7% | +173 | no |
| S3 Persistence | 30,561 | 83.8% | 78.0–88.7% | ref | — |
| S4 Weekly naive | 29,552 | 81.0% | | −276 | no |
| S6 Lasso | 29,574 | 81.1% | | −270 | yes (worse) |
| S1 Fixed windows | 24,381 | 66.9% | | −1,693 | yes (worse) |
| S2 Yesterday rule | 24,323 | 66.7% | | −1,709 | yes (worse) |

**Note:** the hub siting runs (`--hub all`, no ML) use 1,030 days starting 2024-01-08. That window
includes the lucrative first half of 2024, so HB_NORTH's oracle is $40,968 there. Never compare numbers
across the two windows.

## Design choices in this file

See [07_design_decisions.md](07_design_decisions.md#optimisation-and-evaluation): why an LP and not a
heuristic, why each day stands alone, why the oracle is the denominator, why paired block-bootstrap
tests, and why S3 is the reference.
