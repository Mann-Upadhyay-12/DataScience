#!/usr/bin/env python3
"""
forecast_pipeline.py - STEP 3 of the Texas Grid project: clean + join + forecast.

    python forecast_pipeline.py build                       # cleaning log, leak-safe feature table, leakage tests
    python forecast_pipeline.py train --hub HB_NORTH        # walk-forward GBM + Lasso(LEAR-style) forecasts
    python forecast_pipeline.py ablate --hub HB_NORTH       # leakage / ablation study with a placebo
    python battery_backtest.py backtest --hub HB_NORTH \
        --extra-forecast gbm=data/processed/forecast_gbm_HB_NORTH.parquet \
        --extra-forecast lasso=data/processed/forecast_lasso_HB_NORTH.parquet

SETUP
    pip install numpy pandas pyarrow scikit-learn lightgbm scipy      (lightgbm is optional)

THE RULE THAT PROTECTS YOUR RESULTS
    A forecast for local day D is made at 09:45 US-Central on day D-1. Every feature is therefore one of:
      cal_*       calendar (known in advance)
      lf_*        ERCOT load forecast by weather zone, as published before the cutoff
      wind_*      ERCOT wind forecast columns, as published before the cutoff
      solar_*     ERCOT solar forecast columns, as published before the cutoff
      nl_*        net-load features computed only from the three forecasts above
      wx_*        weather forecasts issued about 48h before the valid hour (always before the cutoff)
      actlag2d_*  actual system data lagged by >= 2 whole days
      plag*/pday1_*  the hub's own day-ahead prices from earlier days (built inside `train`)
    The ACTUAL generation columns (gen_*) and ERA5 weather observations are never used as features.
    `build` re-checks this with tests and refuses to continue if a test fails.

MODELS
    gbm    LightGBM (or sklearn HistGradientBoosting) on asinh(price/50): spike-tolerant, handles missing values
    lasso  one Lasso per local hour (LEAR-style, after Lago et al.); alpha tuned ONCE on the first training window
    ens    average of the two in asinh space
    Retraining: every --retrain-days, always on days strictly before the first day being forecast.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import warnings
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

DATA_DIR = Path(os.environ.get("ERCOT_DATA_DIR", "data"))
RAW, PROC, REPORTS = DATA_DIR / "raw", DATA_DIR / "processed", DATA_DIR / "reports"
TZ = ZoneInfo("America/Chicago")
CUTOFF_LOCAL, CUTOFF_DAYS_BEFORE = "09:45:00", 1
RTCB_GO_LIVE = date(2025, 12, 5)
HUBS = ["HB_BUSAVG", "HB_HOUSTON", "HB_NORTH", "HB_PAN", "HB_SOUTH", "HB_WEST"]
ASINH_SCALE = 50.0
ALLOWED_PREFIXES = ("cal_", "lf_", "wind_", "solar_", "nl_", "wx_", "wxd1unsafe_", "actlag2d_")
WX_ZONE_VARS = ["temperature_2m", "wind_speed_100m", "shortwave_radiation"]   # kept per zone
WX_MEAN_VARS = ["temperature_2m", "apparent_temperature", "relative_humidity_2m", "wind_speed_100m",
                "shortwave_radiation", "cloud_cover"]                          # averaged over zones


# ============================================================================
# 1. Helpers
# ============================================================================
class CleaningLog:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, stage: str, dataset: str, rows_in: int, rows_out: int, rule: str) -> None:
        self.rows.append(dict(stage=stage, dataset=dataset, rows_in=int(rows_in), rows_out=int(rows_out), rule=rule))
        print(f"  [{stage:<9}] {dataset:<26} {rows_in:>9,} -> {rows_out:>9,}  {rule}")

    def save(self) -> Path:
        REPORTS.mkdir(parents=True, exist_ok=True)
        p = REPORTS / "cleaning_log.csv"
        pd.DataFrame(self.rows).to_csv(p, index=False)
        return p


def read_raw(key: str) -> pd.DataFrame:
    folder = RAW / key
    files = sorted(list(folder.glob("*.parquet")) + list(folder.glob("*.csv.gz")))
    if not files:
        sys.exit(f"Missing raw data: {folder}")
    parts = [pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f) for f in files]
    df = pd.concat(parts, ignore_index=True)
    for c in df.columns:
        if c.endswith("_utc") and not c.startswith("_"):
            df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    return df


def local_lag(s: pd.Series, days: int) -> pd.Series:
    """Value at the same LOCAL clock hour `days` calendar days earlier (DST-safe up to the repeated hour)."""
    loc = s.index.tz_convert(TZ)
    d = loc.tz_localize(None).normalize()
    base = pd.DataFrame({"d": d, "h": loc.hour, "v": s.to_numpy()}).drop_duplicates(["d", "h"], keep="first")
    q = pd.DataFrame({"d": d - pd.Timedelta(days=days), "h": loc.hour})
    return pd.Series(q.merge(base, on=["d", "h"], how="left")["v"].to_numpy(), index=s.index)


def cutoff_utc(interval_start_utc: pd.Series) -> pd.Series:
    hh, mm, ss = (int(x) for x in CUTOFF_LOCAL.split(":"))
    day = interval_start_utc.dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()
    naive = day - pd.Timedelta(days=CUTOFF_DAYS_BEFORE) + pd.Timedelta(hours=hh, minutes=mm, seconds=ss)
    return naive.dt.tz_localize(TZ, ambiguous=True, nonexistent="shift_forward").dt.tz_convert("UTC")


# ============================================================================
# 2. BUILD: cleaning, joins, leakage tests
# ============================================================================
def load_asof(key: str, keep_regex: str, prefix: str, idx: pd.DatetimeIndex, log: CleaningLog, tests: dict) -> pd.DataFrame:
    df = read_raw(key)
    n0 = len(df)
    late = df["publish_time_utc"] > cutoff_utc(df["interval_start_utc"])
    tests[f"{key}_published_after_cutoff"] = int(late.sum())
    df = df[~late]
    log.add("leak-drop", key, n0, len(df), "drop rows published after the D-1 09:45 US-Central cutoff")
    n1 = len(df)
    df = df.sort_values(["interval_start_utc", "publish_time_utc"]).drop_duplicates("interval_start_utc", keep="last")
    log.add("dedupe", key, n1, len(df), "one row per target hour: latest publication before the cutoff")
    cols = [c for c in df.columns if re.match(keep_regex, c)]
    sub = df.set_index("interval_start_utc")[cols].apply(pd.to_numeric, errors="coerce")
    all_nan = [c for c in sub.columns if sub[c].isna().mean() > 0.9]
    sub = sub.drop(columns=all_nan)
    log.add("columns", key, len(cols), sub.shape[1], f"dropped mostly-empty columns: {all_nan[:4]}{'...' if len(all_nan) > 4 else ''}")
    out = sub.reindex(idx).add_prefix(prefix)
    log.add("align", key, len(sub), int(out.notna().any(axis=1).sum()), "aligned to the hourly UTC grid; gaps stay NaN (no interpolation)")
    return out


def build_weather(idx: pd.DatetimeIndex, mode: str, log: CleaningLog) -> pd.DataFrame:
    if mode == "none":
        return pd.DataFrame(index=idx)
    leads = {"d2": [2], "d1": [2, 1]}[mode]
    folders = sorted(p for p in (RAW / "weather_fcst").iterdir() if p.is_dir())
    frames: dict[str, pd.DataFrame] = {}
    for f in folders:
        z = pd.concat([pd.read_parquet(p) for p in sorted(f.glob("*.parquet"))], ignore_index=True)
        z["time"] = pd.to_datetime(z["time"], utc=True)
        frames[f.name] = z.drop_duplicates("time", keep="last").set_index("time").reindex(idx)
    out = {}
    for lead in leads:
        pre = "wx_d2" if lead == 2 else "wxd1unsafe_d1"
        for var in WX_MEAN_VARS:
            col = f"{var}_previous_day{lead}"
            stack = pd.concat({z: fr[col] for z, fr in frames.items() if col in fr.columns}, axis=1)
            out[f"{pre}_{var}_mean"] = stack.mean(axis=1)
            if var in WX_ZONE_VARS:
                for z in stack.columns:
                    out[f"{pre}_{var}_{z}"] = stack[z]
    w = pd.DataFrame(out, index=idx)
    first_valid = w["wx_d2_temperature_2m_mean"].first_valid_index()
    log.add("weather", "weather_fcst", len(idx), int(w.notna().any(axis=1).sum()),
            f"{len(w.columns)} columns; leads={leads}; forecasts valid from {first_valid}; NaN before that is kept")
    return w


def cmd_build(args) -> None:
    PROC.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    log, tests = CleaningLog(), {}
    print("== BUILD ==")
    # ---- day-ahead prices: the targets ----------------------------------------
    dam = read_raw("dam_spp_hubs")
    n0 = len(dam)
    dam = dam.sort_values(["interval_start_utc", "location", "_pulled_at_utc"]).drop_duplicates(
        ["interval_start_utc", "location"], keep="last")
    log.add("dedupe", "dam_spp_hubs", n0, len(dam), "keep last pull per (hour, hub)")
    wide = dam.pivot(index="interval_start_utc", columns="location", values="spp").add_prefix("dam_")
    full = pd.date_range(wide.index.min(), wide.index.max(), freq="1h", tz="UTC")
    gaps = full.difference(wide.index)
    wide = wide.reindex(full)
    wide.index.name = "interval_start_utc"
    log.add("gaps", "dam_spp_hubs", len(wide), int(wide.notna().all(axis=1).sum()),
            f"{len(gaps)} missing hours flagged as NaN, prices are never interpolated")
    idx = wide.index
    # ---- calendar ------------------------------------------------------------------
    loc = idx.tz_convert(TZ)
    from pandas.tseries.holiday import USFederalHolidayCalendar
    hol = USFederalHolidayCalendar().holidays(start=loc.min().tz_localize(None), end=loc.max().tz_localize(None))
    ldate = loc.tz_localize(None).normalize()
    cal = pd.DataFrame({
        "cal_hour": loc.hour, "cal_dow": loc.dayofweek, "cal_month": loc.month,
        "cal_doy_sin": np.sin(2 * np.pi * loc.dayofyear / 365.25), "cal_doy_cos": np.cos(2 * np.pi * loc.dayofyear / 365.25),
        "cal_weekend": (loc.dayofweek >= 5).astype(int), "cal_holiday": ldate.isin(hol).astype(int),
        "cal_post_rtcb": (ldate >= pd.Timestamp(RTCB_GO_LIVE)).astype(int)}, index=idx)
    meta = pd.DataFrame({"meta_local_date": ldate, "meta_hour": loc.hour}, index=idx)
    # ---- forecasts as published before the cutoff -----------------------------------------
    lf = load_asof("load_forecast_asof", r"^(coast|east|far_west|north|north_central|south_central|southern|west|system_total)$",
                   "lf_", idx, log, tests)
    wind = load_asof("wind_forecast_asof", r"^(cop_hsl|stwpf|wgrpp)_", "wind_", idx, log, tests)
    solar = load_asof("solar_forecast_asof", r"^(cop_hsl|stppf|pvgrpp)_", "solar_", idx, log, tests)
    nl = pd.DataFrame(index=idx)
    if {"lf_system_total", "wind_stwpf_system_wide", "solar_stppf_system_wide"} <= set(lf.columns) | set(wind.columns) | set(solar.columns):
        nl["nl_system"] = lf["lf_system_total"] - wind["wind_stwpf_system_wide"] - solar["solar_stppf_system_wide"]
        nl["nl_ramp1"], nl["nl_ramp3"] = nl["nl_system"].diff(1), nl["nl_system"].diff(3)
        nl["nl_wind_share"] = wind["wind_stwpf_system_wide"] / lf["lf_system_total"]
        nl["nl_solar_share"] = solar["solar_stppf_system_wide"] / lf["lf_system_total"]
    # ---- actual system data, lagged by at least 2 whole days -------------------------------------
    fm = read_raw("fuel_mix_hourly").drop_duplicates("interval_start_utc", keep="last").set_index("interval_start_utc")
    ld = read_raw("load_system_hourly").drop_duplicates("interval_start_utc", keep="last").set_index("interval_start_utc")
    act = pd.DataFrame(index=idx)
    for name, s in (("wind", fm["wind"]), ("solar", fm["solar"]), ("gas", fm["natural_gas"]),
                    ("storage", fm["power_storage"]), ("load", ld["load"])):
        act[f"actlag2d_{name}"] = local_lag(s.reindex(idx), 2)
    log.add("actuals", "fuel_mix+load", len(idx), int(act.notna().any(axis=1).sum()), "actual generation/load used only at lag >= 2 days")
    wx = build_weather(idx, args.weather, log)
    # ---- assemble + tests ---------------------------------------------------------------------------
    features = pd.concat([cal, lf, wind, solar, nl, wx, act], axis=1)
    table = pd.concat([wide, meta, features], axis=1)
    bad = [c for c in features.columns if not c.startswith(ALLOWED_PREFIXES)]
    tests["T2_unlisted_feature_columns"] = bad
    tests["T4_actual_columns_in_features"] = [c for c in features.columns if re.search(r"(^|_)gen_", c)]
    rng = np.random.default_rng(0)
    pick = rng.choice(np.where(act["actlag2d_wind"].notna())[0], 5, replace=False)
    ok = 0
    for i in pick:  # T3 spot check of the 2-day lag against the raw series
        t = idx[i].tz_convert(TZ)
        want = pd.Timestamp(t.tz_localize(None).date()) - pd.Timedelta(days=2) + pd.Timedelta(hours=t.hour)
        cand = fm["wind"][fm["wind"].index.tz_convert(TZ).tz_localize(None) == want]
        ok += int(len(cand) > 0 and abs(cand.iloc[0] - act["actlag2d_wind"].iloc[i]) < 1e-6)
    tests["T3_lag_spotcheck_passed_of_5"] = ok
    table.to_parquet(PROC / "model_table.parquet")
    nan_by_group = {g: round(float(features[[c for c in features.columns if c.startswith(g)]].isna().mean().mean()), 4)
                    for g in ALLOWED_PREFIXES if any(c.startswith(g) for c in features.columns)}
    report = dict(rows=len(table), first=str(idx.min()), last=str(idx.max()), feature_columns=int(features.shape[1]),
                  target_hubs=[c for c in wide.columns], nan_share_by_feature_group=nan_by_group, tests=tests)
    (REPORTS / "build_report.json").write_text(json.dumps(report, indent=2, default=str))
    p = log.save()
    print("\n== LEAKAGE TESTS ==")
    for k, v in tests.items():
        print(f"  {k}: {v}")
    print(f"\nFeature groups, average NaN share: {nan_by_group}")
    print(f"Wrote {PROC/'model_table.parquet'} ({len(table):,} rows, {features.shape[1]} features), {p}, build_report.json")
    failed = bool(bad) or bool(tests["T4_actual_columns_in_features"]) or tests["T3_lag_spotcheck_passed_of_5"] < 5
    if failed:
        sys.exit("A leakage test FAILED. Fix before training.")
    late = {k: v for k, v in tests.items() if k.endswith("published_after_cutoff") and v}
    if late:
        print(f"NOTE: rows published after the cutoff were DROPPED (not used): {late}")
    print("All leakage tests passed.")


# ============================================================================
# 3. TRAIN: walk-forward forecasts
# ============================================================================
def hub_price_features(table: pd.DataFrame, hub: str) -> pd.DataFrame:
    s = table[f"dam_{hub}"]
    f = pd.DataFrame(index=table.index)
    lags = {k: local_lag(s, k) for k in range(1, 8)}
    f["plag1d"], f["plag2d"], f["plag7d"] = lags[1], lags[2], lags[7]
    f["plag_avg7"] = pd.concat(lags.values(), axis=1).mean(axis=1, skipna=True).where(pd.concat(lags.values(), axis=1).notna().sum(axis=1) >= 5)
    d = table["meta_local_date"]
    daily = s.groupby(d).agg(["mean", "max", "min", "std"])
    for stat in daily.columns:
        prev = daily[stat].copy()
        prev.index = prev.index + pd.Timedelta(days=1)       # value of day D-1 is visible on day D
        f[f"pday1_{stat}"] = d.map(prev).to_numpy()
    f["pday1_spread"] = f["pday1_max"] - f["pday1_min"]
    f["plag1d_over_avg7"] = f["plag1d"] - f["plag_avg7"]
    return f


class Gbm:
    def __init__(self):
        try:
            import lightgbm as lgb
            self.m = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=40,
                                       subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=1.0, verbose=-1)
            self.kind = "lightgbm"
        except ImportError:
            from sklearn.ensemble import HistGradientBoostingRegressor
            self.m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, l2_regularization=1.0)
            self.kind = "sklearn-hgb"

    def fit(self, X, y):
        self.cols = list(X.columns)
        self.m.fit(X, y)
        return self

    def predict(self, X):
        return self.m.predict(X[self.cols])

    def importance(self) -> pd.Series:
        imp = getattr(self.m, "feature_importances_", None)
        return pd.Series(imp, index=self.cols).sort_values(ascending=False) if imp is not None else pd.Series(dtype=float)


class HourlyLasso:
    """One Lasso per local hour, alpha tuned on the FIRST training window only.

    Linear models extrapolate: one extreme feature value can produce an absurd forecast (a real run showed an
    RMSE of 526 $/MWh). So standardized features are clipped to +-Z_CLIP and predictions are clipped to the
    range of targets seen in training (plus a small margin).
    """
    Z_CLIP, Y_MARGIN = 4.0, 0.5

    def __init__(self):
        self.alpha: dict[int, float] = {}
        self.models: dict[int, tuple] = {}

    def fit(self, X, y, hours):
        from sklearn.exceptions import ConvergenceWarning
        from sklearn.linear_model import Lasso, LassoCV
        from sklearn.model_selection import TimeSeriesSplit
        warnings.filterwarnings("ignore", category=ConvergenceWarning)  # tiny duality gaps on noisy features are fine
        self.cols = [c for c in X.columns if X[c].notna().mean() > 0.5]
        for h in range(24):
            m = (hours == h).to_numpy()
            if m.sum() < 60:
                continue
            Xf = X.loc[m, self.cols]
            med = Xf.median()
            Xf = Xf.fillna(med)
            mu, sd = Xf.mean(), Xf.std().replace(0, 1.0)
            Z = ((Xf - mu) / sd).clip(-self.Z_CLIP, self.Z_CLIP).to_numpy()
            if h not in self.alpha:
                try:  # scikit-learn renamed n_alphas -> alphas in recent versions
                    cv = LassoCV(cv=TimeSeriesSplit(3), n_alphas=25, max_iter=5000)
                except TypeError:
                    cv = LassoCV(cv=TimeSeriesSplit(3), alphas=25, max_iter=5000)
                cv.fit(Z, y[m])
                self.alpha[h] = float(cv.alpha_)
            lo, hi = float(np.min(y[m])) - self.Y_MARGIN, float(np.max(y[m])) + self.Y_MARGIN
            self.models[h] = (Lasso(alpha=self.alpha[h], max_iter=5000).fit(Z, y[m]), med, mu, sd, lo, hi)
        return self

    def predict(self, X, hours):
        out = np.full(len(X), np.nan)
        for h, (m, med, mu, sd, lo, hi) in self.models.items():
            sel = (hours == h).to_numpy()
            if sel.any():
                Z = ((X.loc[sel, self.cols].fillna(med) - mu) / sd).clip(-self.Z_CLIP, self.Z_CLIP).to_numpy()
                out[sel] = np.clip(m.predict(Z), lo, hi)
        return out


def dm_test(loss_a: np.ndarray, loss_b: np.ndarray, lag: int = 7) -> tuple[float, float]:
    """Diebold-Mariano on daily loss differentials (Newey-West variance). Negative stat = A better than B."""
    from scipy.stats import norm
    d = loss_a - loss_b
    n, dbar = len(d), d.mean()
    g = [np.sum((d[l:] - dbar) * (d[:n - l] - dbar)) / n for l in range(lag + 1)]
    var = g[0] + 2 * sum((1 - l / (lag + 1)) * g[l] for l in range(1, lag + 1))
    stat = dbar / math.sqrt(max(var, 1e-12) / n)
    return float(stat), float(2 * (1 - norm.cdf(abs(stat))))


FUNDAMENTAL_PREFIXES = ("lf_", "wind_", "solar_", "nl_", "wx_", "wxd1unsafe_")


def prepare(table: pd.DataFrame, hub: str, drop: list[str], placebo: str | None, weather_off: bool = False):
    feats = [c for c in table.columns if c.startswith(ALLOWED_PREFIXES)]
    drop = [d for d in drop if d] + (["wx_", "wxd1unsafe_"] if weather_off else [])
    feats = [c for c in feats if not c.startswith(tuple(drop))] if drop else feats
    if any(c.startswith("wxd1unsafe_") for c in feats):
        print("WARNING: wxd1unsafe_* features LEAK information from after the cutoff. Use only as a labelled ablation.")
    X = pd.concat([table[feats], hub_price_features(table, hub)], axis=1)
    if placebo == "shuffle":   # break the link between fundamentals and prices: values are permuted within each hour-of-day
        rng = np.random.default_rng(0)
        cols = [X.columns.get_loc(c) for c in X.columns if c.startswith(FUNDAMENTAL_PREFIXES)]
        for h in range(24):
            pos = np.where(table["meta_hour"].to_numpy() == h)[0]
            X.iloc[pos, cols] = X.iloc[rng.permutation(pos), cols].to_numpy()
    return X


def walk_forward(X, y, day, hours, have_y, test_days, names, retrain_days, min_rows, verbose=True):
    preds = {n: pd.Series(np.nan, index=X.index) for n in names}
    lasso = HourlyLasso() if "lasso" in names else None
    last_gbm = None
    blocks = [test_days[i:i + retrain_days] for i in range(0, len(test_days), retrain_days)]
    for bi, blk in enumerate(blocks):
        tr = (day < blk[0]) & have_y & X["plag_avg7"].notna()   # strictly BEFORE the first day being forecast
        if tr.sum() < min_rows:
            continue
        te = day.isin(blk)
        if "gbm" in names:
            last_gbm = Gbm().fit(X[tr], y[tr])
            preds["gbm"][te] = last_gbm.predict(X[te])
        if lasso is not None:
            lasso.fit(X[tr], y[tr].to_numpy(), hours[tr])
            preds["lasso"][te] = lasso.predict(X[te], hours[te])
        if verbose and bi % 10 == 0:
            print(f"  block {bi + 1}/{len(blocks)} trained on {int(tr.sum()):,} rows, forecasting {blk[0].date()}..{blk[-1].date()}")
    if "gbm" in names and "lasso" in names:
        preds["ens"] = (preds["gbm"] + preds["lasso"]) / 2
    return preds, last_gbm


def to_usd(p: pd.Series) -> pd.Series:
    return ASINH_SCALE * np.sinh(p.clip(-8, 8))


def quick_capture(hub: str, fc_usd: pd.Series) -> float:
    """Share of perfect-foresight revenue earned by a forecast (default 100 MW x 2 h battery, as in the backtest)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from battery_backtest import Battery, is_valid, load_days, settle, solve_day
    days, bat = load_days(hub), Battery()
    tot = orc = 0.0
    for d, day in days.items():
        if not is_valid(days, d):
            continue
        fc = fc_usd.reindex(day.t_utc).to_numpy(dtype=float)
        if np.isnan(fc).any():
            continue
        tot += settle(*solve_day(fc, bat), day.prices, bat)
        orc += settle(*solve_day(day.prices, bat), day.prices, bat)
    return tot / orc if orc else float("nan")


