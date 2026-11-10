#!/usr/bin/env python3
"""
analysis_report.py - STEP 4 of the Texas Grid project: audit + the analyses your report needs.

Run AFTER:  forecast_pipeline.py train  and  battery_backtest.py backtest --extra-forecast ...

    python analysis_report.py audit                       # manual-check printout of forecast publish times
    python analysis_report.py gap  --daily data/results/daily_revenue_HB_NORTH_2h_rte85_degr0.parquet
    python analysis_report.py rank --daily <same file> \
        --forecast gbm=data/processed/forecast_gbm_HB_NORTH.parquet \
        --forecast ens=data/processed/forecast_ens_HB_NORTH.parquet
    python analysis_report.py figs --daily <same file> [--forecast ...]

WHAT EACH ONE ANSWERS
  audit  Were the load/wind/solar forecasts really public before the day-ahead cutoff? Prints raw publish times
         for random days so you can verify them by eye against ERCOT's report schedule (a placebo cannot catch a
         wrongly TIMED file; this can).
  gap    Where does the remaining distance to the oracle come from? (H3: a few spike days?) Regret by regime
         and by month, with pre/post RTC+B split (H4).
  rank   H2: does a rank metric (daily Kendall tau) predict daily revenue regret better than MAE/RMSE?
  figs   Report figures: cumulative regret, monthly capture, tau-vs-regret scatter.

Strategy columns in the daily file are  S0..S5, S6_<name>_opt (your models) and ORACLE.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

DATA_DIR = Path(os.environ.get("ERCOT_DATA_DIR", "data"))
RAW, RESULTS, REPORTS = DATA_DIR / "raw", DATA_DIR / "results", DATA_DIR / "reports"
TZ = ZoneInfo("America/Chicago")
CUTOFF_LOCAL, CUTOFF_DAYS_BEFORE = "09:45:00", 1
RTCB = pd.Timestamp("2025-12-05")


# ---------------------------------------------------------------- helpers
def load_daily(path: str):
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    strategies = [c for c in df.columns if re.match(r"S\d", c)] + ["ORACLE"]
    return df.sort_values("date").reset_index(drop=True), strategies


def models_of(strategies):
    return [s for s in strategies if s.startswith("S6_")]


def read_raw(key: str, cols=None) -> pd.DataFrame:
    files = sorted((RAW / key).glob("*.parquet"))
    if not files:
        sys.exit(f"No raw files for {key}")
    df = pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True)
    for c in df.columns:
        if c.endswith("_utc") and not c.startswith("_"):
            df[c] = pd.to_datetime(df[c], utc=True)
    return df


def cutoff_for(start_utc: pd.Series) -> pd.Series:
    hh, mm, ss = (int(x) for x in CUTOFF_LOCAL.split(":"))
    day = start_utc.dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()
    naive = day - pd.Timedelta(days=CUTOFF_DAYS_BEFORE) + pd.Timedelta(hours=hh, minutes=mm, seconds=ss)
    return naive.dt.tz_localize(TZ, ambiguous=True, nonexistent="shift_forward").dt.tz_convert("UTC")


# ---------------------------------------------------------------- audit
def cmd_audit(a) -> None:
    rng = np.random.default_rng(a.seed)
    out = []
    for key in ("load_forecast_asof", "wind_forecast_asof", "solar_forecast_asof"):
        df = read_raw(key, ["interval_start_utc", "publish_time_utc"])
        df["cutoff"] = cutoff_for(df["interval_start_utc"])
        df["target_local_date"] = df["interval_start_utc"].dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()
        late = df["publish_time_utc"] > df["cutoff"]
        print(f"\n=== {key}: {len(df):,} rows ===")
        print(f"  rows published AFTER the {CUTOFF_LOCAL} US-Central D-{CUTOFF_DAYS_BEFORE} cutoff: {int(late.sum()):,} ({late.mean():.3%})")
        pl = df["publish_time_utc"].dt.tz_convert(TZ)
        clock = pl.dt.hour + pl.dt.minute / 60
        print(f"  publish clock time (US Central): min {clock.min():.2f}h  median {clock.median():.2f}h  max {clock.max():.2f}h")
        lag_days = (df["target_local_date"] - pl.dt.tz_localize(None).dt.normalize()).dt.days
        print("  publish date relative to target date (days before): " + str(lag_days.value_counts().sort_index().head(6).to_dict()))
        # latest publication before the cutoff, per target hour: this is what the model actually saw
        used = df[~late].sort_values("publish_time_utc").drop_duplicates("interval_start_utc", keep="last")
        hours_before = (used["cutoff"] - used["publish_time_utc"]).dt.total_seconds() / 3600
        print(f"  rows actually used: {len(used):,}; hours between publication and cutoff: median {hours_before.median():.1f}, "
              f"min {hours_before.min():.2f}, max {hours_before.max():.1f}")
        stale = used[hours_before > 48]
        print(f"  used rows published more than 48h before the cutoff (possible stale vintage): {len(stale):,}")
        days = used["target_local_date"].drop_duplicates()
        pick = days.iloc[rng.choice(len(days), size=min(a.n, len(days)), replace=False)].sort_values()
        for d in pick:
            r = used[used["target_local_date"] == d].iloc[0]
            out.append(dict(dataset=key, target_day=d.date(),
                            publish_local=r["publish_time_utc"].tz_convert(TZ).strftime("%Y-%m-%d %H:%M"),
                            cutoff_local=r["cutoff"].tz_convert(TZ).strftime("%Y-%m-%d %H:%M"),
                            hours_before_cutoff=round((r["cutoff"] - r["publish_time_utc"]).total_seconds() / 3600, 2)))
    smp = pd.DataFrame(out)
    REPORTS.mkdir(parents=True, exist_ok=True)
    smp.to_csv(REPORTS / "publish_time_audit.csv", index=False)
    print("\n=== random days to verify by hand ===")
    print(smp.to_string(index=False))
    print("\nCHECK: for each dataset the publish time should fall on the day BEFORE the target day, before 09:45 local. "
          "Compare two or three rows with ERCOT's report posting times (the load forecast is posted in the early "
          "morning; wind/solar forecasts hourly). A publish time on the target day itself would be a leak.")
    print(f"Saved {REPORTS/'publish_time_audit.csv'}")


# ---------------------------------------------------------------- gap
def cmd_gap(a) -> None:
    df, strats = load_daily(a.daily)
    orc = df["ORACLE"]
    post = df["date"] >= RTCB
    rows = []
    for s in strats:
        if s in ("ORACLE", "S0_no_battery"):
            continue
        reg = orc - df[s]
        tot = reg.sum()
        top_orc = df.assign(r=reg).nlargest(10, "ORACLE")["r"].sum()
        dec = orc >= orc.quantile(0.9)
        rows.append(dict(
            strategy=s, capture=df[s].sum() / orc.sum(), total_regret_usd=tot,
            share_of_regret_from_10_worst_days=reg.nlargest(10).sum() / tot if tot > 0 else np.nan,
            share_of_regret_on_10_best_oracle_days=top_orc / tot if tot > 0 else np.nan,
            share_of_regret_on_top_decile_oracle_days=reg[dec].sum() / tot if tot > 0 else np.nan,
            share_of_days_in_top_decile=float(dec.mean()),
            capture_pre_rtcb=df.loc[~post, s].sum() / orc[~post].sum() if (~post).any() else np.nan,
            capture_post_rtcb=df.loc[post, s].sum() / orc[post].sum() if post.any() else np.nan,
            days_beating_oracle=int(((df[s] - orc) > 1e-6).sum())))
    t = pd.DataFrame(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    t.to_csv(RESULTS / "gap_decomposition.csv", index=False)
    print("== Where the distance to the oracle comes from ==")
    print(t.round(3).to_string(index=False))
    print("\nH3 reading: if 'share_of_regret_on_top_decile_oracle_days' is far above 0.10 (the days' share), "
          "the missing revenue is concentrated on the highest-value days. 'days_beating_oracle' should be 0: "
          "anything else is a bug (or the cycle cap/efficiency interacting across solves).")
    m = df.assign(month=df["date"].dt.to_period("M").astype(str))
    cap = m.groupby("month").apply(lambda g: pd.Series({s: g[s].sum() / g["ORACLE"].sum() for s in strats if s not in ("ORACLE", "S0_no_battery")}),
                                    include_groups=False)
    cap["oracle_mean_usd_per_day"] = m.groupby("month")["ORACLE"].mean().round(0)
    cap.to_csv(RESULTS / "capture_by_month.csv")
    print("\n== Capture of oracle by month (models and baselines) ==")
    print(cap.round(3).to_string())


# ---------------------------------------------------------------- rank
def load_actual(hub: str) -> pd.Series:
    d = read_raw("dam_spp_hubs", ["interval_start_utc", "location", "spp"])
    d = d[d["location"] == hub].drop_duplicates("interval_start_utc", keep="last").set_index("interval_start_utc")["spp"]
    return d


def daily_metrics(fc: pd.Series, actual: pd.Series) -> pd.DataFrame:
    j = pd.concat([fc.rename("fc"), actual.rename("y")], axis=1, join="inner").dropna()
    j["date"] = j.index.tz_convert(TZ).tz_localize(None).normalize()
    rows = []
    for d, g in j.groupby("date"):
        if len(g) < 23:
            continue
        tau = g["fc"].corr(g["y"], method="kendall")
        e = g["fc"] - g["y"]
        rows.append(dict(date=d, tau=tau, mae=e.abs().mean(), rmse=math.sqrt((e ** 2).mean()),
                         day_range_actual=g["y"].max() - g["y"].min(), day_range_fc=g["fc"].max() - g["fc"].min()))
    return pd.DataFrame(rows)


def cmd_rank(a) -> None:
    from scipy.stats import spearmanr
    df, strats = load_daily(a.daily)
    actual = load_actual(a.hub)
    out = []
    for spec in a.forecast:
        name, path = spec.split("=", 1)
        col = f"S6_{name}_opt"
        if col not in df.columns:
            print(f"skip {name}: column {col} not in the daily file (run the backtest with --extra-forecast {name}=...)")
            continue
        f = pd.read_parquet(path)
        if "location" in f.columns:
            f = f[f["location"] == a.hub]
        f["t"] = pd.to_datetime(f["interval_start_utc"], utc=True)
        fc = f.drop_duplicates("t", keep="last").set_index("t")["spp_fc"]
        m = daily_metrics(fc, actual)
        m = m.merge(df[["date", "ORACLE", col]].rename(columns={col: "rev"}), on="date")
        m["regret"] = m["ORACLE"] - m["rev"]
        m["capture"] = m["rev"] / m["ORACLE"].where(m["ORACLE"] > 1)
        m["model"] = name
        out.append(m)
        print(f"\n== {name}: {len(m)} days. Spearman correlation with DAILY REVENUE REGRET (higher = metric tracks lost money) ==")
        for k in ("tau", "mae", "rmse", "day_range_fc"):
            rho = spearmanr(m[k], m["regret"], nan_policy="omit")
            r2 = spearmanr(m[k], m["capture"], nan_policy="omit")
            print(f"  {k:<13} vs regret: rho={rho.correlation:+.3f} (p={rho.pvalue:.1e})   vs capture: rho={r2.correlation:+.3f}")
        print("  Expect: tau negatively correlated with regret, MAE/RMSE positively. Compare the absolute values: "
              "H2 predicts |rho(tau)| > |rho(MAE)|.")
        # quartiles of tau -> mean capture
        m["tau_q"] = pd.qcut(m["tau"], 4, labels=["Q1 (worst)", "Q2", "Q3", "Q4 (best)"], duplicates="drop")
        print(m.groupby("tau_q", observed=True).agg(days=("tau", "size"), mean_tau=("tau", "mean"), mean_capture=("capture", "mean"),
                                                    mean_regret=("regret", "mean")).round(3).to_string())
    if out:
        pd.concat(out).to_parquet(RESULTS / "rank_vs_regret.parquet", index=False)
        print(f"\nSaved {RESULTS/'rank_vs_regret.parquet'}")


# ---------------------------------------------------------------- figs
def cmd_figs(a) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    df, strats = load_daily(a.daily)
    RESULTS.mkdir(parents=True, exist_ok=True)
    show = [s for s in strats if s not in ("S0_no_battery", "ORACLE", "S1_fixed_windows", "S2_yesterday_rule")]
    fig, ax = plt.subplots(figsize=(10, 5))
    for s in show:
        ax.plot(df["date"], (df["ORACLE"] - df[s]).cumsum() / 1e6, label=s, lw=2 if s.startswith("S6") else 1.2)
    ax.axvline(RTCB, color="gray", ls=":")
    ax.set_ylabel("Cumulative regret vs oracle ($ million)")
    ax.set_title("Revenue left on the table (lower is better)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_cumulative_regret.png", dpi=160)
    plt.close(fig)
    mth = df.assign(month=df["date"].dt.to_period("M").astype(str))
    cap = mth.groupby("month").apply(lambda g: pd.Series({s: g[s].sum() / g["ORACLE"].sum() for s in show}), include_groups=False)
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for s in show:
        ax.plot(range(len(cap)), cap[s] * 100, marker="o", ms=3, label=s, lw=2 if s.startswith("S6") else 1.2)
    ax.set_xticks(range(0, len(cap), 3))
    ax.set_xticklabels(cap.index[::3], rotation=45, ha="right")
    ax.set_ylabel("% of oracle revenue")
    ax.set_title("Monthly capture of perfect-foresight revenue")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_monthly_capture.png", dpi=160)
    plt.close(fig)
    made = ["fig_cumulative_regret.png", "fig_monthly_capture.png"]
    rk = RESULTS / "rank_vs_regret.parquet"
    if rk.exists():
        r = pd.read_parquet(rk)
        fig, ax = plt.subplots(figsize=(7, 5))
        for n, g in r.groupby("model"):
            ax.scatter(g["tau"], g["capture"] * 100, s=8, alpha=0.4, label=n)
        ax.set_xlabel("Daily Kendall tau (forecast vs actual hourly prices)")
        ax.set_ylabel("Daily capture of oracle (%)")
        ax.set_title("Does rank accuracy predict revenue? (H2)")
        ax.grid(alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(RESULTS / "fig_tau_vs_capture.png", dpi=160)
        plt.close(fig)
        made.append("fig_tau_vs_capture.png")
    print("Saved to", RESULTS, ":", ", ".join(made))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Step 4: audit and analysis")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("audit")
    s.add_argument("--n", type=int, default=6, help="random days per dataset to print")
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_audit)
    g = sub.add_parser("gap")
    g.add_argument("--daily", required=True)
    g.set_defaults(fn=cmd_gap)
    r = sub.add_parser("rank")
    r.add_argument("--daily", required=True)
    r.add_argument("--hub", default="HB_NORTH")
    r.add_argument("--forecast", action="append", required=True, help="name=path.parquet (same names as --extra-forecast)")
    r.set_defaults(fn=cmd_rank)
    f = sub.add_parser("figs")
    f.add_argument("--daily", required=True)
    f.set_defaults(fn=cmd_figs)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
