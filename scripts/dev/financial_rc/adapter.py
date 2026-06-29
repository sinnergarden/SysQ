"""Serving adapter for financial_rc — UC-10A daily candidate export.

Loads eligible pretrained boosters only. No training.
Training must be done via UC-4 / daily_retrain / weekly_retrain first.

SysQ outputs outputs/{trade_date}/candidates.json only.
SysA owns task.json and LLM research.
"""
from __future__ import annotations

import hashlib, json
from datetime import timezone, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _eligible_model(
    experiment_id: str, label_id: str, maturity_days: int,
    trade_date: str, feature_list_id: str, model_root: Path,
) -> Path | None:
    """Find best eligible model: latest train_end within maturity guard.

    Selection:
    1. artifact complete (model.txt, center.json, scale.json, meta.json)
    2. feature_list_id matches
    3. label_id matches
    4. train_end <= trade_date - maturity_days (trading days)
    5. among eligible, pick newest train_end
    """
    base = model_root / experiment_id
    if not base.exists():
        return None

    # Calendar for maturity check
    from qlib.data import D
    from qsys.data.adapter import QlibAdapter
    QlibAdapter().init_qlib()
    cal = [str(c)[:10] for c in D.calendar(end_time=max(trade_date, "2026-06-30"), freq="day")]
    td_idx = cal.index(trade_date) if trade_date in cal else len(cal) - 1
    maturity_idx = max(0, td_idx - maturity_days)
    latest_allowed = cal[maturity_idx]

    best = None
    for wd in sorted(base.iterdir()):
        if not wd.is_dir():
            continue
        req = [wd / f for f in ("model.txt", "center.json", "scale.json", "meta.json")]
        if not all(p.exists() for p in req):
            continue
        try:
            m = json.loads((wd / "meta.json").read_text())
        except Exception:
            continue
        if m.get("feature_list_id") != feature_list_id:
            continue
        if m.get("label_id") != label_id:
            continue
        te = m.get("train_end", "")
        if te > latest_allowed:
            continue
        if best is None or te > best[0]:
            best = (te, wd)
    return best[1] if best else None


