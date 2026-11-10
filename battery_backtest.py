#!/usr/bin/env python3
"""
battery_backtest.py - STEP 2 of the Texas Grid project: forecast -> dispatch -> revenue.

Needs only the day-ahead prices you already downloaded and validated
(data/raw/dam_spp_hubs/*.parquet with columns interval_start_utc, location, spp).

SETUP
    pip install numpy pandas pyarrow scipy matplotlib

COMMANDS
    python battery_backtest.py schema          # dump the columns of every raw dataset (paste it to me for step 3)
    python battery_backtest.py dp-check        # prove the LP is correct: compare with a from-scratch DP
    python battery_backtest.py backtest        # the headline experiment (one hub)
    python battery_backtest.py backtest --hub all           # siting comparison across hubs
    python battery_backtest.py backtest --duration 4 --rte 0.88 --degr 3   # sensitivity run
    python battery_backtest.py voc             # value-of-forecast curve (oracle + noise)

WHAT THE BACKTEST DOES
    For every local day D it decides a charge/discharge schedule the day before and settles it at the
    ACTUAL day-ahead price (price-taker). Only information from days before D is used to forecast.
        S0  no battery (zero reference)
        S1  fixed windows (charge around 11:00, discharge around 19:00)
        S2  yesterday's-profile rule (charge yesterday's cheapest hours, discharge its priciest)
        S3  optimizer fed with persistence forecast (same hour yesterday)
        S4  optimizer fed with weekly-seasonal-naive forecast (same hour last week)
        S5  optimizer fed with the 7-day same-hour average
        S6+ optimizer fed with YOUR forecasts: --extra-forecast name=path.parquet
            (columns: interval_start_utc, spp_fc; one row per hour of the target day, made before the cutoff)
        ORACLE  optimizer with perfect foresight = upper bound
    Every strategy is compared on identical days, so differences are paired.

SIMPLIFICATIONS TO STATE IN YOUR REPORT
    * Price-taker, hub prices (not nodal), day-ahead settlement only, no ancillary services.
    * Each day is self-contained: the battery starts and ends the day empty.
    * Efficiency split equally between charging and discharging; degradation = $/MWh discharged.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.stats import kendalltau

DATA_DIR = Path(os.environ.get("ERCOT_DATA_DIR", "data"))
RAW = DATA_DIR / "raw"
RESULTS = DATA_DIR / "results"
REPORTS = DATA_DIR / "reports"
TZ = ZoneInfo("America/Chicago")
RTCB_GO_LIVE = date(2025, 12, 5)  # ERCOT real-time co-optimization (structural break)
HUBS = ["HB_BUSAVG", "HB_HOUSTON", "HB_NORTH", "HB_PAN", "HB_SOUTH", "HB_WEST"]
FIXED_CHARGE_START, FIXED_DISCHARGE_START = 11, 19  # local hours for strategy S1


# ============================================================================
# 1. Battery model
# ============================================================================
@dataclass(frozen=True)
class Battery:
    power_mw: float = 100.0
    duration_h: float = 2.0
    rte: float = 0.85          # round-trip efficiency
    degr: float = 0.0          # $/MWh discharged
    cycles: float = 1.0        # max discharged energy per day, in multiples of capacity
    no_simul: bool = False     # forbid simultaneous charge+discharge (uses a MILP; slower)

    @property
    def energy(self) -> float:
        return self.power_mw * self.duration_h

    @property
    def eta(self) -> float:
        return math.sqrt(self.rte)


# ============================================================================
# 2. Data: day-ahead prices -> one object per local day (DST-safe)
# ============================================================================
@dataclass
class Day:
    d: date
    t_utc: pd.DatetimeIndex     # time-ordered hours of the local day (23/24/25 of them)
    hours: np.ndarray           # local hour-of-day label of each hour (0..23; 1 appears twice on fall-back day)
    prices: np.ndarray          # actual day-ahead prices


def expected_hours(d: date) -> int:
    a = pd.Timestamp(d.isoformat(), tz=TZ)
    b = pd.Timestamp((d + timedelta(days=1)).isoformat(), tz=TZ)
    return int(round((b - a).total_seconds() / 3600))


def _read_folder(folder: Path, columns: list[str] | None = None) -> pd.DataFrame:
    files = sorted(list(folder.glob("*.parquet")) + list(folder.glob("*.csv.gz")))
    if not files:
        sys.exit(f"No files in {folder}. Run the data pipeline first.")
    parts = []
    for f in files:
        parts.append(pd.read_parquet(f, columns=columns) if f.suffix == ".parquet" else pd.read_csv(f, usecols=columns))
    return pd.concat(parts, ignore_index=True)


def load_days(hub: str) -> dict[date, Day]:
    df = _read_folder(RAW / "dam_spp_hubs", ["interval_start_utc", "location", "spp"])
    df = df[df["location"] == hub].copy()
    if df.empty:
        sys.exit(f"Hub {hub} not found. Available: {sorted(_read_folder(RAW / 'dam_spp_hubs', ['location'])['location'].unique())}")
    df["t"] = pd.to_datetime(df["interval_start_utc"], utc=True)
    df = df.drop_duplicates("t", keep="last").sort_values("t")
    loc = df["t"].dt.tz_convert(TZ)
    df["local_date"], df["hour"] = loc.dt.date, loc.dt.hour
    days = {}
    for d, g in df.groupby("local_date"):
        days[d] = Day(d, pd.DatetimeIndex(g["t"]), g["hour"].to_numpy(), g["spp"].to_numpy(dtype=float))
    return days


def is_valid(days: dict, d: date) -> bool:
    x = days.get(d)
    return x is not None and len(x.prices) == expected_hours(d) and not np.isnan(x.prices).any()


def by_label(day: Day) -> np.ndarray:
    """24-slot array by local hour label (first occurrence on the repeated hour; gaps interpolated)."""
    out = np.full(24, np.nan)
    for h, p in zip(day.hours, day.prices):
        if np.isnan(out[h]):
            out[h] = p
    return pd.Series(out).interpolate(limit_direction="both").to_numpy()


def forecast_from_lags(days: dict, d: date, lags: list[int]) -> np.ndarray | None:
    """Forecast for each hour of day d = mean of the same local-hour label on d-lag days."""
    src = []
    for k in lags:
        p = d - timedelta(days=k)
        if not is_valid(days, p):
            return None
        src.append(by_label(days[p]))
    mean = np.mean(src, axis=0)
    return mean[days[d].hours]


# ============================================================================
# 3. Optimizer (LP / MILP) and from-scratch DP
# ============================================================================
def solve_day(price_fc: np.ndarray, bat: Battery) -> tuple[np.ndarray, np.ndarray]:
    """Maximize forecast profit for one day. Returns (charge MW, discharge MW) per hour."""
    T, P, E, eta = len(price_fc), bat.power_mw, bat.energy, bat.eta
    L = np.tril(np.ones((T, T)))
    soc_c, soc_d = L * eta, -L / eta                       # SoC_t = sum_{s<=t} (eta*c_s - d_s/eta)
    cost = np.concatenate([price_fc, -price_fc + bat.degr])  # minimize cost = -profit
    if not bat.no_simul:
        A = np.vstack([np.hstack([soc_c, soc_d]), -np.hstack([soc_c, soc_d]),
                       np.concatenate([np.zeros(T), np.ones(T)])[None, :]])
        b = np.concatenate([np.full(T, E), np.zeros(T), [bat.cycles * E]])
        A_eq, b_eq = np.concatenate([soc_c[-1], soc_d[-1]])[None, :], [0.0]
        res = linprog(cost, A_ub=A, b_ub=b, A_eq=A_eq, b_eq=b_eq, bounds=[(0, P)] * (2 * T), method="highs")
        if res.status != 0:
            raise RuntimeError(f"LP failed: {res.message}")
        return res.x[:T], res.x[T:]
    # MILP with a mode binary u_t (1 = charging)
    n = 3 * T
    cc = np.concatenate([cost, np.zeros(T)])
    soc = np.hstack([soc_c, soc_d, np.zeros((T, T))])
    cons = [LinearConstraint(soc, -np.inf, E), LinearConstraint(-soc, -np.inf, 0.0),
            LinearConstraint(soc[-1][None, :], 0.0, 0.0),
            LinearConstraint(np.concatenate([np.zeros(T), np.ones(T), np.zeros(T)])[None, :], -np.inf, bat.cycles * E),
            LinearConstraint(np.hstack([np.eye(T), np.zeros((T, T)), -P * np.eye(T)]), -np.inf, 0.0),
            LinearConstraint(np.hstack([np.zeros((T, T)), np.eye(T), P * np.eye(T)]), -np.inf, P)]
    res = milp(cc, integrality=np.concatenate([np.zeros(2 * T), np.ones(T)]),
               bounds=Bounds(np.zeros(n), np.concatenate([np.full(2 * T, P), np.ones(T)])), constraints=cons)
    if res.x is None:
        raise RuntimeError(f"MILP failed: {res.message}")
    return res.x[:T], res.x[T:2 * T]


def dp_value(prices: np.ndarray, bat: Battery, n_levels: int = 100) -> float:
    """Same problem solved by backward induction over a discretized state of charge (no LP involved)."""
    T, P, E, eta = len(prices), bat.power_mw, bat.energy, bat.eta
    step = E / n_levels
    soc = np.arange(n_levels + 1) * step
    delta = soc[None, :] - soc[:, None]            # battery-energy change going from state i to state j
    charge_grid = np.where(delta > 0, delta / eta, 0.0)    # MWh drawn from the grid (1 hour => MW)
    discharge_out = np.where(delta < 0, -delta * eta, 0.0)  # MWh delivered to the grid
    feasible = (charge_grid <= P + 1e-9) & (discharge_out <= P + 1e-9)
    cycle_ok = True  # cycle cap is not modeled in the DP: only compare when the cap is not binding
    V = np.full(n_levels + 1, -np.inf)
    V[0] = 0.0
    for t in range(T - 1, -1, -1):
        reward = prices[t] * (discharge_out - charge_grid) - bat.degr * discharge_out
        cand = np.where(feasible, reward + V[None, :], -np.inf)
        V = cand.max(axis=1)
    return float(V[0]) if cycle_ok else float("nan")


def settle(c: np.ndarray, d: np.ndarray, actual: np.ndarray, bat: Battery) -> float:
    return float(np.sum(actual * (d - c)) - bat.degr * np.sum(d))


def run_rule(charge_idx: set, discharge_idx: set, T: int, bat: Battery) -> tuple[np.ndarray, np.ndarray]:
    soc, c, d = 0.0, np.zeros(T), np.zeros(T)
    for t in range(T):
        if t in charge_idx:
            c[t] = min(bat.power_mw, (bat.energy - soc) / bat.eta)
            soc += bat.eta * c[t]
        elif t in discharge_idx:
            d[t] = min(bat.power_mw, soc * bat.eta)
            soc -= d[t] / bat.eta
    return c, d


# ============================================================================
# 4. Backtest
# ============================================================================
def valid_days_for_all(days: dict, extra: dict, start: date | None, end: date | None) -> list[date]:
    out = []
    for d in sorted(days):
        if (start and d < start) or (end and d >= end) or not is_valid(days, d):
            continue
        if any(forecast_from_lags(days, d, lg) is None for lg in ([1], [7], list(range(1, 8)))):
            continue
        if any(d not in fc for fc in extra.values()):
            continue
        out.append(d)
    return out


def load_extra(specs: list[str], days: dict, hub: str) -> dict[str, dict[date, np.ndarray]]:
    out = {}
    for s in specs or []:
        name, path = s.split("=", 1)
        f = pd.read_parquet(path)
        if "location" in f.columns:
            f = f[f["location"] == hub]
        f["t"] = pd.to_datetime(f["interval_start_utc"], utc=True)
        series = f.drop_duplicates("t", keep="last").set_index("t")["spp_fc"]
        per_day = {}
        for d, day in days.items():
            if day.t_utc.isin(series.index).all():
                per_day[d] = series.reindex(day.t_utc).to_numpy(dtype=float)
        out[name] = per_day
        print(f"  extra forecast '{name}': covers {len(per_day)} days")
    return out


def block_bootstrap(arrs: list[np.ndarray], stat, block: int = 7, n: int = 2000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    N = len(arrs[0])
    nb = math.ceil(N / block)
    out = np.empty(n)
    for i in range(n):
        starts = rng.integers(0, max(1, N - block + 1), nb)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:N]
        out[i] = stat(*[a[idx] for a in arrs])
    return out


def run_backtest(days: dict, bat: Battery, extra: dict, start=None, end=None) -> tuple[pd.DataFrame, dict]:
    dates = valid_days_for_all(days, extra, start, end)
    if len(dates) < 30:
        sys.exit(f"Only {len(dates)} usable days. Check your data window.")
    rows, taus = [], {}
    names = ["S0_no_battery", "S1_fixed_windows", "S2_yesterday_rule", "S3_persistence_opt", "S4_weekly_naive_opt",
             "S5_avg7_opt"] + [f"S6_{n}_opt" for n in extra] + ["ORACLE"]
    tau_acc = {n: [] for n in names}
    n_h = max(1, math.ceil(bat.duration_h))
    simul_days = 0
    for d in dates:
        day = days[d]
        T, actual = len(day.prices), day.prices
        rec = {"date": d}
        rec["S0_no_battery"] = 0.0
        rec["cycles_S0_no_battery"] = 0.0
        # S1 fixed windows (by local hour label)
        ch = {i for i, h in enumerate(day.hours) if FIXED_CHARGE_START <= h < FIXED_CHARGE_START + n_h}
        di = {i for i, h in enumerate(day.hours) if FIXED_DISCHARGE_START <= h < FIXED_DISCHARGE_START + n_h}
        c, dd = run_rule(ch, di, T, bat)
        rec["S1_fixed_windows"], rec["cycles_S1_fixed_windows"] = settle(c, dd, actual, bat), dd.sum() / bat.energy
        # S2 yesterday's profile
        yl = by_label(days[d - timedelta(days=1)])
        cheap, dear = set(np.argsort(yl)[:n_h]), set(np.argsort(yl)[-n_h:])
        ch = {i for i, h in enumerate(day.hours) if h in cheap}
        di = {i for i, h in enumerate(day.hours) if h in dear}
        c, dd = run_rule(ch, di, T, bat)
        rec["S2_yesterday_rule"], rec["cycles_S2_yesterday_rule"] = settle(c, dd, actual, bat), dd.sum() / bat.energy
        # optimizer strategies
        fcs = {"S3_persistence_opt": forecast_from_lags(days, d, [1]), "S4_weekly_naive_opt": forecast_from_lags(days, d, [7]),
               "S5_avg7_opt": forecast_from_lags(days, d, list(range(1, 8)))}
        for n in extra:
            fcs[f"S6_{n}_opt"] = extra[n][d]
        fcs["ORACLE"] = actual
        for name, fc in fcs.items():
            c, dd = solve_day(fc, bat)
            if (np.minimum(c, dd) > 1e-6).any():
                simul_days += 1
            rec[name], rec[f"cycles_{name}"] = settle(c, dd, actual, bat), dd.sum() / bat.energy
            if name != "ORACLE" and np.std(fc) > 0:
                tau_acc[name].append(kendalltau(fc, actual)[0])
        rows.append(rec)
    out = pd.DataFrame(rows)
    out["regime"] = np.where(out["date"] >= RTCB_GO_LIVE, "post_RTCB", "pre_RTCB")
    meta = dict(days_used=len(dates), first=str(dates[0]), last=str(dates[-1]), simultaneous_charge_discharge_days=simul_days,
                days_in_data=len(days), kendall_tau={k: float(np.nanmean(v)) for k, v in tau_acc.items() if v})
    return out, meta


def summarize(res: pd.DataFrame, bat: Battery, meta: dict, hub: str) -> pd.DataFrame:
    strategies = [c for c in res.columns if c.startswith("S") and not c.startswith("cycles") and c != "regime"] + ["ORACLE"]
    oracle = res["ORACLE"].to_numpy()
    rows = []
    for s in strategies:
        x = res[s].to_numpy()
        ci = block_bootstrap([x, oracle], lambda a, o: a.sum() / o.sum())
        pre, post = res["regime"] == "pre_RTCB", res["regime"] == "post_RTCB"
        years = len(res) / 365.0
        rows.append(dict(
            hub=hub, strategy=s, days=len(res),
            revenue_per_mw_year=x.sum() / bat.power_mw / years,
            capture_of_oracle=x.sum() / oracle.sum(), capture_ci_low=np.percentile(ci, 2.5), capture_ci_high=np.percentile(ci, 97.5),
            capture_pre_rtcb=x[pre].sum() / oracle[pre].sum() if pre.any() else np.nan,
            capture_post_rtcb=x[post].sum() / oracle[post].sum() if post.any() else np.nan,
            mean_daily_revenue=x.mean(), worst5pct_day_avg=np.sort(x)[: max(1, len(x) // 20)].mean(),
            top10_days_share=np.sort(x)[-10:].sum() / x.sum() if x.sum() > 0 else np.nan,
            cycles_per_day=res.get(f"cycles_{s}", pd.Series([0])).mean(),
            kendall_tau=meta["kendall_tau"].get(s, np.nan)))
    return pd.DataFrame(rows)


def paired_vs(res: pd.DataFrame, ref: str = "S3_persistence_opt") -> pd.DataFrame:
    out, r = [], res[ref].to_numpy()
    for s in [c for c in res.columns if c.startswith("S") and not c.startswith("cycles") and c != ref] + ["ORACLE"]:
        diff = res[s].to_numpy() - r
        bs = block_bootstrap([diff], lambda x: x.mean())
        out.append(dict(strategy=s, reference=ref, mean_daily_gain=diff.mean(), ci_low=np.percentile(bs, 2.5),
                        ci_high=np.percentile(bs, 97.5), significant=bool(np.percentile(bs, 2.5) > 0 or np.percentile(bs, 97.5) < 0)))
    return pd.DataFrame(out)


def make_plots(res: pd.DataFrame, summ: pd.DataFrame, tag: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: skipping figures (pip install matplotlib)")
        return
    RESULTS.mkdir(parents=True, exist_ok=True)
    strategies = [c for c in res.columns if c.startswith("S") and not c.startswith("cycles") and c != "regime"] + ["ORACLE"]
    dates = pd.to_datetime(res["date"])
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for s in strategies:
        ax.plot(dates, res[s].cumsum() / 1e6, label=s, lw=2.2 if s == "ORACLE" else 1.4, ls="--" if s == "ORACLE" else "-")
    ax.axvline(pd.Timestamp(RTCB_GO_LIVE), color="gray", ls=":", lw=1)
    ax.text(pd.Timestamp(RTCB_GO_LIVE), ax.get_ylim()[1] * 0.97, " RTC+B", color="gray", va="top")
    ax.set_ylabel("Cumulative revenue ($ million)")
    ax.set_title(f"Forecast-driven battery dispatch ({tag})")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / f"fig_cumulative_revenue_{tag}.png", dpi=160)
    plt.close(fig)
    s2 = summ[~summ["strategy"].isin(["S0_no_battery", "ORACLE"])]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    y = np.arange(len(s2))
    err = np.vstack([s2["capture_of_oracle"] - s2["capture_ci_low"], s2["capture_ci_high"] - s2["capture_of_oracle"]])
    ax.barh(y, s2["capture_of_oracle"] * 100, xerr=err * 100, color="#4c78a8")
    ax.set_yticks(y)
    ax.set_yticklabels(s2["strategy"])
    ax.set_xlabel("% of perfect-foresight revenue (95% block-bootstrap CI)")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / f"fig_capture_rate_{tag}.png", dpi=160)
    plt.close(fig)


def common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hub", default="HB_NORTH", help="hub name or 'all'")
    p.add_argument("--power", type=float, default=100.0, help="MW")
    p.add_argument("--duration", type=float, default=2.0, help="hours of storage")
    p.add_argument("--rte", type=float, default=0.85)
    p.add_argument("--degr", type=float, default=0.0, help="$/MWh discharged")
    p.add_argument("--cycles", type=float, default=1.0, help="max discharge per day in capacities")
    p.add_argument("--no-simul", action="store_true", help="forbid simultaneous charge+discharge (MILP, slower)")
    p.add_argument("--start", default=None, help="first local date YYYY-MM-DD")
    p.add_argument("--end", default=None, help="last local date, exclusive")


def battery_from(a) -> Battery:
    return Battery(a.power, a.duration, a.rte, a.degr, a.cycles, a.no_simul)


def d_or_none(s):
    return date.fromisoformat(s) if s else None


# ============================================================================
# 5. Commands
# ============================================================================
def cmd_backtest(a) -> None:
    bat = battery_from(a)
    hubs = HUBS if a.hub == "all" else [a.hub]
    RESULTS.mkdir(parents=True, exist_ok=True)
    all_sum = []
    for hub in hubs:
        print(f"\n===== {hub} | {bat.power_mw:.0f} MW x {bat.duration_h:g} h | RTE {bat.rte:.0%} | degr ${bat.degr}/MWh =====")
        days = load_days(hub)
        extra = load_extra(a.extra_forecast, days, hub)
        res, meta = run_backtest(days, bat, extra, d_or_none(a.start), d_or_none(a.end))
        tag = f"{hub}_{bat.duration_h:g}h_rte{int(bat.rte * 100)}_degr{a.degr:g}"
        summ = summarize(res, bat, meta, hub)
        res.to_parquet(RESULTS / f"daily_revenue_{tag}.parquet", index=False)
        summ.to_csv(RESULTS / f"summary_{tag}.csv", index=False)
        paired = paired_vs(res)
        paired.to_csv(RESULTS / f"paired_vs_persistence_{tag}.csv", index=False)
        make_plots(res, summ, tag)
        print(f"days used: {meta['days_used']} ({meta['first']} -> {meta['last']}) of {meta['days_in_data']} days in data")
        if meta["simultaneous_charge_discharge_days"]:
            print(f"NOTE: {meta['simultaneous_charge_discharge_days']} optimizer solves used simultaneous charge+discharge "
                  "(happens with negative prices). Re-run with --no-simul to forbid it and compare.")
        show = summ[["strategy", "revenue_per_mw_year", "capture_of_oracle", "capture_ci_low", "capture_ci_high",
                     "capture_pre_rtcb", "capture_post_rtcb", "cycles_per_day", "top10_days_share", "kendall_tau"]]
        print(show.round(3).to_string(index=False))
        print("\nPaired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):")
        print(paired.round(1).to_string(index=False))
        all_sum.append(summ)
    if len(all_sum) > 1:
        both = pd.concat(all_sum)
        both.to_csv(RESULTS / "summary_all_hubs.csv", index=False)
        print("\n== Siting comparison: revenue per MW-year by hub ==")
        print(both.pivot(index="hub", columns="strategy", values="revenue_per_mw_year").round(0).to_string())
    print(f"\nFiles written to {RESULTS}")


def cmd_dp_check(a) -> None:
    bat = Battery(a.power, a.duration, a.rte, a.degr, cycles=99.0)  # cap not binding: DP does not model it
    days = load_days(a.hub if a.hub != "all" else "HB_NORTH")
    ok = [d for d in sorted(days) if is_valid(days, d)]
    rng = np.random.default_rng(0)
    sample = rng.choice(len(ok), size=min(40, len(ok)), replace=False)
    diffs = []
    for i in sample:
        p = days[ok[i]].prices
        c, d = solve_day(p, bat)
        lp, dp = settle(c, d, p, bat), dp_value(p, bat, n_levels=a.levels)
        diffs.append((lp - dp) / max(1.0, abs(lp)))
    diffs = np.array(diffs)
    print(f"LP vs DP on {len(diffs)} random days (grid of {a.levels} SoC levels):")
    print(f"  relative gap LP-DP: mean {diffs.mean():.4%}, max {diffs.max():.4%}, min {diffs.min():.4%}")
    print("  LP should be >= DP (continuous vs discretized) and within a small discretization error.")
    print("  PASS" if diffs.min() > -1e-6 and diffs.max() < 0.03 else "  CHECK: gap larger than expected")


def cmd_voc(a) -> None:
    bat = battery_from(a)
    days = load_days(a.hub)
    dates = valid_days_for_all(days, {}, d_or_none(a.start), d_or_none(a.end))
    rng = np.random.default_rng(0)
    ref = {}
    for name, lag in (("persistence", [1]), ("weekly_naive", [7]), ("avg7", list(range(1, 8)))):
        tot = 0.0
        for d in dates:
            c, dd = solve_day(forecast_from_lags(days, d, lag), bat)
            tot += settle(c, dd, days[d].prices, bat)
        ref[name] = tot
    oracle = sum(settle(*solve_day(days[d].prices, bat), days[d].prices, bat) for d in dates)
    rows = []
    for sigma in (0, 2, 5, 10, 20, 40, 80, 160):
        tot = 0.0
        for d in dates:
            act = days[d].prices
            fc = act + rng.normal(0, sigma, len(act))
            c, dd = solve_day(fc, bat)
            tot += settle(c, dd, act, bat)
        rows.append(dict(noise_sigma=sigma, capture=tot / oracle))
    for alpha in (0.25, 0.5, 0.75, 1.0):
        tot = 0.0
        for d in dates:
            act = days[d].prices
            fc = (1 - alpha) * act + alpha * forecast_from_lags(days, d, [1])
            c, dd = solve_day(fc, bat)
            tot += settle(c, dd, act, bat)
        rows.append(dict(noise_sigma=f"blend_persistence_{alpha}", capture=tot / oracle))
    out = pd.DataFrame(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.to_csv(RESULTS / f"value_of_forecast_{a.hub}.csv", index=False)
    print(out.round(4).to_string(index=False))
    print("\nReference captures:", {k: round(v / oracle, 4) for k, v in ref.items()})
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        g = out[out["noise_sigma"].map(lambda v: isinstance(v, (int, float)))]
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.plot(g["noise_sigma"], g["capture"] * 100, "o-")
        for k, v in ref.items():
            ax.axhline(v / oracle * 100, ls="--", lw=1, label=k)
        ax.set_xlabel("Forecast noise sigma ($/MWh) added to the true price")
        ax.set_ylabel("% of perfect-foresight revenue")
        ax.set_title("Value of forecast: how much accuracy is worth")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(RESULTS / f"fig_value_of_forecast_{a.hub}.png", dpi=160)
        print("Figure saved.")
    except ImportError:
        pass


def cmd_schema(a) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = []
    for folder in sorted(p for p in RAW.rglob("*") if p.is_dir() and any(p.glob("*.parquet"))):
        f = sorted(folder.glob("*.parquet"))[0]
        df = pd.read_parquet(f)
        lines.append(f"\n=== {folder.relative_to(RAW)}  ({len(list(folder.glob('*.parquet')))} files, first: {f.name}) ===")
        for c in df.columns:
            extra = ""
            if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c])) and df[c].nunique() <= 12:
                extra = f"  values: {sorted(df[c].dropna().unique().tolist())}"
            lines.append(f"  {c:<45} {str(df[c].dtype):<22}{extra}")
        lines.append("  sample rows:")
        lines.append("    " + df.head(2).to_string(index=False).replace("\n", "\n    "))
    text = "\n".join(lines)
    (REPORTS / "schema.txt").write_text(text)
    print(text)
    print(f"\nSaved to {REPORTS/'schema.txt'}. Paste this file's contents to Claude for step 3 (feature building).")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Battery dispatch backtest for the Texas Grid project")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    common_args(b)
    b.add_argument("--extra-forecast", action="append", help="name=path.parquet with interval_start_utc, spp_fc")
    b.set_defaults(fn=cmd_backtest)
    d = sub.add_parser("dp-check")
    common_args(d)
    d.add_argument("--levels", type=int, default=100)
    d.set_defaults(fn=cmd_dp_check)
    v = sub.add_parser("voc")
    common_args(v)
    v.set_defaults(fn=cmd_voc)
    s = sub.add_parser("schema")
    s.set_defaults(fn=cmd_schema)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
