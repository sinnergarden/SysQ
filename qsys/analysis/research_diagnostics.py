"""Generic config-driven research diagnostics engine.

Not a CLI entrypoint nor a canned strategy-specific attribution module.
The engine is config-driven — diagnostics config specifies what to
diagnose, the engine runs generic checks, and output artifacts are
written to ``experiments/<experiment_id>/diagnostics/``.

Usage (not a script)::

    python -c "
    from qsys.analysis.research_diagnostics import ResearchDiagnostics
    r = ResearchDiagnostics.from_config('configs/diagnostics/my_diagnostics.yaml')
    result = r.run()
    print(result['summary'])
    "
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.feature.registry import FeatureListRegistry
from qsys.label.store import LabelStore
from qsys.utils.logger import log


# ── Result dataclass ────────────────────────────────────────────────────


@dataclass
class CoverageResult:
    feature: str
    coverage: float = 0.0
    missing_rate: float = 0.0
    inf_rate: float = 0.0
    zero_rate: float = 0.0


@dataclass
class FeatureICResult:
    feature: str
    label_id: str
    rank_ic_mean: float | None = None
    icir: float | None = None
    positive_ic_ratio: float | None = None
    n_dates: int = 0


@dataclass
class BucketReturnResult:
    feature: str
    label_id: str
    bucket_1: float | None = None
    bucket_2: float | None = None
    bucket_3: float | None = None
    bucket_4: float | None = None
    bucket_5: float | None = None
    top_minus_bottom: float | None = None
    monotonicity_score: float | None = None


@dataclass
class CorrelationPair:
    feature_a: str
    feature_b: str
    corr: float


@dataclass
class ExposureBreakdown:
    label_id: str
    feature: str
    raw_rank_ic: float | None = None
    within_industry_rank_ic: float | None = None
    retention_ratio: float | None = None


# ── Engine ──────────────────────────────────────────────────────────────


def _resolve_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    """Resolve a column name from candidates, trying bare name then $-prefixed qlib name.

    Returns the actual column name found in *frame*, or None.
    """
    for c in candidates:
        if c in frame.columns:
            return c
        prefixed = f"${c}" if not c.startswith("$") else c
        if prefixed in frame.columns:
            return prefixed
    return None


class ResearchDiagnostics:
    """Generic config-driven research diagnostics.

    Parameters
    ----------
    config:
        Diagnostics configuration dict.
    root:
        Research root path.
    """

    def __init__(
        self,
        config: dict[str, Any],
        root: str | Path = "data/research",
    ) -> None:
        self._cfg = config
        self.root = Path(root).resolve()
        self._adapter = QlibAdapter()
        self._label_store = LabelStore(str(self.root))

        self._feature_frame: pd.DataFrame | None = None
        self._features: list[str] = []
        self._feature_meta: dict[str, bool] = {}  # feature → column exists in frame
        self._label_data: dict[str, pd.DataFrame] = {}
        self._resolved_ind_field: str | None = None
        self._resolved_size_field: str | None = None

    # ── Factory ─────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, path: str | Path, **kwargs: Any) -> ResearchDiagnostics:
        """Create diagnostics instance from YAML config file."""
        import yaml

        cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(cfg, **kwargs)

    # ── Run ─────────────────────────────────────────────────────────────

    def run(self) -> dict[str, Any]:
        """Execute all enabled diagnostics.

        Returns dict with keys: summary (str), output_dir (str),
        and per-diagnostic data paths.
        """
        self._load_data()
        enabled = self._cfg.get("diagnostics", {})
        diag_cfg = self._cfg.get("exposure", {})
        top_cfg = self._cfg.get("top_candidates", {})
        output_dir = self._output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        results: dict[str, Any] = {}

        # 1. Coverage
        if enabled.get("coverage", True):
            cov_df = self._run_coverage()
            cov_df.to_csv(output_dir / "coverage.csv", index=False)
            results["coverage"] = cov_df.to_dict("records")
            log.info("Coverage: %d features", len(cov_df))

        # 2. Feature IC
        if enabled.get("feature_ic", True):
            ic_df = self._run_feature_ic()
            ic_df.to_csv(output_dir / "feature_ic.csv", index=False)
            results["feature_ic"] = ic_df.to_dict("records")
            log.info("Feature IC: %d pairs", len(ic_df))

        # 3. Bucket return
        if enabled.get("bucket_return", True):
            br_df = self._run_bucket_return()
            br_df.to_csv(output_dir / "bucket_return.csv", index=False)
            results["bucket_return"] = br_df.to_dict("records")
            log.info("Bucket return: %d entries", len(br_df))

        # 4. Correlation
        if enabled.get("correlation", True):
            corr_df = self._run_correlation()
            corr_df.to_csv(output_dir / "correlation.csv", index=False)
            results["correlation"] = corr_df.to_dict("records")
            log.info("Correlation: %d pairs above threshold", len(corr_df))

        # 5. Exposure breakdown
        if enabled.get("exposure_breakdown", True):
            exp_df = self._run_exposure_breakdown(diag_cfg)
            exp_df.to_csv(output_dir / "exposure_breakdown.csv", index=False)
            results["exposure_breakdown"] = exp_df.to_dict("records")
            log.info("Exposure breakdown: %d entries", len(exp_df))

        # 6. Top candidate exposure
        if top_cfg.get("enabled", True):
            tc_skip = self._run_top_candidates(top_cfg, output_dir)
            results["top_candidates"] = {
                "skipped": tc_skip,
                "reason": "signal artifact unavailable; top candidate exposure skipped"
                if tc_skip
                else "",
            }

        # Summary
        summary = self._build_summary(results)
        with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        results["summary"] = summary
        results["output_dir"] = str(output_dir)

        return results

    # ── Data loading ────────────────────────────────────────────────────

    def _load_data(self) -> None:
        self._adapter.init_qlib()

        universe = self._cfg.get("universe", "csi800")
        start = self._cfg.get("start_date", "2024-06-01")
        end = self._cfg.get("end_date", "2025-12-31")
        fids = self._cfg.get("feature_list_id") or self._cfg.get("focus_features", [])

        if self._cfg.get("feature_list_id"):
            self._features = FeatureListRegistry.load(self._cfg["feature_list_id"])
        else:
            self._features = list(fids) if isinstance(fids, list) else []

        # Focus features subset (for diagnostic focus)
        focus = self._cfg.get("focus_features", [])
        all_requested = list(dict.fromkeys(self._features + focus))

        if not all_requested:
            log.warning("No features specified in config")
            return

        # Always fetch exposure fields so diagnostics can run even if
        # they are not in the feature list
        diag_cfg = self._cfg.get("exposure", {})
        extra_support = []
        for cat in ("industry_field_candidates", "size_field_candidates"):
            for f in diag_cfg.get(cat, []):
                qf = f"${f}" if not f.startswith("$") else f
                if qf not in all_requested:
                    all_requested.append(qf)
                    extra_support.append(qf)

        raw = self._adapter.get_features(
            universe,
            all_requested + ["$factor"],
            start_time=start,
            end_time=end,
        )
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        self._feature_frame = frame

        for f in all_requested:
            self._feature_meta[f] = f in frame.columns

        # Load labels
        for lcfg in self._cfg.get("labels", []):
            lid = lcfg if isinstance(lcfg, str) else lcfg.get("label_id", "")
            if not lid:
                continue
            try:
                self._label_data[lid] = self._label_store.load_labels(
                    lid, start_date=start, end_date=end,
                )
            except Exception as exc:
                log.warning("Could not load label %s: %s", lid, exc)

    # ── Coverage ────────────────────────────────────────────────────────

    def _run_coverage(self) -> pd.DataFrame:
        frame = self._feature_frame
        rows: list[CoverageResult] = []
        for feat in self._features:
            if feat not in frame.columns:
                rows.append(CoverageResult(feature=feat))
                continue
            s = pd.to_numeric(frame[feat], errors="coerce")
            n = len(s)
            n_nn = s.notna().sum()
            cov = n_nn / n if n > 0 else 0.0
            inf_mask = s.apply(
                lambda x: isinstance(x, float) and (np.isinf(x) or np.isneginf(x))
            )
            rows.append(
                CoverageResult(
                    feature=feat,
                    coverage=cov,
                    missing_rate=1.0 - cov,
                    inf_rate=int(inf_mask.sum()) / n if n > 0 else 0.0,
                    zero_rate=int(((s == 0) & s.notna()).sum()) / n if n > 0 else 0.0,
                )
            )
        return pd.DataFrame([asdict(r) for r in rows])

    # ── Feature IC ──────────────────────────────────────────────────────

    def _run_feature_ic(self) -> pd.DataFrame:
        frame = self._feature_frame
        features = self._features + self._cfg.get("focus_features", [])
        features = list(dict.fromkeys(features))
        rows: list[FeatureICResult] = []

        for feat in features:
            if feat not in frame.columns:
                continue
            for lid, ld in self._label_data.items():
                merged = pd.merge(
                    frame[["trade_date", "instrument", feat]],
                    ld[["trade_date", "instrument", "label_value"]],
                    on=["trade_date", "instrument"],
                    how="inner",
                ).dropna(subset=[feat, "label_value"])
                if len(merged) < 30:
                    continue

                daily_ic = (
                    merged.groupby("trade_date")
                    .apply(
                        lambda g: g[feat].corr(g["label_value"], method="spearman"),
                        include_groups=False,
                    )
                    .dropna()
                )
                if daily_ic.empty:
                    continue
                ic_mean = float(daily_ic.mean())
                ic_std = float(daily_ic.std(ddof=1)) if len(daily_ic) > 1 else 0.0
                icir = ic_mean / ic_std if ic_std > 1e-12 else None
                pos_ratio = float((daily_ic > 0).mean())
                rows.append(
                    FeatureICResult(
                        feature=feat,
                        label_id=lid,
                        rank_ic_mean=ic_mean,
                        icir=icir,
                        positive_ic_ratio=pos_ratio,
                        n_dates=len(daily_ic),
                    )
                )
        return pd.DataFrame([asdict(r) for r in rows])

    # ── Bucket return ───────────────────────────────────────────────────

    def _run_bucket_return(self) -> pd.DataFrame:
        frame = self._feature_frame
        features = self._features + self._cfg.get("focus_features", [])
        features = list(dict.fromkeys(features))
        rows: list[BucketReturnResult] = []

        for feat in features:
            if feat not in frame.columns:
                continue
            for lid, ld in self._label_data.items():
                merged = pd.merge(
                    frame[["trade_date", "instrument", feat]],
                    ld[["trade_date", "instrument", "label_value"]],
                    on=["trade_date", "instrument"],
                    how="inner",
                ).dropna(subset=[feat, "label_value"])
                if len(merged) < 30:
                    continue
                # Cross-sectional q5 buckets per date
                merged["_bucket"] = merged.groupby("trade_date")[feat].transform(
                    lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) + 1
                    if s.nunique() >= 5
                    else None
                )
                bucket_means = merged.groupby("_bucket")["label_value"].mean()
                if len(bucket_means) < 5:
                    continue
                vals = [float(bucket_means.get(i, None)) for i in range(1, 6)]
                tmb = vals[4] - vals[0] if all(v is not None for v in vals) else None
                # Monotonicity: fraction of adjacent comparisons that go the right direction
                mono = None
                if vals[0] is not None and vals[4] is not None:
                    score = 0
                    total = 0
                    for i in range(4):
                        if vals[i] is not None and vals[i + 1] is not None:
                            total += 1
                            if vals[i + 1] > vals[i]:
                                score += 1
                    mono = score / total if total > 0 else None
                rows.append(
                    BucketReturnResult(
                        feature=feat,
                        label_id=lid,
                        bucket_1=vals[0],
                        bucket_2=vals[1],
                        bucket_3=vals[2],
                        bucket_4=vals[3],
                        bucket_5=vals[4],
                        top_minus_bottom=tmb,
                        monotonicity_score=mono,
                    )
                )
        return pd.DataFrame([asdict(r) for r in rows])

    # ── Correlation ─────────────────────────────────────────────────────

    def _run_correlation(self) -> pd.DataFrame:
        frame = self._feature_frame
        features = [f for f in self._features if f in frame.columns]
        threshold = self._cfg.get("correlation_threshold", 0.8)
        if len(features) < 2:
            return pd.DataFrame(columns=["feature_a", "feature_b", "corr"])

        # Sample 5000 rows for speed
        sample = frame[features].sample(min(5000, len(frame)), random_state=42)
        corr_mat = sample.corr(method="pearson")

        pairs: list[CorrelationPair] = []
        seen: set[tuple[str, str]] = set()
        for i, a in enumerate(features):
            for j, b in enumerate(features):
                if j <= i:
                    continue
                v = corr_mat.loc[a, b]
                if abs(v) >= threshold and pd.notna(v):
                    pairs.append(CorrelationPair(feature_a=a, feature_b=b, corr=v))
                    seen.add((a, b))
        pairs.sort(key=lambda p: -abs(p.corr))
        return pd.DataFrame([asdict(p) for p in pairs])

    # ── Exposure breakdown ──────────────────────────────────────────────

    def _run_exposure_breakdown(self, diag_cfg: dict[str, Any]) -> pd.DataFrame:
        frame = self._feature_frame
        features = self._features + self._cfg.get("focus_features", [])
        features = list(dict.fromkeys(features))

        # Industry field detection
        ind_candidates = diag_cfg.get(
            "industry_field_candidates",
            ["industry", "industry_code", "sw_l1"],
        )
        ind_field = _resolve_column(frame, ind_candidates)
        self._resolved_ind_field = ind_field

        # Size field detection
        size_candidates = diag_cfg.get(
            "size_field_candidates", ["circ_mv", "total_mv"]
        )
        size_field = _resolve_column(frame, size_candidates)
        self._resolved_size_field = size_field

        rows: list[ExposureBreakdown] = []
        # Determine extra columns to include in merge for exposure analysis
        extra_cols = []
        if ind_field and ind_field not in extra_cols:
            extra_cols.append(ind_field)
        if size_field and size_field not in extra_cols:
            extra_cols.append(size_field)

        for lid, ld in self._label_data.items():
            for feat in features:
                if feat not in frame.columns:
                    continue
                merge_cols = ["trade_date", "instrument", feat] + extra_cols
                merge_cols = list(dict.fromkeys(merge_cols))
                merged = pd.merge(
                    frame[merge_cols],
                    ld[["trade_date", "instrument", "label_value"]],
                    on=["trade_date", "instrument"],
                    how="inner",
                ).dropna(subset=[feat, "label_value"])
                if len(merged) < 30:
                    continue

                # Raw RankIC
                raw_ic = (
                    merged.groupby("trade_date")
                    .apply(
                        lambda g: g[feat].corr(g["label_value"], method="spearman"),
                        include_groups=False,
                    )
                    .dropna()
                )
                raw_mean = float(raw_ic.mean()) if not raw_ic.empty else None

                # Within-industry RankIC
                within_mean: float | None = None
                retention: float | None = None
                if ind_field and ind_field in merged.columns:
                    merged["_ind_feat_rank"] = merged.groupby(
                        ["trade_date", ind_field]
                    )[feat].rank(pct=True)
                    within_ic = (
                        merged.groupby("trade_date")
                        .apply(
                            lambda g: g["_ind_feat_rank"].corr(
                                g["label_value"], method="spearman"
                            ),
                            include_groups=False,
                        )
                        .dropna()
                    )
                    within_mean = float(within_ic.mean()) if not within_ic.empty else None
                    if raw_mean and within_mean and abs(raw_mean) > 1e-6:
                        retention = within_mean / raw_mean

                rows.append(
                    ExposureBreakdown(
                        label_id=lid,
                        feature=feat,
                        raw_rank_ic=raw_mean,
                        within_industry_rank_ic=within_mean,
                        retention_ratio=retention,
                    )
                )

        # Size-bucket exposure (per-date cross-sectional qcut)
        if size_field and size_field in frame.columns:
            size_n = diag_cfg.get("size_buckets", 5)
            size_rows: list[dict[str, Any]] = []
            for feat in features:
                if feat not in frame.columns:
                    continue
                for lid, ld in self._label_data.items():
                    merge_cols = ["trade_date", "instrument", feat, size_field]
                    merge_cols = list(dict.fromkeys(merge_cols))
                    merged = pd.merge(
                        frame[merge_cols],
                        ld[["trade_date", "instrument", "label_value"]],
                        on=["trade_date", "instrument"],
                        how="inner",
                    ).dropna(subset=[feat, "label_value", size_field])
                    if len(merged) < 30:
                        continue
                    merged["_size_bucket"] = merged.groupby("trade_date")[size_field].transform(
                        lambda s: pd.qcut(s.rank(method="first"), size_n, labels=False) + 1
                        if s.nunique() >= size_n
                        else None
                    )
                    for bucket in range(1, size_n + 1):
                        sub = merged[merged["_size_bucket"] == bucket]
                        if len(sub) < 5:
                            continue
                        ic = sub[feat].corr(sub["label_value"], method="spearman")
                        size_rows.append(
                            {
                                "label_id": lid,
                                "feature": feat,
                                "size_bucket": bucket,
                                "rank_ic": float(ic) if pd.notna(ic) else None,
                            }
                        )
            if size_rows:
                import csv
                output_dir = self._output_dir()
                with open(output_dir / "size_exposure.csv", "w", newline="") as f:
                    w = csv.DictWriter(
                        f,
                        fieldnames=["label_id", "feature", "size_bucket", "rank_ic"],
                    )
                    w.writeheader()
                    w.writerows(size_rows)

        return pd.DataFrame([asdict(r) for r in rows])

    # ── Top candidate exposure (skip if signal not available) ───────────

    def _run_top_candidates(
        self, top_cfg: dict[str, Any], output_dir: Path
    ) -> bool:
        """Try to load signal artifact. Skip if not available."""
        experiment_id = self._cfg.get("experiment_id", "")
        if not experiment_id:
            return True

        # Try signal_research_manifest.json
        manifest_path = (
            self.root
            / "experiments"
            / experiment_id
            / "signal_research_manifest.json"
        )
        if not manifest_path.exists():
            return True

        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            return True

        signal_runs = manifest.get("signal_runs", []) or manifest.get(
            "combined_signal_runs", []
        )
        if not signal_runs:
            return True

        # Check if actual signal parquet exists
        from pathlib import Path as PPath

        has_signal = any(
            (
                self.root
                / "signals"
                / sr.get("signal_id", "")
                / sr.get("signal_run_id", "")
                / "predictions.parquet"
            ).exists()
            for sr in signal_runs
        )
        return not has_signal

    # ── Summary builder ─────────────────────────────────────────────────

    def _build_summary(self, results: dict[str, Any]) -> dict[str, Any]:
        cov = results.get("coverage", [])
        ic = results.get("feature_ic", [])
        br = results.get("bucket_return", [])
        corr = results.get("correlation", [])
        exp = results.get("exposure_breakdown", [])

        # Coverage summary
        cov_df = pd.DataFrame(cov) if cov else pd.DataFrame()
        low_cov = cov_df[cov_df["coverage"] < 0.8] if not cov_df.empty else pd.DataFrame()
        inf_list = cov_df[cov_df["inf_rate"] > 0]["feature"].tolist() if not cov_df.empty else []
        zero_list = cov_df[(cov_df["zero_rate"] > 0.5) & (cov_df["coverage"] > 0)]["feature"].tolist() if not cov_df.empty else []

        summary = {
            "config": {
                "diagnostics_id": self._cfg.get("diagnostics_id", ""),
                "experiment_id": self._cfg.get("experiment_id", ""),
                "feature_list_id": self._cfg.get("feature_list_id", ""),
                "universe": self._cfg.get("universe", ""),
                "n_labels": len(self._cfg.get("labels", [])),
                "n_features": len(self._features),
                "n_focus_features": len(self._cfg.get("focus_features", [])),
            },
            "coverage": {
                "n_features_total": len(cov),
                "n_coverage_ge_80pct": int((cov_df["coverage"] >= 0.8).sum()) if not cov_df.empty else 0,
                "low_coverage_features": low_cov["feature"].tolist() if not low_cov.empty else [],
                "features_with_inf": inf_list,
                "features_high_zero_rate": zero_list,
            },
            "feature_ic": {
                "n_pairs": len(ic),
                "n_labels_analyzed": len(set(r.get("label_id") for r in ic)),
            },
            "bucket_return": {
                "n_pairs": len(br),
            },
            "correlation": {
                "n_pairs_above_threshold": len(corr),
                "threshold": self._cfg.get("correlation_threshold", 0.8),
            },
            "exposure_breakdown": {
                "n_entries": len(exp),
                "industry_field": self._resolved_ind_field,
                "size_field": self._resolved_size_field,
            },
            "top_candidates": results.get("top_candidates", {}),
        }
        return summary

    # ── Output path ─────────────────────────────────────────────────────

    def _output_dir(self) -> Path:
        eid = self._cfg.get("experiment_id", "unknown")
        return self.root / "experiments" / eid / "diagnostics"
