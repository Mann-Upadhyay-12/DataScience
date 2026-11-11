# The dashboard (`index.html`): what every panel shows and how it is computed

`index.html` is a single self-contained page. It loads Chart.js 4.4.1 from jsDelivr and the results from
`data.js`, which sets `window.TEXAS_GRID_DATA`. That object has the same content as
`dashboard_data.json`. Everything you see is **computed in the browser from that object**. Apart from
the hub descriptions and glossary text, no numbers are typed into the HTML, so if the data is
regenerated, the page updates itself.

The page can be deep-linked with `#start`, `#sim`, `#backtest`, `#ablation`, `#hyp`, `#siting` or `#gloss`.

---

## Data object (`window.TEXAS_GRID_DATA`)

| Key | Rows | Source | Used on |
|---|---:|---|---|
| `summary_hb_north` | 10 | `summary_HB_NORTH_2h_rte85_degr0.csv` (855-day ML run) | KPIs, tabs 0, 2, 4 |
| `paired` | 9 | `paired_vs_persistence_HB_NORTH_…csv` | tabs 0, 2 |
| `daily` | 855 | daily revenue, cumulative $M and cumulative regret $M per strategy, plus regime | tabs 2, 4 |
| `capture_m` | 29 | `capture_by_month.csv` | tab 2 (monthly view) |
| `forecast_metrics` | 6 | `forecast_metrics_HB_NORTH.csv` | tab 2 |
| `ablation` | 9 | `ablation_HB_NORTH.csv` | KPIs, tabs 0, 3 |
| `audit` | 18 | `publish_time_audit.csv` | tab 3 |
| `rank_scatter` | 855 | per-day τ, MAE, RMSE, capture, regret for GBM (`rank_vs_regret.parquet`) | tab 4 (H2) |
| `gap` | 8 | `gap_decomposition.csv` | tabs 0, 4 (H3) |
| `voc` | 12 | `value_of_forecast_HB_NORTH.csv` | tab 4 |
| `summary_all` | 42 | `summary_all_hubs.csv` (1,030-day siting run) | tab 5, start-tab note |
| `siting_pivot`, `siting_capture` | 6 hubs | pivots of `summary_all` | KPIs, tab 5 |
| `summary_4h` | 7 | `summary_HB_NORTH_4h_rte88_degr3.csv` | tab 5 (sizing) |
| `sim_profiles` | 6 days | actual + GBM forecast hourly prices for 6 hand-picked HB_NORTH days | tab 1 |

