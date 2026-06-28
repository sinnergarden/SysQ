#!/usr/bin/env python3
"""60d ablation — IC decomposition (original / within-industry / ind-alloc) + TopK quality."""
import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
import pandas as pd, numpy as np
from qsys.signal.store import SignalStore
from qsys.label.store import LabelStore
from qlib.data import D
from qsys.data.adapter import QlibAdapter

QlibAdapter().init_qlib()
al = LabelStore().load_labels("fwd_ret_60d_raw")
al["trade_date"] = al["trade_date"].astype(str).str[:10]; al["ts_code"] = al["instrument"]

# Industry
ind_raw = D.features(D.instruments("csi800"), ["$industry"], start_time="2024-01-01", end_time="2024-01-02", freq="day")
ind_map = ind_raw.reset_index()[["instrument", "$industry"]].drop_duplicates().rename(columns={"instrument": "ts_code", "$industry": "industry"})

runs = [
    ("pure (90)", "rolling__60d_v3a_plus_liquidity_pure__v3a_liq_pure_60d__fwd_ret_60d_raw__daily_zscore__2020-01-01_2025-12-31"),
    ("w/ind (92)", "rolling__60d_v3a_plus_liquidity_indadj__v3a_liq_indadj_60d__fwd_ret_60d_raw__daily_zscore__2020-01-01_2025-12-31"),
    ("+financial (96)", "rolling__60d_v3a_growth_financial__v3a_growth_financial_60d__fwd_ret_60d_raw__daily_zscore__2020-01-01_2025-12-31"),
]
s = SignalStore(); sid = "fwd_ret_60d_raw__daily_zscore"

def daily_ic_stats(df):
    """Original, within-industry, ind-alloc IC/RankIC."""
    df = df.dropna(subset=["label_value"]).copy()
    if len(df) < 100: return {}
    # Original
    od = df.groupby("trade_date").apply(lambda g: pd.Series({"ic":g["score"].corr(g["label_value"]),"rk":g["score"].rank().corr(g["label_value"].rank())}), include_groups=False).dropna()
    # Within-industry (rank pct within date+industry)
    df2 = df.copy().merge(ind_map, on="ts_code", how="left").dropna(subset=["industry"])
    if len(df2) > 100:
        df2["ws"] = df2.groupby(["trade_date", "industry"], group_keys=False)["score"].rank(pct=True)
        wd = df2.groupby("trade_date").apply(lambda g: pd.Series({"ic":g["ws"].corr(g["label_value"]),"rk":g["ws"].rank().corr(g["label_value"].rank())}), include_groups=False).dropna()
    else: wd = od * np.nan
    # Ind-alloc
    ia = df2.groupby(["trade_date","industry"]).agg(ms=("score","mean"),ml=("label_value","mean")).reset_index()
    ad = ia.groupby("trade_date").apply(lambda g: pd.Series({"ic":g["ms"].corr(g["ml"]),"rk":g["ms"].rank().corr(g["ml"].rank())}), include_groups=False).dropna()
    return {"orig":od, "within":wd, "alloc":ad}

def topk_stats(df):
    df = df.dropna(subset=["label_value"]).copy()
    if len(df) < 100: return {}
    res = {}
    for y in ["2020","2021","2022","2023","2024","2025"]:
        yr = df[df["trade_date"].str[:4]==y]
        if len(yr)<200: continue
        ds = sorted(yr["trade_date"].unique())
        for k in [20,50,100]:
            v = pd.concat([yr[yr["trade_date"]==dt].sort_values("score",ascending=False).head(k)["label_value"] for dt in ds]).dropna()
            if len(v)>0:
                res[f"t{k}_{y}"] = v.mean()
                res[f"h{k}_{y}"] = (v>0).mean()
                res[f"b{k}_{y}"] = (v<0).mean()
                res[f"g{k}_{y}"] = (v>0.3).mean()
    return res

for name, rid in runs:
    sig = s.load_signal_run(sid, rid)
    sig["ts_code"] = sig["instrument"]
    df = sig[["trade_date","ts_code","score"]].merge(al[["trade_date","ts_code","label_value"]],on=["trade_date","ts_code"],how="left")

    ic = daily_ic_stats(df)
    tk = topk_stats(df)

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print(f"  {'Method':<20s} {'IC':>7s} {'ICIR':>7s} {'RankIC':>7s} {'RkICIR':>7s}")
    print(f"  {'-'*56}")
    for method, label in [("orig","Original"),("within","Within-ind"),("alloc","Ind-alloc")]:
        d = ic.get(method)
        if d is not None and len(d)>0:
            print(f"  {label:<20s} {d['ic'].mean():>7.4f} {d['ic'].mean()/d['ic'].std() if d['ic'].std()>0 else 0:>7.3f} {d['rk'].mean():>7.4f} {d['rk'].mean()/d['rk'].std() if d['rk'].std()>0 else 0:>7.3f}")
        else:
            print(f"  {label:<20s} {'N/A':>7s}")

    # Yearly IC decomposition
    print(f"\n  {'─'*30} Yearly IC ──")
    print(f"  {'Year':>6s} {'OrigIC':>8s} {'OrigIR':>8s} {'WthnIC':>8s} {'WthnIR':>8s} {'AlocIC':>8s} {'AlocIR':>8s}")
    for y in ["2020","2021","2022","2023","2024","2025"]:
        line = f"  {y:>6s}"
        for method, label in [("orig","ic"),("within","ic"),("alloc","ic")]:
            d = ic.get(method)
            if d is not None and len(d)>0:
                ds = d.reset_index()
                ds["_y"] = ds["trade_date"].str[:4]
                yd = ds[ds["_y"]==y]
                if len(yd)>5:
                    line += f"  {yd['ic'].mean():>8.4f}"
                    line += f"  {yd['ic'].mean()/yd['ic'].std() if yd['ic'].std()>0 else 0:>8.3f}"
                else:
                    line += f"  {'N/A':>8s}  {'N/A':>8s}"
        print(line)
        sys.stdout.flush()

    # TopK
    print(f"\n  {'─'*30} TopK ──")
    for k in [20,50,100]:
        print(f"  Top{k}:")
        print(f"  {'Year':>6s} {'Mean':>7s} {'Hit':>7s} {'Bad':>7s} {'>30%':>7s}")
        for y in ["2020","2021","2022","2023","2024","2025"]:
            mn = tk.get(f"t{k}_{y}"); ht = tk.get(f"h{k}_{y}"); bd = tk.get(f"b{k}_{y}"); g3 = tk.get(f"g{k}_{y}")
            if mn: print(f"  {y:>6s} {mn:>7.4f} {ht:>7.2%} {bd:>7.2%} {g3:>7.2%}")

    sys.stdout.flush()