def cmd_train(args) -> None:
    table = pd.read_parquet(PROC / "model_table.parquet")
    hub, tag = args.hub, args.tag
    if f"dam_{hub}" not in table.columns:
        sys.exit(f"Unknown hub {hub}")
    X = prepare(table, hub, args.drop.split(",") if args.drop else [], args.placebo, args.weather_off)
    y_raw = table[f"dam_{hub}"]
    y = np.arcsinh(y_raw / ASINH_SCALE)
    day, hours, have_y = table["meta_local_date"], table["meta_hour"], y_raw.notna()
    test_days = sorted(d for d in day[have_y].unique() if d >= pd.Timestamp(args.first_test))
    names = args.models.split(",")
    print(f"Hub {hub}{tag}: {len(test_days)} test days from {test_days[0].date()}; {X.shape[1]} features; gbm backend: {Gbm().kind}")
    preds, last_gbm = walk_forward(X, y, day, hours, have_y, test_days, names, args.retrain_days, args.min_train_rows)
    PROC.mkdir(parents=True, exist_ok=True)
    out_cols = {}
    for n, p in preds.items():
        usd = to_usd(p)
        out_cols[n] = usd
        pd.DataFrame({"interval_start_utc": table.index, "location": hub, "spp_fc": usd.to_numpy()}).dropna().to_parquet(
            PROC / f"forecast_{n}_{hub}{tag}.parquet", index=False)
    ev = pd.DataFrame({"y": y_raw, "day": day, "regime": np.where(table["cal_post_rtcb"] == 1, "post_RTCB", "pre_RTCB"),
                       "persistence": X["plag1d"], "weekly_naive": X["plag7d"], "avg7": X["plag_avg7"], **out_cols})
    ev = ev[ev["day"].isin(test_days)].dropna()
    ev = ev[ev["day"].map(ev.groupby("day").size()) >= 23]
    rows = []
    for m in ["persistence", "weekly_naive", "avg7"] + list(out_cols):
        e = ev[m] - ev["y"]
        tau = ev.groupby("day").apply(lambda g: pd.Series(g[m]).corr(g["y"], method="kendall"), include_groups=False).mean()
        row = dict(model=m, MAE=e.abs().mean(), RMSE=math.sqrt((e ** 2).mean()),
                   MAE_asinh=(np.arcsinh(ev[m] / ASINH_SCALE) - np.arcsinh(ev["y"] / ASINH_SCALE)).abs().mean(),
                   worst_abs_error=e.abs().max(), kendall_tau=tau)
        for rg in ("pre_RTCB", "post_RTCB"):
            sub = ev[ev["regime"] == rg]
            row[f"MAE_{rg}"] = (sub[m] - sub["y"]).abs().mean() if len(sub) else np.nan
        d_a = (ev[m] - ev["y"]).abs().groupby(ev["day"]).mean().to_numpy()
        d_b = (ev["avg7"] - ev["y"]).abs().groupby(ev["day"]).mean().to_numpy()
        row["DM_vs_avg7_stat"], row["DM_vs_avg7_p"] = (dm_test(d_a, d_b) if m != "avg7" else (np.nan, np.nan))
        rows.append(row)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(PROC / f"forecast_metrics_{hub}{tag}.csv", index=False)
    print(f"\n== Forecast accuracy on {ev['day'].nunique()} identical test days ({hub}{tag}), prices in $/MWh ==")
    print(metrics.round(3).to_string(index=False))
    print("DM stat < 0 and p < 0.05 means significantly better than the 7-day average baseline.")
    if last_gbm is not None and len(last_gbm.importance()):
        imp = last_gbm.importance()
        imp.head(30).to_csv(PROC / f"feature_importance_gbm_{hub}{tag}.csv")
        print("\nTop GBM features (last fit):", ", ".join(imp.head(12).index))
    flags = " ".join(f"--extra-forecast {n}=data/processed/forecast_{n}_{hub}{tag}.parquet" for n in out_cols)
    print(f"\nNext: python battery_backtest.py backtest --hub {hub} {flags}")