`-999` means "not applicable" (e.g. Kendall τ for strategies that don't use a forecast). The page shows it as "—".

---

## Shared elements

- **KPI ribbon** (all tabs): six headline numbers, each with a "what it means" line. Click one to open
  the tab with its evidence. The values come from `summary_hb_north`, `ablation` and `siting_pivot`.
  The τ meaning uses (1+τ)/2 = the share of hour pairs ordered correctly.
- **"How to read" boxes** (dashed border) explain the chart *before* you look at it.
- **"What it means" boxes** (green border) are written by JavaScript from the data, so they update
  when you change a control.
- **Dotted-underlined terms** show a definition on hover, tap or keyboard focus. The same definitions
  make up the Glossary tab (object `G` in the script).

---

## Tab 0: Start Here

| Panel | Content |
|---|---|
| The question | The project in one paragraph, plus how a battery earns money in three bullets |
| Pipeline | 5 steps: Collect → Forecast → Optimize → Settle → Score |
| Key findings | Six cards with a computed headline number and a button to the evidence |
| Read this before trusting the numbers | The modelling simplifications, and why HB_NORTH shows $36,463 on tab 2 but $40,968 on tab 5 (855 vs 1,030 days) |

## Tab 1: What-If Dispatch (the playground)

**What happens when you move a control:** the page re-solves two schedules for the chosen day.

1. **Oracle plan:** optimised on the actual prices.
2. **Forecast plan:** optimised on the GBM D-1 forecast (`forecast_prices`), i.e. what was known at 09:45 the day before.

Both are **paid at actual prices**. Capture = forecast plan ÷ oracle plan.

**Solver** (`dpSchedule`, `planDay`): an exact dynamic program over 80 state-of-charge levels with the
same physics as the backtest LP (√RTE each way, power limit, starts and ends empty). The daily cycle
limit is enforced with a **Lagrangian price**: if the plan cycles too much, a penalty λ $/MWh is added
to discharging and found by 16 bisection steps until the limit holds. This avoids adding a second state
dimension and stays fast (~15 ms for both plans).

| Control | Effect |
|---|---|
| Market day | 6 real days, sorted by spread |
| Power (MW) | Inverter size |
| Duration (h) | Energy = power × duration |
| RTE | Round-trip efficiency, raises or lowers the break-even spread |
| Degradation $/MWh | Wear cost per MWh discharged |
| Volatility multiplier | `p' = mean + k·(p − mean)` for both actual and forecast. It stretches the daily shape and keeps the average. (The old version multiplied every price by k, which only scaled revenue linearly.) |
| Cycle limit | Max discharge per day in full-battery units. The backtest uses 1.0 |
| Surprise midday crash | Sets 11:00–16:00 **actual** prices to −$15. The forecast doesn't change, so you can see the cost of an event the model didn't predict |
| Show: Forecast / Oracle plan | Which schedule the bars, state-of-charge line and stats table describe |

**Outputs:** both revenues, a capture meter, average buy and sell prices, realized spread, **break-even sell
price** (avg buy ÷ RTE + degradation), wear cost, a naive annualized figure, and a written narrative.
The narrative gives the price range, which hours were traded, a verdict on the forecast, whether the
cycle limit is binding, and a scale check against the backtest average.

**Cycles below 1.0 are expected.** A full battery delivers E·√RTE ≈ 0.92 E to the grid, and cycles are
counted on the grid side, the same way as in the backtest.

## Tab 2: Backtest Results

- **Strategy table:** revenue, capture, 95% CI, paired daily gain vs S3 with its CI, a plain-language
  significance pill ("Yes, better / Not proven / Yes, worse"), cycles/day and τ. Click a row to toggle it on the chart.
- **Performance over time** (four views, with the RTC+B go-live marked by a dashed line):
  - *Cumulative revenue* ($M for 100 MW). The final values are listed under the chart.
  - *Cumulative regret*: money left on the table vs the oracle.
  - *Rolling 30-day capture*: Σ strategy / Σ oracle over a sliding window.
  - *Monthly capture*: from `capture_m`, with the oracle's $/day as bars on a second axis.
- **Forecast accuracy table:** MAE, RMSE, worst miss, τ, MAE before and after RTC+B, and the DM p-value. The best values are highlighted.

## Tab 3: Forecast & Leakage Audit

- **Ablation bar chart:** capture lost when each input group is removed. The placebo is red; losses over
  2 points are amber.
- **Ablation table:** includes the ≈ $/MW-yr impact (Δ capture × oracle revenue).
- **Placebo and cutoff boxes:** the two leakage arguments in words. The numbers come from `build_report.json`.
- **Publish-time sample:** each audited vintage with its margin before the cutoff, in minutes.

## Tab 4: Hypotheses

- **H2 scatter:** choose the X axis (τ, MAE, RMSE), the Y axis (capture or regret) and the regime. MAE,
  RMSE and regret use log axes (days with $0 regret are drawn at $1). The title shows Spearman ρ for the
  current selection. The table below shows Spearman *and* Pearson for τ, MAE and RMSE vs regret, so the
  caveat that the result flips in dollar terms is visible.
- **H3 concentration curve** (a Lorenz curve): days sorted by regret, cumulative share of total regret.
  Pick a strategy and a "worst X%" slider. The table gives total regret, the share from the worst X%,
  the 10 worst days, the top-decile oracle days, median daily regret, and days beating the oracle (0 =
  sanity check). Computed from `daily` as `ORACLE − strategy`.
- **H4 before/after chart and table** for 8 strategies, with the change in points and a
  seasonality caveat.
- **Value-of-forecast curve:** capture vs noise σ (log x-axis) plus the persistence-blend series (top
  axis). The slider interpolates the measured curve and converts it to $/MW-yr using HB_NORTH's
  1,030-day oracle.

## Tab 5: Hub Siting & Sizing

- **Chart:** Oracle, 7-day avg, persistence and fixed windows by hub, sorted by potential. It can switch
  to capture %.
- **Sortable table** (click the headers), including *best capture* and *left on table* = oracle − best realistic strategy.
- **Sizing table:** 2 h vs 4 h at HB_NORTH, per MW and per MWh. The 4 h run also used RTE 88% and $3 degradation, and the page says so.

## Glossary tab

All definitions in one list (the same source as the tooltips).

---

## Visual design choices

- **The theme was kept as it was:** paper colours (`--paper*`), brass accents, Georgia for prose and a
  monospace font for numbers and labels. New components (chips, meter, explain/takeaway boxes, findings
  cards) only use the existing CSS variables.
- **Stable strategy colours** across every chart and table (object `S`): GBM brass, ensemble purple,
  7-day avg green, persistence amber, oracle dark ink and dashed (it is a bound, not a real strategy).
- **Green = good / red = bad is used sparingly:** only for significance, losses and the leakage verdict.
- **Each section has explain-then-show-then-interpret ordering**, so someone new to the project is
  never shown a bare chart.
- **Phone-friendly:** grids stack below 900 px, tables scroll inside their box, and there is no horizontal page scroll at 390 px.
- **Works without the chart library:** if the CDN can't load, every chart is replaced by a message and
  tables and text still work.
