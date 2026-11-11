# Design decisions

Each entry gives: **the decision → why → the alternative and why it was rejected.**

## Scope

**Day-ahead energy arbitrage only.** One market with one daily decision, so it is clean to backtest and
every dollar is traceable. *Alternatives:* adding the real-time market or ancillary services (AS) would
be more realistic. In Texas, AS is a large share of real battery revenue. But it needs real-time bidding
logic and an AS capacity model, and it would make "how much is a better price forecast worth?"
impossible to isolate. It is listed as future work.

**Price-taker at hub prices.** One 100 MW battery barely moves a ~70 GW market, and hub prices are
public and liquid. *Alternative:* nodal prices + market impact would need private node data and an
equilibrium model.

**100 MW × 2 h, RTE 85%, ≤1 cycle/day as the base case.** This is a typical ERCOT battery in 2024–26.
The 1-cycle limit reflects the warranty limits operators actually face. The 4 h run is a sensitivity case.

## Data collection

**Forecasts are downloaded "as of" the cutoff, not filtered afterwards.** Asking the API for the latest
vintage before D-1 09:45 means the file on disk *is* what a trader could see. Filtering a "latest"
download afterwards is impossible, because the earlier vintages are gone. The build step re-checks the
rule anyway (defence in depth).

**Cutoff at 09:45, not 10:00.** A 15-minute buffer for bid preparation and posting delays. The
load forecast is published at 09:30, so the margin is real but thin (see the review doc).

**48 h-lead weather only.** 24 h-lead forecasts are issued after the cutoff for afternoon and evening
hours. They are kept under an explicit `wxd1unsafe_` prefix so the leak can be quantified rather than
silently avoided.

**Raw files are never edited. One file per date range. Everything is stored in UTC.** This makes
downloads resumable and idempotent, and any result can be traced back to a file. UTC avoids DST
ambiguity in storage. Local time is derived only where the market logic needs it (local days, clock
hours, cutoffs).

**Hard row/request budgets.** The free API plan has monthly caps. Overrunning them mid-download would
leave partial months. Deferring chunks and resuming next period is safer.

**No interpolation of prices or forecasts.** Interpolating a missing spike hour would invent the most
valuable data point of the day. Gaps stay NaN, LightGBM handles them natively, and invalid days are
excluded from the backtest.

## Features and forecasting

**A prefix whitelist for features plus automated leakage tests.** Every new column must declare where it
came from. A misnamed column, or an actual-generation column slipping in, stops the build instead of
quietly inflating results.

**Target = asinh(price/50).** Texas prices range from −$50 to $5,000+. Squared-error training on raw
prices would be dominated by a handful of spike hours. A log transform can't handle negative prices;
asinh can. The scale of 50 makes it roughly linear in the normal $0–100 range.

**Two contrasting model families.** GBM (non-linear, handles NaN, captures "low wind × high load ⇒
spike") and an hourly Lasso (LEAR, a standard benchmark in the price-forecasting literature,
interpretable). If both agree on the inputs that matter, the finding is robust. The ensemble tests
whether averaging helps. Here it didn't, because the Lasso is clearly weaker.

**Fixed, untuned GBM hyperparameters.** Tuning on the test period would leak, and doing it properly
needs nested walk-forward validation (expensive). The defaults are conservative (small learning rate,
subsampling, L2). The reported result is therefore a *lower* bound on what tuning could give.

**Lasso alpha tuned once on the first window.** Re-tuning every week is slow and makes the model's
behaviour change from block to block. Tuning once is honest (it only uses past data) and stable.

**Expanding walk-forward window, retrain every 7 days.** This mimics a live desk retraining weekly. An
expanding window keeps rare events (winter storms) in memory. *Alternative:* a rolling window adapts
faster to regime changes such as RTC+B, at the cost of forgetting spikes. That would be worth testing.

**Ablation and placebo measured in battery dollars, not MAE.** The project's claim is about money. Some
ablations *lower* MAE but cost revenue, which is H2 seen from another angle.

## Optimisation and evaluation

**An LP, not a heuristic or an RL agent.** With a fixed price vector, day-ahead dispatch is a convex LP:
the exact optimum in milliseconds, with no training. All differences between strategies therefore come
from the **forecast**, not from the dispatcher. The DP cross-check proves the LP formulation is right.

**Each day stands alone (start and end empty).** It keeps days independent, which makes the paired
statistics valid, and matches the daily bid. *Cost:* it slightly undervalues carrying charge overnight
into a dear morning.

**Settle at actual prices.** This is the definition of a backtest. A schedule's value under the forecast is meaningless.

**Oracle = the denominator.** "93% of the best possible" is comparable across hubs, periods and battery
sizes in a way that raw dollars aren't. The capture rate is a **ratio of sums**, so big days count more.
That matches the owner's objective. A mean of daily ratios would let quiet $50 days count as much as
$80k days.

**Identical days for every strategy plus paired tests.** Comparing on different day sets would confuse
skill with luck in the calendar. Pairing removes the common day-to-day noise, which is huge, and makes
small differences detectable.

**Block bootstrap (7-day blocks).** Daily revenues are autocorrelated (heat waves last a week). An
i.i.d. bootstrap would understate uncertainty and declare spurious significance.

**S3 persistence as the reference.** It is the simplest *optimised* strategy. "Is the ML worth it?" is
really "does it beat the free forecast?" Comparing against fixed windows would flatter every model.

**Diebold–Mariano with Newey–West variance.** This is the standard test for comparing forecast accuracy
on autocorrelated errors.

**RTC+B treated as a structural break.** It was a major market redesign, so every result is also
reported before and after it. A calendar-feature flag (`cal_post_rtcb`) lets the models adapt.

## Dashboard

**A static single file + `data.js`.** It opens with a double-click, needs no server, and can be hosted
anywhere. JSON loaded with `fetch()` fails from `file://`, a script tag doesn't.

**Everything computed from data in the browser.** Previously many figures were typed into the HTML and
could silently drift from the results. Now regenerating `data.js` updates every number and sentence.

**An in-browser DP instead of the LP.** There is no JS LP solver dependency. An 80-level DP is exact up
to discretisation, and the Lagrangian bisection enforces the cycle limit without a second state dimension.

**Explain → show → interpret on every panel,** plus a glossary with hover terms, so a reader new to
power markets can follow it without the report.

**The original theme was preserved** at the owner's request. See [05_dashboard.md](05_dashboard.md#visual-design-choices).
