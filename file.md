# Battery Backtest & DP Check Execution Outputs

This document records the exact command outputs from running `dp-check` and `backtest` (including baseline, cross-hub comparison, and sensitivity runs) using [`battery_backtest.py`](file:///home/mann/CHAMP/college/ds/battery_backtest.py).

---

## 1. DP Check (`python3 battery_backtest.py dp-check`)

```text
LP vs DP on 40 random days (grid of 100 SoC levels):
  relative gap LP-DP: mean 0.1210%, max 2.4185%, min 0.0040%
  LP should be >= DP (continuous vs discretized) and within a small discretization error.
  PASS
```

---

## 2. Baseline Backtest (`python3 battery_backtest.py backtest`)

```text
===== HB_NORTH | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
days used: 1030 (2024-01-08 -> 2026-11-02) of 1039 days in data
NOTE: 15 optimizer solves used simultaneous charge+discharge (happens with negative prices). Re-run with --no-simul to forbid it and compare.
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            27207.182              0.664           0.593            0.724             0.649              0.708           0.850             0.170          NaN
  S2_yesterday_rule            26783.228              0.654           0.570            0.728             0.665              0.621           0.727             0.175          NaN
 S3_persistence_opt            34554.532              0.843           0.793            0.887             0.862              0.790           0.978             0.168        0.607
S4_weekly_naive_opt            32610.736              0.796           0.725            0.853             0.825              0.711           0.979             0.157        0.568
        S5_avg7_opt            35345.463              0.863           0.795            0.914             0.891              0.781           0.982             0.166        0.661
             ORACLE            40968.046              1.000           1.000            1.000             1.000              1.000           0.978             0.163          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain   ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -9467.0 -11023.0  -8157.7         True
   S1_fixed_windows S3_persistence_opt          -2013.0  -2743.6  -1521.3         True
  S2_yesterday_rule S3_persistence_opt          -2129.1  -2932.1  -1612.5         True
S4_weekly_naive_opt S3_persistence_opt           -532.5  -1160.2    -21.2         True
        S5_avg7_opt S3_persistence_opt            216.7   -138.9    464.7        False
             ORACLE S3_persistence_opt           1757.1   1212.2   2456.8         True

Files written to data/results
```

---

## 3. All Hubs Siting Comparison (`python3 battery_backtest.py backtest --hub all`)

```text
===== HB_BUSAVG | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
days used: 1030 (2024-01-08 -> 2026-11-02) of 1039 days in data
NOTE: 6 optimizer solves used simultaneous charge+discharge (happens with negative prices). Re-run with --no-simul to forbid it and compare.
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            26775.286              0.667           0.599            0.725             0.656              0.701           0.850             0.171          NaN
  S2_yesterday_rule            26538.736              0.661           0.579            0.735             0.680              0.604           0.734             0.175          NaN
 S3_persistence_opt            33816.088              0.842           0.793            0.885             0.863              0.779           0.977             0.169        0.616
S4_weekly_naive_opt            32070.945              0.799           0.729            0.855             0.830              0.704           0.978             0.157        0.581
        S5_avg7_opt            35012.722              0.872           0.816            0.917             0.894              0.807           0.981             0.165        0.669
             ORACLE            40147.006              1.000           1.000            1.000             1.000              1.000           0.978             0.163          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain   ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -9264.7 -10810.6  -7978.5         True
   S1_fixed_windows S3_persistence_opt          -1929.0  -2634.1  -1439.3         True
  S2_yesterday_rule S3_persistence_opt          -1993.8  -2751.4  -1506.6         True
S4_weekly_naive_opt S3_persistence_opt           -478.1  -1073.1      4.1        False
        S5_avg7_opt S3_persistence_opt            327.8    120.8    510.6         True
             ORACLE S3_persistence_opt           1734.5   1219.1   2402.7         True

===== HB_HOUSTON | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
days used: 1030 (2024-01-08 -> 2026-11-02) of 1039 days in data
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            23850.006              0.622           0.558            0.680             0.624              0.617           0.850             0.191          NaN
  S2_yesterday_rule            23661.878              0.617           0.530            0.697             0.640              0.545           0.741             0.171          NaN
 S3_persistence_opt            30622.746              0.799           0.739            0.852             0.816              0.744           0.978             0.165        0.619
S4_weekly_naive_opt            29119.449              0.759           0.691            0.816             0.799              0.632           0.978             0.165        0.553
        S5_avg7_opt            32931.844              0.859           0.800            0.905             0.887              0.768           0.981             0.174        0.650
             ORACLE            38348.379              1.000           1.000            1.000             1.000              1.000           0.978             0.167          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain  ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -8389.8 -9809.2  -7272.8         True
   S1_fixed_windows S3_persistence_opt          -1855.5 -2580.1  -1223.9         True
  S2_yesterday_rule S3_persistence_opt          -1907.1 -2676.8  -1405.5         True
S4_weekly_naive_opt S3_persistence_opt           -411.9 -1072.6    219.1        False
        S5_avg7_opt S3_persistence_opt            632.6   222.7   1223.7         True
             ORACLE S3_persistence_opt           2116.6  1475.1   2991.7         True

===== HB_NORTH | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
days used: 1030 (2024-01-08 -> 2026-11-02) of 1039 days in data
NOTE: 15 optimizer solves used simultaneous charge+discharge (happens with negative prices). Re-run with --no-simul to forbid it and compare.
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            27207.182              0.664           0.593            0.724             0.649              0.708           0.850             0.170          NaN
  S2_yesterday_rule            26783.228              0.654           0.570            0.728             0.665              0.621           0.727             0.175          NaN
 S3_persistence_opt            34554.532              0.843           0.793            0.887             0.862              0.790           0.978             0.168        0.607
S4_weekly_naive_opt            32610.736              0.796           0.725            0.853             0.825              0.711           0.979             0.157        0.568
        S5_avg7_opt            35345.463              0.863           0.795            0.914             0.891              0.781           0.982             0.166        0.661
             ORACLE            40968.046              1.000           1.000            1.000             1.000              1.000           0.978             0.163          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain   ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -9467.0 -11023.0  -8157.7         True
   S1_fixed_windows S3_persistence_opt          -2013.0  -2743.6  -1521.3         True
  S2_yesterday_rule S3_persistence_opt          -2129.1  -2932.1  -1612.5         True
S4_weekly_naive_opt S3_persistence_opt           -532.5  -1160.2    -21.2         True
        S5_avg7_opt S3_persistence_opt            216.7   -138.9    464.7        False
             ORACLE S3_persistence_opt           1757.1   1212.2   2456.8         True

===== HB_PAN | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
days used: 1030 (2024-01-08 -> 2026-11-02) of 1039 days in data
NOTE: 85 optimizer solves used simultaneous charge+discharge (happens with negative prices). Re-run with --no-simul to forbid it and compare.
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            25503.583              0.602           0.530            0.663             0.583              0.654           0.850             0.183          NaN
  S2_yesterday_rule            20152.259              0.475           0.404            0.548             0.461              0.516           0.623             0.165          NaN
 S3_persistence_opt            31094.914              0.733           0.692            0.774             0.743              0.707           0.987             0.164        0.421
S4_weekly_naive_opt            28849.327              0.680           0.618            0.733             0.714              0.586           0.988             0.174        0.376
        S5_avg7_opt            33746.663              0.796           0.723            0.851             0.827              0.711           0.988             0.175        0.493
             ORACLE            42397.576              1.000           1.000            1.000             1.000              1.000           0.987             0.160          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain  ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -8519.2 -9897.3  -7382.7         True
   S1_fixed_windows S3_persistence_opt          -1531.9 -2333.2   -889.5         True
  S2_yesterday_rule S3_persistence_opt          -2998.0 -3983.9  -2288.0         True
S4_weekly_naive_opt S3_persistence_opt           -615.2 -1282.5    -14.7         True
        S5_avg7_opt S3_persistence_opt            726.5   162.1   1216.2         True
             ORACLE S3_persistence_opt           3096.6  2456.4   3864.7         True

===== HB_SOUTH | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
days used: 1030 (2024-01-08 -> 2026-11-02) of 1039 days in data
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            25334.477              0.663           0.600            0.718             0.657              0.683           0.850             0.176          NaN
  S2_yesterday_rule            25298.160              0.662           0.583            0.735             0.692              0.571           0.746             0.180          NaN
 S3_persistence_opt            32024.369              0.839           0.797            0.877             0.862              0.764           0.977             0.171        0.619
S4_weekly_naive_opt            29767.096              0.779           0.714            0.834             0.812              0.677           0.978             0.159        0.572
        S5_avg7_opt            33183.992              0.869           0.819            0.909             0.892              0.796           0.980             0.167        0.662
             ORACLE            38187.946              1.000           1.000            1.000             1.000              1.000           0.978             0.160          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain   ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -8773.8 -10268.8  -7532.4         True
   S1_fixed_windows S3_persistence_opt          -1832.8  -2458.9  -1362.1         True
  S2_yesterday_rule S3_persistence_opt          -1842.8  -2545.9  -1385.6         True
S4_weekly_naive_opt S3_persistence_opt           -618.4  -1113.2   -269.7         True
        S5_avg7_opt S3_persistence_opt            317.7     96.1    514.9         True
             ORACLE S3_persistence_opt           1688.7   1258.4   2231.6         True

===== HB_WEST | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
days used: 1030 (2024-01-08 -> 2026-11-02) of 1039 days in data
NOTE: 62 optimizer solves used simultaneous charge+discharge (happens with negative prices). Re-run with --no-simul to forbid it and compare.
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            31016.722              0.679           0.615            0.733             0.673              0.695           0.850             0.153          NaN
  S2_yesterday_rule            25509.790              0.558           0.481            0.631             0.572              0.518           0.688             0.140          NaN
 S3_persistence_opt            36433.263              0.797           0.753            0.840             0.816              0.743           0.982             0.156        0.535
S4_weekly_naive_opt            34902.006              0.764           0.702            0.813             0.803              0.651           0.982             0.148        0.502
        S5_avg7_opt            39140.237              0.856           0.801            0.900             0.884              0.776           0.978             0.153        0.608
             ORACLE            45706.381              1.000           1.000            1.000             1.000              1.000           0.982             0.150          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain   ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -9981.7 -11483.5  -8750.0         True
   S1_fixed_windows S3_persistence_opt          -1484.0  -2250.3   -873.3         True
  S2_yesterday_rule S3_persistence_opt          -2992.7  -4158.6  -2141.2         True
S4_weekly_naive_opt S3_persistence_opt           -419.5  -1048.8    145.6        False
        S5_avg7_opt S3_persistence_opt            741.6    359.2   1179.3         True
             ORACLE S3_persistence_opt           2540.6   1907.9   3325.9         True

== Siting comparison: revenue per MW-year by hub ==
strategy     ORACLE  S0_no_battery  S1_fixed_windows  S2_yesterday_rule  S3_persistence_opt  S4_weekly_naive_opt  S5_avg7_opt
hub                                                                                                                          
HB_BUSAVG   40147.0            0.0           26775.0            26539.0             33816.0              32071.0      35013.0
HB_HOUSTON  38348.0            0.0           23850.0            23662.0             30623.0              29119.0      32932.0
HB_NORTH    40968.0            0.0           27207.0            26783.0             34555.0              32611.0      35345.0
HB_PAN      42398.0            0.0           25504.0            20152.0             31095.0              28849.0      33747.0
HB_SOUTH    38188.0            0.0           25334.0            25298.0             32024.0              29767.0      33184.0
HB_WEST     45706.0            0.0           31017.0            25510.0             36433.0              34902.0      39140.0

Files written to data/results
```

---

## 4. Sensitivity Run (`python3 battery_backtest.py backtest --duration 4 --rte 0.88 --degr 3`)

```text
===== HB_NORTH | 100 MW x 4 h | RTE 88% | degr $3.0/MWh =====
days used: 1030 (2024-01-08 -> 2026-11-02) of 1039 days in data
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            36364.619              0.596           0.533            0.651             0.553              0.708           0.880             0.181          NaN
  S2_yesterday_rule            38336.694              0.629           0.532            0.713             0.640              0.601           0.761             0.163          NaN
 S3_persistence_opt            49383.345              0.810           0.743            0.864             0.829              0.762           0.971             0.166        0.607
S4_weekly_naive_opt            48271.711              0.792           0.744            0.833             0.812              0.739           0.971             0.148        0.568
        S5_avg7_opt            52278.856              0.858           0.792            0.902             0.880              0.800           0.972             0.162        0.661
             ORACLE            60965.102              1.000           1.000            1.000             1.000              1.000           0.971             0.164          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain   ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt         -13529.7 -15586.3 -11809.0         True
   S1_fixed_windows S3_persistence_opt          -3566.8  -4690.8  -2521.7         True
  S2_yesterday_rule S3_persistence_opt          -3026.5  -4335.9  -2174.7         True
S4_weekly_naive_opt S3_persistence_opt           -304.6   -965.0    406.5        False
        S5_avg7_opt S3_persistence_opt            793.3    489.7   1171.2         True
             ORACLE S3_persistence_opt           3173.1   2109.9   4685.4         True

Files written to data/results
```


---

## 5. Forecast Pipeline Build (`python3 forecast_pipeline.py build`)

```text
== BUILD ==
  [dedupe   ] dam_spp_hubs                 149,472 ->   149,472  keep last pull per (hour, hub)
  [gaps     ] dam_spp_hubs                  24,912 ->    24,912  0 missing hours flagged as NaN, prices are never interpolated
  [leak-drop] load_forecast_asof            24,912 ->    24,912  drop rows published after the D-1 09:45 US-Central cutoff
  [dedupe   ] load_forecast_asof            24,912 ->    24,912  one row per target hour: latest publication before the cutoff
  [columns  ] load_forecast_asof                 9 ->         9  dropped mostly-empty columns: []
  [align    ] load_forecast_asof            24,912 ->    24,906  aligned to the hourly UTC grid; gaps stay NaN (no interpolation)
  [leak-drop] wind_forecast_asof            24,912 ->    24,912  drop rows published after the D-1 09:45 US-Central cutoff
  [dedupe   ] wind_forecast_asof            24,912 ->    24,912  one row per target hour: latest publication before the cutoff
  [columns  ] wind_forecast_asof                18 ->        18  dropped mostly-empty columns: []
  [align    ] wind_forecast_asof            24,912 ->    24,906  aligned to the hourly UTC grid; gaps stay NaN (no interpolation)
  [leak-drop] solar_forecast_asof           24,912 ->    24,912  drop rows published after the D-1 09:45 US-Central cutoff
  [dedupe   ] solar_forecast_asof           24,912 ->    24,912  one row per target hour: latest publication before the cutoff
  [columns  ] solar_forecast_asof               21 ->        21  dropped mostly-empty columns: []
  [align    ] solar_forecast_asof           24,912 ->    24,906  aligned to the hourly UTC grid; gaps stay NaN (no interpolation)
  [actuals  ] fuel_mix+load                 24,912 ->    24,861  actual generation/load used only at lag >= 2 days
  [weather  ] weather_fcst                  24,912 ->    24,444  30 columns; leads=[2]; forecasts valid from 2024-01-20 12:00:00+00:00; NaN before that is kept

== LEAKAGE TESTS ==
  load_forecast_asof_published_after_cutoff: 0
  wind_forecast_asof_published_after_cutoff: 0
  solar_forecast_asof_published_after_cutoff: 0
  T2_unlisted_feature_columns: []
  T4_actual_columns_in_features: []
  T3_lag_spotcheck_passed_of_5: 5

Feature groups, average NaN share: {'cal_': 0.0, 'lf_': 0.0002, 'wind_': 0.0002, 'solar_': 0.0003, 'nl_': 0.0003, 'wx_': 0.0268, 'actlag2d_': 0.002}
Wrote data/processed/model_table.parquet (24,912 rows, 96 features), data/reports/cleaning_log.csv, build_report.json
All leakage tests passed.
```

---

## 6. Forecast Pipeline Train (`python3 forecast_pipeline.py train --hub HB_NORTH`)

```text
== Forecast accuracy on 855 identical test days (HB_NORTH), prices in $/MWh ==
       model    MAE    RMSE  MAE_asinh  kendall_tau  MAE_pre_RTCB  MAE_post_RTCB  DM_vs_avg7_stat  DM_vs_avg7_p
 persistence 12.201  50.249      0.148        0.619        10.481         14.899           -0.632         0.528
weekly_naive 16.415  65.063      0.200        0.578        13.259         21.364            3.871         0.000
        avg7 12.737  48.060      0.157        0.673        10.297         16.563              NaN           NaN
         gbm  6.917  29.047      0.084        0.801         6.410          7.713           -3.845         0.000
       lasso 14.423 526.515      0.133        0.632         9.055         22.839            0.443         0.658
         ens  7.974  55.075      0.095        0.751         6.589         10.145           -4.278         0.000

DM stat < 0 and p < 0.05 means significantly better than the 7-day average baseline.
```

---

## 7. Downstream Backtest with ML Forecasts

Command:
```bash
python3 battery_backtest.py backtest --hub HB_NORTH \
    --extra-forecast gbm=data/processed/forecast_gbm_HB_NORTH.parquet \
    --extra-forecast lasso=data/processed/forecast_lasso_HB_NORTH.parquet \
    --extra-forecast ens=data/processed/forecast_ens_HB_NORTH.parquet
```

```text
===== HB_NORTH | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
  extra forecast 'gbm': covers 856 days
  extra forecast 'lasso': covers 856 days
  extra forecast 'ens': covers 856 days
days used: 855 (2024-07-01 -> 2026-11-02) of 1039 days in data
NOTE: 15 optimizer solves used simultaneous charge+discharge (happens with negative prices). Re-run with --no-simul to forbid it and compare.
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            24381.095              0.669           0.616            0.719             0.648              0.708           0.850             0.136          NaN
  S2_yesterday_rule            24322.981              0.667           0.597            0.731             0.691              0.621           0.735             0.141          NaN
 S3_persistence_opt            30561.716              0.838           0.780            0.887             0.863              0.790           0.976             0.119        0.619
S4_weekly_naive_opt            29561.872              0.811           0.758            0.855             0.863              0.711           0.976             0.131        0.577
        S5_avg7_opt            31190.906              0.855           0.765            0.917             0.894              0.781           0.979             0.122        0.673
         S6_gbm_opt            33970.124              0.932           0.889            0.961             0.955              0.888           0.979             0.113        0.801
       S6_lasso_opt            29245.182              0.802           0.725            0.865             0.816              0.776           0.997             0.117        0.632
         S6_ens_opt            32780.890              0.899           0.844            0.942             0.921              0.857           0.992             0.111        0.751
             ORACLE            36462.997              1.000           1.000            1.000             1.000              1.000           0.976             0.130          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain  ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -8373.1 -9475.7  -7402.6         True
   S1_fixed_windows S3_persistence_opt          -1693.3 -2058.7  -1355.2         True
  S2_yesterday_rule S3_persistence_opt          -1709.2 -2038.9  -1415.7         True
S4_weekly_naive_opt S3_persistence_opt           -273.9  -605.4    140.6        False
        S5_avg7_opt S3_persistence_opt            172.4  -278.4    451.7        False
         S6_gbm_opt S3_persistence_opt            933.8   680.4   1238.6         True
       S6_lasso_opt S3_persistence_opt           -360.7  -675.8   -117.5         True
         S6_ens_opt S3_persistence_opt            608.0   460.2    783.6         True
             ORACLE S3_persistence_opt           1616.8  1087.3   2411.5         True

Files written to data/results
```


---

## 8. Updated Forecast Pipeline Training (with Lasso Regularization/Clipping)

Command:
```bash
python3 forecast_pipeline.py train --hub HB_NORTH
```

```text
== Forecast accuracy on 855 identical test days (HB_NORTH), prices in $/MWh ==
       model    MAE   RMSE  MAE_asinh  worst_abs_error  kendall_tau  MAE_pre_RTCB  MAE_post_RTCB  DM_vs_avg7_stat  DM_vs_avg7_p
 persistence 12.201 50.249      0.148         1627.350        0.619        10.481         14.899           -0.632         0.528
weekly_naive 16.415 65.063      0.200         1866.340        0.578        13.259         21.364            3.871         0.000
        avg7 12.737 48.060      0.157         1775.854        0.673        10.297         16.563              NaN           NaN
         gbm  6.930 28.379      0.084         1240.089        0.800         6.437          7.703           -3.798         0.000
       lasso 10.667 45.814      0.140         1874.809        0.633         9.405         12.644           -2.011         0.044
         ens  7.970 40.052      0.098         1701.552        0.753         6.693          9.971           -4.136         0.000

DM stat < 0 and p < 0.05 means significantly better than the 7-day average baseline.
```

---

## 9. Updated Backtest with New Forecast Models

Command:
```bash
python3 battery_backtest.py backtest --hub HB_NORTH \
    --extra-forecast gbm=data/processed/forecast_gbm_HB_NORTH.parquet \
    --extra-forecast lasso=data/processed/forecast_lasso_HB_NORTH.parquet \
    --extra-forecast ens=data/processed/forecast_ens_HB_NORTH.parquet
```

```text
===== HB_NORTH | 100 MW x 2 h | RTE 85% | degr $0.0/MWh =====
  extra forecast 'gbm': covers 856 days
  extra forecast 'lasso': covers 856 days
  extra forecast 'ens': covers 856 days
days used: 855 (2024-07-01 -> 2026-11-02) of 1039 days in data
NOTE: 15 optimizer solves used simultaneous charge+discharge (happens with negative prices). Re-run with --no-simul to forbid it and compare.
           strategy  revenue_per_mw_year  capture_of_oracle  capture_ci_low  capture_ci_high  capture_pre_rtcb  capture_post_rtcb  cycles_per_day  top10_days_share  kendall_tau
      S0_no_battery                0.000              0.000           0.000            0.000             0.000              0.000           0.000               NaN          NaN
   S1_fixed_windows            24381.095              0.669           0.616            0.719             0.648              0.708           0.850             0.136          NaN
  S2_yesterday_rule            24322.981              0.667           0.597            0.731             0.691              0.621           0.735             0.141          NaN
 S3_persistence_opt            30561.716              0.838           0.780            0.887             0.863              0.790           0.976             0.119        0.619
S4_weekly_naive_opt            29561.872              0.811           0.758            0.855             0.863              0.711           0.976             0.131        0.577
        S5_avg7_opt            31190.906              0.855           0.765            0.917             0.894              0.781           0.979             0.122        0.673
         S6_gbm_opt            33931.357              0.931           0.887            0.960             0.953              0.888           0.979             0.113        0.800
       S6_lasso_opt            29574.001              0.811           0.738            0.871             0.828              0.779           1.000             0.119        0.633
         S6_ens_opt            32998.709              0.905           0.853            0.946             0.925              0.867           0.996             0.110        0.753
             ORACLE            36462.997              1.000           1.000            1.000             1.000              1.000           0.976             0.130          NaN

Paired daily revenue gain vs S3 persistence optimizer ($/day, 95% block-bootstrap CI):
           strategy          reference  mean_daily_gain  ci_low  ci_high  significant
      S0_no_battery S3_persistence_opt          -8373.1 -9475.7  -7402.6         True
   S1_fixed_windows S3_persistence_opt          -1693.3 -2058.7  -1355.2         True
  S2_yesterday_rule S3_persistence_opt          -1709.2 -2038.9  -1415.7         True
S4_weekly_naive_opt S3_persistence_opt           -273.9  -605.4    140.6        False
        S5_avg7_opt S3_persistence_opt            172.4  -278.4    451.7        False
         S6_gbm_opt S3_persistence_opt            923.2   674.0   1229.9         True
       S6_lasso_opt S3_persistence_opt           -270.6  -566.8    -20.9         True
         S6_ens_opt S3_persistence_opt            667.7   507.6    863.2         True
             ORACLE S3_persistence_opt           1616.8  1087.3   2411.5         True
```

---

## 10. Feature Ablation & Placebo Study (`python3 forecast_pipeline.py ablate --hub HB_NORTH`)

```text
== ABLATION (HB_NORTH, GBM, retrain every 28 days) ==
                      variant  features    MAE   RMSE  kendall_tau  capture_of_oracle  capture_change_vs_full
                         full       106  8.000 34.269        0.799              0.930                   0.000
     prices_and_calendar_only        18 10.711 43.629        0.666              0.877                  -0.053
             no_load_forecast        92  7.893 36.753        0.762              0.922                  -0.008
             no_wind_forecast        83  8.610 37.896        0.735              0.890                  -0.040
            no_solar_forecast        80  7.873 34.950        0.759              0.916                  -0.014
         no_net_load_features       101  7.578 33.389        0.771              0.920                  -0.010
                   no_weather        76  8.268 35.748        0.796              0.930                  -0.000
               no_actual_lags       101  8.151 36.135        0.798              0.927                  -0.003
PLACEBO_shuffled_fundamentals       106 10.597 43.518        0.660              0.854                  -0.076
```
