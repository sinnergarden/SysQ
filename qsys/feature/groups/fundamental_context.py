from __future__ import annotations

import numpy as np
import pandas as pd

from qsys.feature.transforms import PIT_CROSS_SECTION_COLUMN, cross_section_transform


def build_fundamental_context_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if "total_mv" in out.columns:
        out["log_mktcap"] = np.log(out["total_mv"].replace(0, np.nan))
    if "circ_mv" in out.columns:
        out["float_mktcap"] = out["circ_mv"]
    if "pe" in out.columns:
        out["pe_ttm"] = out["pe"]
    if "pb" in out.columns:
        out["pb_raw"] = out["pb"]
    if "ps_ttm" not in out.columns and "ps" in out.columns:
        out["ps_ttm"] = out["ps"]

    if {"net_income", "revenue"}.issubset(out.columns):
        out["net_margin"] = out["net_income"] / out["revenue"].replace(0, np.nan)
    if {"op_cashflow", "net_income"}.issubset(out.columns):
        out["operating_cf_to_profit"] = out["op_cashflow"] / out["net_income"].replace(0, np.nan)
    if {"net_income", "total_assets"}.issubset(out.columns):
        out["roa"] = out["net_income"] / out["total_assets"].replace(0, np.nan)

    if "grossprofit_margin" in out.columns and "gross_margin" not in out.columns:
        out["gross_margin"] = out["grossprofit_margin"]
    if "debt_to_assets" in out.columns and "debt_to_asset" not in out.columns:
        out["debt_to_asset"] = out["debt_to_assets"]

    for base_col, new_col in [
        ("revenue", "revenue_yoy"),
        ("net_income", "profit_yoy"),
    ]:
        if base_col in out.columns:
            prev = out.groupby("ts_code")[base_col].shift(252)
            out[new_col] = out[base_col] / prev.replace(0, np.nan) - 1

    if "inventory" in out.columns:
        prev = out.groupby("ts_code")["inventory"].shift(252)
        out["inventory_yoy"] = out["inventory"] / prev.replace(0, np.nan) - 1
    if "accounts_receiv" in out.columns:
        prev = out.groupby("ts_code")["accounts_receiv"].shift(252)
        out["ar_yoy"] = out["accounts_receiv"] / prev.replace(0, np.nan) - 1

    # ── Value-growth specific: fundamental improvement (252d delta) ──
    # NOTE: 252d shift is a calendar-day approximation of 4 quarters, NOT
    # a strict fiscal-period (同比) comparison. Actual fiscal YoY requires
    # aligning by end_date via ann_date merge_asof — this does not do that.
    # Use for directional signal only; do not claim exact fiscal changes.
    _delta_pairs = [
        ("roe", "roe_delta_252d"),
        ("grossprofit_margin", "grossprofit_margin_delta_252d"),
        ("debt_to_assets", "debt_to_assets_delta_252d"),
        ("op_cashflow", "op_cashflow_delta_252d"),
    ]
    for src, dst in _delta_pairs:
        if src in out.columns:
            prev = out.groupby("ts_code")[src].shift(252)
            out[dst] = out[src] - prev

    # ── Valuation percentiles (current value's rank in own 252d history) ──
    _val_rank_pairs = [
        ("pe", "pe_rank_252d"),
        ("pb", "pb_rank_252d"),
        ("ps_ttm", "ps_rank_252d"),
    ]
    for src, dst in _val_rank_pairs:
        if src in out.columns:
            out[dst] = out.groupby("ts_code")[src].transform(
                lambda s: s.rolling(252, min_periods=60).rank(pct=True)
            )

    # ── Valuation deltas ──
    if "pe" in out.columns:
        prev_pe = out.groupby("ts_code")["pe"].shift(120)
        out["pe_delta_120d"] = out["pe"] - prev_pe
    if "pb" in out.columns:
        prev_pb = out.groupby("ts_code")["pb"].shift(120)
        out["pb_delta_120d"] = out["pb"] - prev_pb

    # ═══════════════════════════════════════════════════════════════
    # v2: valuation_repair_setup
    # ═══════════════════════════════════════════════════════════════

    # 3-year valuation percentiles
    for src, dst in [("pe", "pe_percentile_756d"), ("pb", "pb_percentile_756d")]:
        if src in out.columns:
            out[dst] = out.groupby("ts_code")[src].transform(
                lambda s: s.rolling(756, min_periods=180).rank(pct=True)
            )

    # Valuation distance from 3-year low (pe_distance_from_756d_low = 0 at low, >0 above low)
    # Repair room vs historical median (>0 means median is above current — room to repair up)
    if "pe" in out.columns:
        _pe_min_756 = out.groupby("ts_code")["pe"].transform(
            lambda s: s.rolling(756, min_periods=180).min()
        )
        out["pe_distance_from_756d_low"] = out["pe"] / _pe_min_756.replace(0, np.nan) - 1
        _pe_med_756 = out.groupby("ts_code")["pe"].transform(
            lambda s: s.rolling(756, min_periods=180).median()
        )
        out["pe_repair_room_to_median"] = _pe_med_756 / out["pe"].replace(0, np.nan) - 1
    if "pb" in out.columns:
        _pb_min_756 = out.groupby("ts_code")["pb"].transform(
            lambda s: s.rolling(756, min_periods=180).min()
        )
        out["pb_distance_from_756d_low"] = out["pb"] / _pb_min_756.replace(0, np.nan) - 1
        _pb_med_756 = out.groupby("ts_code")["pb"].transform(
            lambda s: s.rolling(756, min_periods=180).median()
        )
        out["pb_repair_room_to_median"] = _pb_med_756 / out["pb"].replace(0, np.nan) - 1

    # Earnings yield: net_income / total_mv
    if {"net_income", "total_mv"}.issubset(out.columns):
        out["earnings_yield_proxy"] = out["net_income"] / out["total_mv"].replace(0, np.nan)

    # PEG proxy: PE / profit_yoy
    if "pe" in out.columns and "profit_yoy" in out.columns:
        _growth = out["profit_yoy"].replace(0, np.nan).abs()
        out["peg_proxy"] = out["pe"] / _growth

    # ═══════════════════════════════════════════════════════════════
    # v2: fundamental_acceleration_quality
    # ═══════════════════════════════════════════════════════════════

    # YoY acceleration
    for src, dst in [("revenue_yoy", "revenue_yoy_accel"), ("profit_yoy", "profit_yoy_accel")]:
        if src in out.columns:
            prev = out.groupby("ts_code")[src].shift(252)
            out[dst] = out[src] - prev

    # 4-quarter deltas (756d approx)
    for src, dst in [("roe", "roe_delta_756d"), ("net_margin", "net_margin_delta_756d")]:
        if src in out.columns:
            prev = out.groupby("ts_code")[src].shift(756)
            out[dst] = out[src] - prev

    # OCF margin
    if {"op_cashflow", "revenue"}.issubset(out.columns):
        out["ocf_margin"] = out["op_cashflow"] / out["revenue"].replace(0, np.nan)

    # ═══════════════════════════════════════════════════════════════
    # v2: path_classifier_scores
    # Note: depend on features from BOTH builders; silently NaN
    # if called standalone without relative_strength running first.
    # ═══════════════════════════════════════════════════════════════

    _ts60 = out.get("trend_smoothness_60d", None)
    _rps120 = out.get("rps_120d", None)
    _rps20 = out.get("rps_20d", None)
    _pp252 = out.get("price_percentile_252d", None)

    if all(x is not None for x in [_ts60, _rps120, _rps20, _pp252]):
        # BUGFIX: percent_rank of trend_smoothness must be per trade_date
        # (cross-sectional rank), not across all dates × stocks.
        cont = (cross_section_transform(
                    out,
                    "trend_smoothness_60d",
                    "trade_date",
                    lambda s: s.fillna(0).clip(-1, 1).rank(pct=True),
                ) * 1.0
                + _rps120.fillna(0) * 1.0
                + _pp252.fillna(0) * 0.5
                + (1 - _rps20.fillna(0)) * 0.3)
        out["continuation_candidate_score"] = cont / (1.0 + 1.0 + 0.5 + 0.3)

    _pepct = out.get("pe_percentile_756d", None)
    _d2l = out.get("distance_to_252d_low", None)
    _pct252 = out.get("price_percentile_252d", None)
    if all(x is not None for x in [_pepct, _d2l, _pct252]):
        # Components:
        # - low_val_penalty:  higher when pe at low percentile (1-percentile)
        # - near_low_bonus:   higher when close to 252d low (d2l near 0)
        #   Example: d2l=0 (at low) → near_low_bonus=0.5; d2l=1 → near_low_bonus=0
        low_val_penalty = (1 - _pepct.fillna(0.5)) * 1.0
        near_low_bonus = (1 - _d2l.fillna(0).clip(0, 1)) * 0.5
        score = (low_val_penalty + near_low_bonus
                 + (_rps120.fillna(0) if _rps120 is not None else 0) * 0.3)
        out["repair_candidate_score"] = score / (1.0 + 0.5 + 0.3)

    _pp252 = out.get("price_percentile_252d", None)
    _rps120 = out.get("rps_120d", None)
    _vs20 = out.get("volume_spike_20d", None)
    if all(x is not None for x in [_pp252, _rps120]):
        heat = (_pp252.fillna(0) * 0.4 + _rps120.fillna(0) * 0.4
                + (_vs20.fillna(0).clip(0, 3) / 3 if _vs20 is not None else 0) * 0.2)
        out["overheat_risk_score"] = heat

    _pepct = out.get("pe_percentile_756d", None)
    _pct252 = out.get("price_percentile_252d", None)
    _rn756 = out.get("roe_delta_756d", None)
    _nm756 = out.get("net_margin_delta_756d", None)
    if all(x is not None for x in [_pepct, _pct252]):
        trap = ((1 - _pepct.fillna(0.5)) * 0.3
                + (1 - _pct252.fillna(0.5)) * 0.3)
        if _rn756 is not None:
            trap += (_rn756.fillna(0) < -0.01).astype(float) * 0.2
        if _nm756 is not None:
            trap += (_nm756.fillna(0) < -0.01).astype(float) * 0.2
        out["value_trap_risk_score"] = trap

    # Clean up intermediates
    for c in list(out.columns):
        if c.startswith("_") and c != PIT_CROSS_SECTION_COLUMN:
            out = out.drop(columns=[c])

    return out