def cmd_ablate(args) -> None:
    """Retrain the GBM with feature groups removed. A group whose removal collapses performance is where the
    value (or a leak) lives: audit it first. Also runs a placebo with shuffled fundamentals."""
    table = pd.read_parquet(PROC / "model_table.parquet")
    hub = args.hub
    y_raw = table[f"dam_{hub}"]
    y = np.arcsinh(y_raw / ASINH_SCALE)
    day, hours, have_y = table["meta_local_date"], table["meta_hour"], y_raw.notna()
    test_days = sorted(d for d in day[have_y].unique() if d >= pd.Timestamp(args.first_test))
    variants = [("full", [], None),
                ("prices_and_calendar_only", ["lf_", "wind_", "solar_", "nl_", "wx_", "actlag2d_"], None),
                ("no_load_forecast", ["lf_", "nl_"], None), ("no_wind_forecast", ["wind_", "nl_"], None),
                ("no_solar_forecast", ["solar_", "nl_"], None), ("no_net_load_features", ["nl_"], None),
                ("no_weather", ["wx_"], None), ("no_actual_lags", ["actlag2d_"], None),
                ("PLACEBO_shuffled_fundamentals", [], "shuffle")]
    rows = []
    for name, drop, placebo in variants:
        X = prepare(table, hub, drop, placebo)
        preds, _ = walk_forward(X, y, day, hours, have_y, test_days, ["gbm"], args.retrain_days, args.min_train_rows, verbose=False)
        usd = to_usd(preds["gbm"])
        ev = pd.DataFrame({"y": y_raw, "p": usd, "day": day, "avg7": X["plag_avg7"]})
        ev = ev[ev["day"].isin(test_days)].dropna()
        ev = ev[ev["day"].map(ev.groupby("day").size()) >= 23]
        e = ev["p"] - ev["y"]
        tau = ev.groupby("day").apply(lambda g: g["p"].corr(g["y"], method="kendall"), include_groups=False).mean()
        rows.append(dict(variant=name, features=X.shape[1], MAE=e.abs().mean(), RMSE=math.sqrt((e ** 2).mean()),
                         kendall_tau=tau, capture_of_oracle=quick_capture(hub, usd)))
        print(f"  done: {name:<32} MAE {rows[-1]['MAE']:.2f}  capture {rows[-1]['capture_of_oracle']:.3f}", flush=True)
    out = pd.DataFrame(rows)
    out["capture_change_vs_full"] = out["capture_of_oracle"] - out.loc[0, "capture_of_oracle"]
    out.to_csv(PROC / f"ablation_{hub}.csv", index=False)
    print(f"\n== ABLATION ({hub}, GBM, retrain every {args.retrain_days} days) ==")
    print(out.round(3).to_string(index=False))
    print("""
HOW TO READ THIS
  * prices_and_calendar_only should be close to the 7-day-average baseline. If it is far above, price lags leak.
  * PLACEBO_shuffled_fundamentals must fall to roughly prices_and_calendar_only. If it stays high, the signal is
    coming from somewhere other than the fundamentals: audit the price-lag features.
  * The group whose removal costs the most is expected to be the load/net-load forecast. Verify its publish
    times against ERCOT's report schedule once by hand for a few days.""")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Step 3: leak-safe features and walk-forward forecasts")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--weather", choices=["d2", "d1", "none"], default="d2",
                   help="d2 = safe 48h-lead forecasts (default); d1 adds UNSAFE 24h-lead columns for an ablation")
    b.set_defaults(fn=cmd_build)
    t = sub.add_parser("train")
    t.add_argument("--hub", default="HB_NORTH", choices=HUBS)
    t.add_argument("--models", default="gbm,lasso")
    t.add_argument("--first-test", default="2024-07-01")
    t.add_argument("--retrain-days", type=int, default=7)
    t.add_argument("--min-train-rows", type=int, default=2000)
    t.add_argument("--weather-off", action="store_true", help="drop weather features (ablation)")
    t.add_argument("--drop", default="", help="comma list of feature prefixes to drop, e.g. wx_,actlag2d_")
    t.add_argument("--placebo", choices=["shuffle"], default=None)
    t.add_argument("--tag", default="", help="suffix for output files, e.g. _noweather")
    t.set_defaults(fn=cmd_train)
    ab = sub.add_parser("ablate", help="leakage/ablation study with a placebo")
    ab.add_argument("--hub", default="HB_NORTH", choices=HUBS)
    ab.add_argument("--first-test", default="2024-07-01")
    ab.add_argument("--retrain-days", type=int, default=28)
    ab.add_argument("--min-train-rows", type=int, default=2000)
    ab.set_defaults(fn=cmd_ablate)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