class FinancialRCAdapter:
    """Serving adapter — loads eligible boosters, predicts, exports candidates."""

    FEATURE_LIST = "v3a_plus_liquidity_financial_rc"
    MODEL_ROOT = _PROJECT_ROOT / "data/research/models"

    # (experiment_id_exp, label_id, maturity_days, tag)
    MODEL_SPECS = [
        ("60d_v3a_growth_financial", "fwd_ret_60d_raw", 60, "60d"),
        ("180d_v3a_growth_financial", "fwd_ret_180d_raw", 180, "180d"),
    ]

    def __init__(self):
        self._features: list = []
        self._model_dirs: dict = {}
        self._model_tags: list[str] = []

    def load_model(self, trade_date: str) -> None:
        import pandas as pd
        from qsys.data.adapter import QlibAdapter
        from qsys.feature.registry import FeatureListRegistry

        QlibAdapter().init_qlib()
        self._features = FeatureListRegistry.load(self.FEATURE_LIST)
        print(f"  Features: {len(self._features)}")

        for exp_id, label_id, maturity, tag in self.MODEL_SPECS:
            md = _eligible_model(exp_id, label_id, maturity, trade_date,
                                  self.FEATURE_LIST, self.MODEL_ROOT)
            if md is None:
                raise FileNotFoundError(
                    f"No eligible pretrained booster for {tag} ({exp_id}). "
                    f"Run UC-4 / daily_retrain / weekly_retrain first."
                )
            print(f"  Model {tag}: {md.name}")
            self._model_dirs[tag] = md
            self._model_tags.append(tag)

    def predict(self, trade_date: str, top_k: int = 5,
                w60: float = 0.3, w180: float = 0.7) -> dict:
        import numpy as np
        import pandas as pd
        import lightgbm as lgb
        import shap
        from qsys.data.adapter import QlibAdapter
        from qsys.signal.alpha_v1.labels import robust_zscore_transform

        QlibAdapter().init_qlib()

        raw = QlibAdapter().get_features("csi800", self._features + ["$close"],
                                          start_time=trade_date, end_time=trade_date)
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        frame["ts_code"] = frame["instrument"]
        frame = frame.sort_values("ts_code").reset_index(drop=True)
        print(f"  Features: {len(frame)} stocks")

        scores = {}
        shaps = {}
        for tag in self._model_tags:
            md = self._model_dirs[tag]
            model = lgb.Booster(model_file=str(md / "model.txt"))
            center = pd.read_json(md / "center.json", typ="series")
            scale = pd.read_json(md / "scale.json", typ="series")
            Xp = frame[self._features].fillna(0).astype(np.float32)
            Xp_z = robust_zscore_transform(Xp, center, scale)
            pred = model.predict(Xp_z.values)
            pred_z = (pred - pred.mean()) / max(pred.std(), 1e-8)
            scores[tag] = pd.Series(pred_z, index=frame["ts_code"])

            explainer = shap.TreeExplainer(model, data=Xp_z[:100])
            sv = explainer.shap_values(Xp_z)
            sd = {}
            for i, code in enumerate(frame["ts_code"]):
                vals = {}
                idxs = np.argsort(np.abs(sv[i]))[::-1][:15]
                for j in idxs:
                    vals[self._features[j]] = round(float(sv[i][j]), 4)
                sd[code] = vals
            stats = {}
            for feat in self._features:
                arr = sv[:, self._features.index(feat)]
                stats[feat] = {"min": round(float(arr.min()), 4), "max": round(float(arr.max()), 4),
                               "mean": round(float(arr.mean()), 4), "p50": round(float(np.median(arr)), 4),
                               "p75": round(float(np.percentile(arr, 75)), 4),
                               "p90": round(float(np.percentile(arr, 90)), 4)}
            shaps[tag] = (sd, stats)

        ranking = w60 * scores.get("60d", pd.Series(0)) + w180 * scores.get("180d", pd.Series(0))
        ranking = ranking.sort_values(ascending=False).reset_index()
        ranking.columns = ["ts_code", "ranking_score"]
        ranking["rank"] = ranking.index + 1
        topk = ranking.head(top_k)

        tb = pd.read_parquet(str(_PROJECT_ROOT / "data/tushare/stock_basic.parquet"))
        tb["ck"] = tb["ts_code"].str.replace(".", "", regex=False)
        nm = dict(zip(tb["ck"], tb["name"]))
        im = dict(zip(tb["ck"], tb["industry"]))

        tz_cn = timezone(timedelta(hours=8))
        gen_ts = pd.Timestamp.now(tz_cn).strftime("%Y-%m-%dT%H:%M:%S%z")
        gen_ts = gen_ts[:-2] + ":" + gen_ts[-2:]

        candidates = []
        for _, r in topk.iterrows():
            ck = r["ts_code"].replace(".", ""); cd = r["ts_code"]
            sd60, st60 = shaps.get("60d", ({}, {}))
            sd180, st180 = shaps.get("180d", ({}, {}))
            sh60v = sd60.get(cd, {}); sh180v = sd180.get(cd, {})
            us60 = {f: st60[f] for f in sh60v if f in st60}
            us180 = {f: st180[f] for f in sh180v if f in st180}
            candidates.append({
                "ts_code": cd, "name": nm.get(ck), "industry": im.get(ck),
                "rank": int(r["rank"]), "ranking_score": round(float(r["ranking_score"]), 4),
                "ranking_weight": [w60, w180],
                "ranking_note": f"{w60}*60d_return+{w180}*180d_return",
                "models": [
                    {"name": "60d_return", "horizon": "60d", "target": "return",
                     "weight": w60, "score": round(float(scores.get("60d", pd.Series()).loc[cd]) if cd in scores.get("60d", pd.Series()).index else 0, 4),
                     "feature_contrib": {"method": "shap", "values": sh60v, "universe_stats": us60}},
                    {"name": "180d_return", "horizon": "180d", "target": "return",
                     "weight": w180, "score": round(float(scores.get("180d", pd.Series()).loc[cd]) if cd in scores.get("180d", pd.Series()).index else 0, 4),
                     "feature_contrib": {"method": "shap", "values": sh180v, "universe_stats": us180}},
                ]})

        rs = ranking["ranking_score"]
        rss = {k: round(float(getattr(rs, k)()), 4) for k in ["min", "max", "mean", "median"]}
        rss["p75"] = round(float(rs.quantile(0.75)), 4); rss["p90"] = round(float(rs.quantile(0.90)), 4)

        return {
            "trade_date": trade_date, "generated_at": gen_ts, "universe": "csi800",
            "top_k": top_k, "score_source": "model", "score_transform": "daily_cs_zscore",
            "ranking": {"weights": {"60d_return": w60, "180d_return": w180},
                        "note": f"{w60}*60d_return+{w180}*180d_return",
                        "ranking_score_stats": rss},
            "source": {"feature_list_id": self.FEATURE_LIST},
            "candidates": candidates,
        }
