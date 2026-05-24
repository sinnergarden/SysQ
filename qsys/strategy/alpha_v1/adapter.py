"""AlphaV1StrategyAdapter — wraps ALPHA_V1_CANDIDATE into StrategyCandidate protocol.

This is a bridge until strategies become first-class plugin objects.
No trading logic lives here — only identity, config access, and optional
lifecycle hooks.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE


# ── Helpers ──────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fmt(amount: float) -> str:
    return f"¥{amount/1000:.2f}k"


def _cs_zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-3, 3)


def _robust_zscore_transform(
    X: pd.DataFrame, center: pd.Series, scale: pd.Series
) -> pd.DataFrame:
    return ((X.astype(np.float32) - center) / scale).clip(-3, 3).fillna(0.0)


class AlphaV1StrategyAdapter:
    """Adapter exposing the alpha_v1 config singleton as a StrategyCandidate.

    Usage::

        candidate: StrategyCandidate = AlphaV1StrategyAdapter()
        runner.run_preopen(context, candidate)
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path(__file__).resolve().parents[3]
        self._stock_names: dict[str, str] = {}
        self._stock_names_loaded = False
        self._loaded_models: dict = {}
        self._clean_features: list[str] = []
        # Optional overrides set by ``from_config``
        self._config: dict | None = None
        self._config_display_name: str | None = None
        self._config_model_dir: Path | None = None
        self._config_predictions_dir: Path | None = None
        self._config_ledger_db_path: str | None = None

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        config: dict | None = None,
        project_root: Path | None = None,
    ) -> "AlphaV1StrategyAdapter":
        """Construct from an optional YAML config dict.

        Parameters
        ----------
        config : dict or None
            Parsed YAML content.  Fields that may be overridden:

            - ``display_name`` — cosmetic only
            - ``paths.model_dir`` — overrides ``_model_dir``
            - ``paths.predictions_dir`` — overrides ``_predictions_dir``
            - ``paths.ledger_db`` — overrides ``_ledger_db_path``

        project_root : Path or None
            Project root for path resolution.

        Raises
        ------
        ValueError
            If a ``portfolio`` section is present and any of its values differ
            from the frozen ``ALPHA_V1_CANDIDATE`` spec.
        """
        self = cls(project_root=project_root)

        cfg = config or {}
        self._config = cfg
        self._config_display_name = cfg.get("display_name")

        # ── Path overrides ────────────────────────────────────────────
        paths = cfg.get("paths", {})
        pr = self._project_root

        raw_model_dir = paths.get("model_dir")
        if raw_model_dir:
            p = Path(raw_model_dir)
            self._config_model_dir = p if p.is_absolute() else pr / raw_model_dir

        raw_pred_dir = paths.get("predictions_dir")
        if raw_pred_dir:
            p = Path(raw_pred_dir)
            self._config_predictions_dir = p if p.is_absolute() else pr / raw_pred_dir

        raw_ledger = paths.get("ledger_db")
        if raw_ledger:
            p = Path(raw_ledger)
            self._config_ledger_db_path = str(p if p.is_absolute() else pr / raw_ledger)

        # ── Portfolio guard ───────────────────────────────────────────
        portfolio = cfg.get("portfolio")
        if portfolio is not None:
            spec = ALPHA_V1_CANDIDATE.portfolio
            mismatches: list[str] = []
            for attr in ("top_n", "buffer_hold", "buffer_buy",
                         "single_stock_cap", "rebalance_freq"):
                val = portfolio.get(attr)
                if val is not None and val != getattr(spec, attr):
                    mismatches.append(
                        f"  portfolio.{attr}: config={val!r}, spec={getattr(spec, attr)!r}"
                    )
            if mismatches:
                raise ValueError(
                    "Config portfolio values differ from frozen ALPHA_V1_CANDIDATE:\n"
                    + "\n".join(mismatches)
                )

        # ── Training / label / feature guard ──────────────────────────
        training = cfg.get("training")
        if training is not None:
            spec_t = ALPHA_V1_CANDIDATE.training
            t_mismatches: list[str] = []
            for attr, expected in [("train_days", spec_t.train_days),
                                     ("test_days", spec_t.test_days),
                                     ("step_days", spec_t.step_days)]:
                val = training.get(attr)
                if val is not None and val != expected:
                    t_mismatches.append(
                        f"  training.{attr}: config={val!r}, spec={expected!r}"
                    )
            if t_mismatches:
                raise ValueError(
                    "Config training values differ from frozen ALPHA_V1_CANDIDATE:\n"
                    + "\n".join(t_mismatches)
                )

        label_cfg = cfg.get("label")
        if label_cfg is not None:
            l_mismatches: list[str] = []
            horizons = label_cfg.get("horizons")
            expected_horizons = [5, 20]
            if horizons is not None and sorted(horizons) != expected_horizons:
                l_mismatches.append(
                    f"  label.horizons: config={horizons!r}, expected={expected_horizons!r}"
                )
            label_type = label_cfg.get("type")
            if label_type is not None and label_type != "forward_return":
                l_mismatches.append(
                    f"  label.type: config={label_type!r}, expected='forward_return'"
                )
            if l_mismatches:
                raise ValueError(
                    "Config label values differ from frozen alpha_v1 semantics:\n"
                    + "\n".join(l_mismatches)
                )

        feature_cfg = cfg.get("feature")
        if feature_cfg is not None:
            f_mismatches: list[str] = []
            fs = feature_cfg.get("feature_set")
            if fs is not None and fs != "alpha_v1":
                f_mismatches.append(
                    f"  feature.feature_set: config={fs!r}, expected='alpha_v1'"
                )
            sv = feature_cfg.get("schema_version")
            if sv is not None and sv != "current":
                f_mismatches.append(
                    f"  feature.schema_version: config={sv!r}, expected='current'"
                )
            if f_mismatches:
                raise ValueError(
                    "Config feature values differ from frozen alpha_v1 semantics:\n"
                    + "\n".join(f_mismatches)
                )

        return self

    # ── Derived paths ──────────────────────────────────────────────────

    @property
    def _model_dir(self) -> Path:
        if self._config_model_dir is not None:
            return self._config_model_dir
        return self._project_root / "experiments/alpha_v1_models/latest"

    @property
    def _predictions_dir(self) -> Path:
        if self._config_predictions_dir is not None:
            return self._config_predictions_dir
        return self._project_root / "experiments/alpha_v1_shadow_predictions"

    @property
    def _ledger_db_path(self) -> str:
        if self._config_ledger_db_path is not None:
            return self._config_ledger_db_path
        return str(self._project_root / "data" / "trade.db")

    UNIVERSE = "csi300"

    # ── Identity ──────────────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return ALPHA_V1_CANDIDATE.strategy_id

    @property
    def account_id(self) -> str:
        return ALPHA_V1_CANDIDATE.shadow_account_id

    @property
    def display_name(self) -> str:
        return self._config_display_name or "Alpha V1"

    # ── Configuration ──────────────────────────────────────────────────

    @property
    def universe(self) -> str:
        return self.UNIVERSE

    @property
    def feature_set(self) -> str:
        return "alpha_v1"

    @property
    def model_version(self) -> str:
        return ALPHA_V1_CANDIDATE.version

    @property
    def signal_version(self) -> str:
        return f"blend_{ALPHA_V1_CANDIDATE.blend.ratio_str}"

    @property
    def rebalance_policy(self) -> dict[str, Any]:
        p = ALPHA_V1_CANDIDATE.portfolio
        return {
            "top_n": p.top_n,
            "buffer_hold": p.buffer_hold,
            "buffer_buy": p.buffer_buy,
            "rebalance_freq": p.rebalance_freq,
            "single_stock_cap": p.single_stock_cap,
        }

    # ── Data ────────────────────────────────────────────────────────────

    def get_stock_name(self, ts_code: str) -> str:
        if not self._stock_names_loaded:
            self._load_stock_names()
        return self._stock_names.get(ts_code, ts_code)

    def _load_stock_names(self) -> None:
        path = self._project_root / "data" / "stock_names.csv"
        if path.exists():
            df = pd.read_csv(path)
            for _, row in df.iterrows():
                self._stock_names[str(row["ts_code"])] = str(row["name"])
        self._stock_names_loaded = True

    def resolve_data_date(self, trade_date: str) -> str:
        from qlib.data import D as qlib_D

        from qsys.data.adapter import QlibAdapter

        QlibAdapter().init_qlib()
        cal = qlib_D.calendar(start_time="2020-01-01", end_time=trade_date)
        if cal is None or len(cal) == 0:
            print(f"  ⚠ qlib 日历无 <= {trade_date} 的交易日")
            return trade_date
        data_date = pd.Timestamp(cal[-1]).strftime("%Y-%m-%d")
        if data_date != trade_date:
            print(f"  ⚠ {trade_date} 无数据，回退到 {data_date}")
        return data_date

    def load_model(self) -> None:
        import lightgbm as lgb

        models: dict[str, tuple[Any, pd.Series, pd.Series]] = {}
        for tag in ["5d", "20d"]:
            model_path = self._model_dir / f"model_{tag}.txt"
            center_path = self._model_dir / f"center_{tag}.json"
            scale_path = self._model_dir / f"scale_{tag}.json"
            if not model_path.exists():
                raise FileNotFoundError(f"模型文件不存在: {model_path}")
            model = lgb.Booster(model_file=str(model_path))
            center = pd.Series(json.loads(center_path.read_text()))
            scale = pd.Series(json.loads(scale_path.read_text()))
            models[tag] = (model, center, scale)
            print(f"  Model {tag}: {model.num_trees()} trees")
        # Resolve feature list
        features_file = self._model_dir / "features.json"
        if features_file.exists():
            self._clean_features = json.loads(features_file.read_text())
        else:
            from qsys.feature.library import FeatureLibrary

            all_features = FeatureLibrary.get_semantic_all_features_config()
            from qsys.strategy.alpha_v1.spec import get_clean_features

            self._clean_features = get_clean_features(all_features)
        print(f"  Features: {len(self._clean_features)}")
        self._loaded_models = models

    def fetch_data(self, data_date: str) -> Any:
        from qlib.data import D as qlib_D

        from qsys.data.adapter import QlibAdapter
        from qsys.feature.library import FeatureLibrary

        adapter = QlibAdapter()
        adapter.init_qlib()
        all_features = FeatureLibrary.get_semantic_all_features_config()
        raw = adapter.get_features(
            self.UNIVERSE,
            all_features + ["$close"],
            start_time=data_date,
            end_time=data_date,
        )
        if raw.empty:
            print(f"  ⚠ {data_date} 无特征数据")
            return pd.DataFrame()
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        print(f"  {self.UNIVERSE}: {len(frame)} rows (data_date={data_date})")
        return frame

    # ── Predict + Plan ──────────────────────────────────────────────────

    def generate_predictions(self, data: Any) -> Any:
        frame: pd.DataFrame = data
        from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE as C

        if frame is None or (isinstance(frame, pd.DataFrame) and frame.empty):
            raise ValueError("交易日无数据")
        trade_date = frame["trade_date"].iloc[0] if "trade_date" in frame.columns else "unknown"
        if not self._clean_features:
            raise ValueError("clean_features empty — call load_model() first")
        X = frame[self._clean_features].astype(np.float32).fillna(0.0)
        if X.empty or len(X) < 10:
            raise ValueError(f"交易日 {trade_date} 数据不足 ({len(X)} rows)")

        if not self._loaded_models:
            raise ValueError("models not loaded — call load_model() first")

        pred_5d_model, center_5d, scale_5d = self._loaded_models["5d"]
        pred_20d_model, center_20d, scale_20d = self._loaded_models["20d"]
        Xz_5d = _robust_zscore_transform(X, center_5d, scale_5d)
        Xz_20d = _robust_zscore_transform(X, center_20d, scale_20d)
        p5 = pd.Series(pred_5d_model.predict(Xz_5d.values), index=X.index)
        p20 = pd.Series(pred_20d_model.predict(Xz_20d.values), index=X.index)
        z5 = _cs_zscore(p5)
        z20 = _cs_zscore(p20)
        blended = C.blend.blend_5d * z5 + C.blend.blend_20d * z20

        instruments = frame["instrument"].values
        rows = []
        for i, inst in enumerate(instruments):
            rows.append({
                "trade_date": trade_date,
                "instrument": str(inst),
                "score": float(blended.iloc[i]) if pd.notna(blended.iloc[i]) else 0.0,
                "model_name": "alpha_v1_candidate_ensemble",
                "mainline_object_name": "alpha_v1_candidate",
            })
        return pd.DataFrame(rows)

    def should_rebalance(self, trade_date: str) -> bool:
        freq = ALPHA_V1_CANDIDATE.portfolio.rebalance_freq
        if freq != "weekly":
            return True
        trade_dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
        # Try to get the actual trade date from the last execution
        last_trade_date: str | None = None
        if Path(self._ledger_db_path).exists():
            try:
                from qsys.ledger.service import LedgerService

                service = LedgerService(self._ledger_db_path)
                last_trade_date = service.get_latest_trade_date(self.account_id)
                service.close()
            except Exception:
                pass
        if not last_trade_date:
            account_path = self._project_root / "shadow" / "account.json"
            if account_path.exists():
                try:
                    acct_data = json.loads(account_path.read_text())
                    last_trade_date = acct_data.get("trade_date", "")
                except (json.JSONDecodeError, OSError):
                    pass
        if last_trade_date:
            last_dt = datetime.strptime(last_trade_date, "%Y-%m-%d").date()
            current_iso = trade_dt.isocalendar()
            last_iso = last_dt.isocalendar()
            if last_iso[0] == current_iso[0] and last_iso[1] == current_iso[1]:
                return False
        return True

    @staticmethod
    def _parse_date(raw: Any) -> str:
        """Extract YYYY-MM-DD from a date cell that may be Timestamp or string."""
        if isinstance(raw, pd.Timestamp):
            return raw.strftime("%Y-%m-%d")
        return str(raw).split(" ")[0]

    def build_plan(self, predictions: Any, target_dir: Any) -> bool:
        from qsys.ops.shadow_rebalance import build_alpha_v1_plan

        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Save predictions to a temp location that build_alpha_v1_plan can read
        pred_path = target_dir / "predictions_for_plan.csv"
        predictions.to_csv(pred_path, index=False)

        trade_date = self._parse_date(predictions["trade_date"].iloc[0])
        build_alpha_v1_plan(
            base_dir=".",
            trade_date=trade_date,
            reference_date=trade_date,
            predictions_path=str(pred_path),
            output_dir=str(target_dir.parent),  # run_root
            db_path=self._ledger_db_path,
        )
        return True

    # ── Backtest hooks ─────────────────────────────────────────────

    def generate_predictions_for_date(
        self, trade_date: str, *, data_date: str | None = None,
    ) -> pd.DataFrame:
        """Generate predictions for a single historical date.

        Reuses existing adapter methods: ``resolve_data_date``,
        ``load_model``, ``fetch_data``, ``generate_predictions``.
        ``load_model`` is idempotent (cached in ``_loaded_models``).
        """
        dd = data_date or self.resolve_data_date(trade_date)
        if not self._loaded_models:
            self.load_model()
        data = self.fetch_data(dd)
        if data is None or (isinstance(data, pd.DataFrame) and data.empty):
            return pd.DataFrame()
        return self.generate_predictions(data)

    def build_plan_for_backtest(
        self,
        predictions: pd.DataFrame,
        account: Any,
        trade_date: str,
        output_dir: Any,
    ) -> Path:
        """Build a trading plan using in-memory ``account`` state.

        Reuses ``build_plan_from_predictions`` from ``qsys.ops.plan_builder``.
        Returns path to ``plan`` subdirectory under *output_dir*.
        """
        from qsys.backtest.portfolio import build_rank_weight_portfolio
        from qsys.ops.plan_builder import build_plan_from_predictions

        p = ALPHA_V1_CANDIDATE.portfolio
        # Use data_date close prices for plan building (matching DailyRunner's
        # preopen behaviour) — predictions["trade_date"] is the data_date.
        data_date = self._parse_date(predictions["trade_date"].iloc[0])
        return build_plan_from_predictions(
            shadow_dir=Path("/tmp/_bt_shadow"),  # dummy — account provided
            trade_date=trade_date,
            reference_date=data_date,
            predictions=predictions,
            output_dir=Path(output_dir),
            portfolio_fn=build_rank_weight_portfolio,
            top_n=p.top_n,
            buffer_hold=p.buffer_hold,
            buffer_buy=p.buffer_buy,
            single_stock_cap=p.single_stock_cap,
            strategy_id=self.strategy_id,
            strategy_version=self.model_version,
            portfolio_method="rank_weight_buffer",
            account=account,
        )

    def load_plan_instruments(self, plan_dir: Any) -> list[str]:
        intents_path = Path(plan_dir) / "order_intents.csv"
        if not intents_path.exists():
            return []
        try:
            df = pd.read_csv(intents_path)
            return sorted(set(df["instrument"].astype(str)))
        except Exception:
            return []

    def save_predictions(self, predictions: Any, run_root: Any, trade_date: str) -> None:
        """Save predictions to the alpha_v1 shared predictions directory."""
        shared_dir = self._project_root / "experiments" / "alpha_v1_shadow_predictions"
        shared_dir.mkdir(parents=True, exist_ok=True)
        path = shared_dir / f"predictions_{trade_date}.csv"
        predictions.to_csv(path, index=False)
        print(f"  → {len(predictions)} predictions saved: {path}")

    def fetch_open_prices(self, trade_date: str, instruments: list[str]) -> dict[str, float]:
        """Fetch open prices via qlib for the given instruments."""
        from qsys.data.adapter import QlibAdapter

        adapter = QlibAdapter()
        adapter.init_qlib()
        market = adapter.get_features(
            instruments, ["$open"],
            start_time=trade_date, end_time=trade_date,
        )
        if market is None or market.empty:
            return {}
        if isinstance(market.index, pd.MultiIndex):
            market = market.swaplevel().sort_index()
        frame = market.reset_index()
        frame = frame[frame["datetime"].astype(str).str.startswith(trade_date)]
        if frame.empty:
            return {}
        frame = frame.sort_values(["instrument", "datetime"]).drop_duplicates(
            subset=["instrument"], keep="last"
        )
        prices = frame.set_index("instrument")["$open"].astype(float).to_dict()
        return prices

    def print_predictions_summary(self, predictions: Any) -> None:
        """Print top-5 predictions to console."""
        top = predictions.sort_values("score", ascending=False).head(5)
        for i, (_, row) in enumerate(top.iterrows(), 1):
            print(f"    #{i} {row['instrument']}  score={row['score']:.4f}")

    # ── Execute + MTM ──────────────────────────────────────────────────

    def execute_plan(self, context: Any) -> Any:
        from qsys.ops.shadow_rebalance import execute_alpha_v1_plan

        plan_dir = context.run_root / "plan"
        staging_exec_dir = context.run_root / "execution" / "staging"

        artifacts = execute_alpha_v1_plan(
            base_dir=".",
            plan_dir=str(plan_dir),
            execution_date=context.trade_date,
            output_dir=str(staging_exec_dir),
            debug_run=context.debug_run,
            db_path=self._ledger_db_path if not context.debug_run else None,
        )
        return artifacts

    def commit_execution(self, context: Any, staging_dir: Any) -> None:
        from qsys.ops.shadow_execution import commit_execution_artifacts

        commit_execution_artifacts(
            run_root=context.run_root,
            staging_dir=staging_dir,
            db_path=self._ledger_db_path,
            trade_date=context.trade_date,
            strategy_id=self.strategy_id,
            debug_run=context.debug_run,
        )

    def mark_to_market(self, context: Any) -> dict | None:
        from qsys.ops.mtm import (
            fetch_close_prices,
            load_mtm_snapshot,
            try_mark_to_market,
        )

        exec_dir = context.run_root / "execution"
        staging_exec_dir = context.run_root / "execution" / "staging"

        if context.debug_run:
            staging_acct = staging_exec_dir / "account_after.json"
            staging_pos = staging_exec_dir / "positions_after.csv"
            mtm = try_mark_to_market(
                context.trade_date,
                output_dir=context.run_root,
                account_path=staging_acct if staging_acct.exists() else None,
                positions_path=staging_pos if staging_pos.exists() else None,
                project_root=context.project_root,
                shadow_account_id=self.account_id,
                get_stock_name_fn=self.get_stock_name,
            )
        else:
            mtm = try_mark_to_market(
                context.trade_date,
                output_dir=context.run_root,
                db_path=self._ledger_db_path if not context.debug_run else None,
                project_root=context.project_root,
                shadow_account_id=self.account_id,
                get_stock_name_fn=self.get_stock_name,
            )
        return mtm

    def load_artifacts_for_notification(self, context: Any) -> Any | None:
        from qsys.ops.shadow_execution import ShadowRebalanceArtifacts

        exec_dir = context.run_root / "execution"
        plan_dir = context.run_root / "plan"
        summary_path = exec_dir / "execution_summary.json"
        if not summary_path.exists():
            return None
        try:
            summary = json.loads(summary_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return ShadowRebalanceArtifacts(
            trade_date=context.trade_date,
            run_id=summary.get("run_id", ""),
            status=summary.get("status", "success"),
            strategy_id=summary.get("strategy_id", self.strategy_id),
            strategy_version=summary.get("strategy_version", ""),
            portfolio_method=summary.get("portfolio_method", "plan_execution"),
            top_n=summary.get("portfolio_params", {}).get("top_n", 20),
            buffer_hold=60, buffer_buy=40, single_stock_cap=0.07,
            turnover_buffer=0.0, price_mode="open", rebalance_mode="plan_execution",
            target_weights_path=str(plan_dir / "target_weights.csv") if (plan_dir / "target_weights.csv").exists() else "",
            order_intents_path=str(plan_dir / "order_intents.csv") if (plan_dir / "order_intents.csv").exists() else "",
            execution_summary_path=str(summary_path),
            account_after_path=str(exec_dir / "account_after.json"),
            positions_after_path=str(exec_dir / "positions_after.csv"),
            shadow_account_path="", shadow_positions_path="", shadow_ledger_path="",
            ledger_rows_path="",
            rebalance_audit_path=str(plan_dir / "rebalance_audit.csv") if (plan_dir / "rebalance_audit.csv").exists() else "",
            order_count=summary.get("order_count", 0),
            buy_count=summary.get("buy_count", 0),
            sell_count=summary.get("sell_count", 0),
            skipped_count=summary.get("skipped_count", 0),
            filled_count=summary.get("filled_count", 0),
            rejected_count=summary.get("rejected_count", 0),
            turnover=summary.get("turnover", 0.0),
            cash_after=summary.get("cash_after", 0.0),
            total_value_after=summary.get("total_value_after", 0.0),
        )

    # ── Training ─────────────────────────────────────────────────────────

    def train(self, context: Any) -> Any:
        """Delegate to AlphaV1Trainer."""
        from qsys.model.alpha_v1_trainer import AlphaV1Trainer

        trainer = AlphaV1Trainer(
            project_root=self._project_root,
            config=self._config,
            model_version=self.model_version,
        )
        return trainer.run(context)

    # ── Notifications ──────────────────────────────────────────────────

    def build_preopen_message(
        self, context: Any, rebalance_skipped: bool, predictions: Any
    ) -> str:
        predictions = pd.DataFrame(predictions)
        top = predictions.sort_values("score", ascending=False).head(5)
        top_picks = [(row["instrument"], row["score"]) for _, row in top.iterrows()]
        run_root = Path(context.run_root)
        plan_dir = run_root / "plan"

        lines = [
            f"✅ Alpha V1 Pre-open {context.trade_date}",
            f"Time: {_now_str()}",
            "", "📈 推荐股票",
        ]
        for i, (inst, score) in enumerate(top_picks[:5], 1):
            name = self.get_stock_name(inst)
            lines.append(f"  {i}. {inst} {name}  score={score:.4f}")

        # Show existing plan details if available
        intents_path = plan_dir / "order_intents.csv"
        has_existing_plan = intents_path.exists()
        if has_existing_plan:
            try:
                orders_df = pd.read_csv(intents_path)
                # Merge scores from predictions
                scores_df = predictions[["instrument", "score"]]
                orders_df = orders_df.merge(scores_df, on="instrument", how="left")
                orders_df["score"] = orders_df["score"].fillna(0.0)
                buys = orders_df[orders_df["side"] == "buy"].sort_values("score", ascending=False)
                sells = orders_df[orders_df["side"] == "sell"].sort_values("score", ascending=False)
                lines += ["", "📋 计划交易（以 OPEN 价执行）", ""]
                if not buys.empty:
                    lines.append(f"  计划买入 ({len(buys)}):")
                    lines.append(f"    {'代码':<12} {'名称':<8} {'买入金额':<12} 手数  score")
                    for _, row in buys.iterrows():
                        name = self.get_stock_name(row["instrument"])
                        diff_val = float(row.get("diff_value", 0))
                        qty = int(row.get("requested_qty", 0))
                        lines.append(f"    {row['instrument']:<12} {name:<8} +{_fmt(diff_val):<10} {qty//100}手  {row['score']:.4f}")
                if not sells.empty:
                    lines.append(f"  计划卖出 ({len(sells)}):")
                    lines.append(f"    {'代码':<12} {'名称':<8} {'卖出金额':<12} 手数  score")
                    for _, row in sells.iterrows():
                        name = self.get_stock_name(row["instrument"])
                        diff_val = float(row.get("diff_value", 0))
                        qty = int(row.get("requested_qty", 0))
                        lines.append(f"    {row['instrument']:<12} {name:<8} -{_fmt(abs(diff_val)):<10} {qty//100}手  {row['score']:.4f}")
                    lines.append("")
            except Exception as e:
                lines.append(f"  ⚠ 无法读取交易计划详情: {e}")

        if rebalance_skipped and not has_existing_plan:
            lines += ["", "⏭ 本周已调仓，跳过重复交易"]

        lines += [
            "",
            f"策略: {ALPHA_V1_CANDIDATE.display_name} | 频率: {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq}",
            f"Universe: {self.UNIVERSE} | 预测: {len(predictions)}只",
        ]
        if has_existing_plan:
            lines += ["", "📝 注: 计划不执行交易，待 21:30 数据同步后 postclose 以开盘价执行"]
        return "\n".join(lines)

    def build_postclose_message(
        self,
        context: Any,
        mtm: dict | None = None,
        artifacts: Any = None,
        stale_check: dict | None = None,
        execution_committed: bool = False,
        execution_skipped: bool = False,
        idempotent_skip: bool = False,
    ) -> str:
        self.get_stock_name("")  # ensure cache loaded
        lines = [
            f"📊 Alpha V1 Post-close {context.trade_date}",
            f"Time: {_now_str()}", "",
        ]
        if context.debug_run:
            lines.append("🔧 调试模式 — 不修改 shadow 账户")
            lines.append("")
        if execution_committed and not execution_skipped:
            if idempotent_skip:
                lines += ["✅ 执行状态: 已完成（执行记录已存在）", ""]
            else:
                lines += ["✅ 执行状态: 已完成", ""]
        elif execution_committed and execution_skipped:
            lines += ["✅ 执行状态: 无计划需执行", ""]
        elif context.debug_run:
            lines += ["🔧 执行状态: 调试模式，未提交 shadow 账户", ""]
        if stale_check:
            sc = stale_check
            status_icon = {"passed": "✅", "blocked": "⛔", "skipped": "⏭", "skipped_low_overlap": "⏭"}
            lines.append(
                f"📡 数据陈旧检查: {status_icon.get(sc.get('status', ''), '❓')} "
                f"一致={sc.get('identical_count', 0)}/{sc.get('checked_count', 0)} "
                f"({sc.get('identical_ratio', 0)*100:.0f}%)"
            )
            if sc.get("examples"):
                for ex in sc["examples"]:
                    lines.append(f"    {ex}")
            lines.append("")
        if artifacts:
            lines.append(f"🏦 执行摘要（按 {context.trade_date} 开盘价）")
            lines.append(
                f"  成交额: {_fmt(artifacts.turnover)}  买入委托: {artifacts.order_count} "
                f"成交: {artifacts.filled_count}  未成交: {artifacts.rejected_count}"
            )
            mv = artifacts.total_value_after - artifacts.cash_after
            lines.append(
                f"  Total: {_fmt(artifacts.total_value_after)}  "
                f"Cash: {_fmt(artifacts.cash_after)}  MV: {_fmt(mv)}"
            )
            lines.append("")
        if mtm:
            cum_pnl_str = f"+{_fmt(mtm['cumulative_pnl'])}" if mtm['cumulative_pnl'] >= 0 else _fmt(mtm['cumulative_pnl'])
            daily_str = f"+{_fmt(mtm['daily_pnl'])}" if mtm['daily_pnl'] >= 0 else _fmt(mtm['daily_pnl'])
            lines.append(f"💰 Mark-to-Market（按 {context.trade_date} 收盘价）")
            lines.append(f"  累计 PnL: {cum_pnl_str} ({mtm['cumulative_pnl_pct']:+.2f}%)")
            lines.append(f"  当日 PnL: {daily_str}")
            lines.append(f"  Total: {_fmt(mtm['total_value'])}  Cash: {_fmt(mtm['cash'])}")
            pos_before = mtm.get('positions_before_count', 0)
            pos_after = mtm.get('priced_count', 0)
            if pos_before > 0:
                lines.append(f"  Position: {_fmt(mtm['market_value'])}  Holdings: {pos_after}只（原有{pos_before} + 新增{pos_after - pos_before}）")
            else:
                lines.append(f"  Position: {_fmt(mtm['market_value'])}  Holdings: {pos_after}只")
            top3 = mtm['details'][:3]
            bot3 = mtm['details'][-3:] if len(mtm['details']) >= 3 else mtm['details']
            if top3:
                lines.append("")
                lines.append("📈 当日收益 Top 3")
                for inst, name, qty, cost, close, pnl_val in top3:
                    s = f"+{_fmt(pnl_val)}" if pnl_val >= 0 else _fmt(pnl_val)
                    lines.append(f"  {inst} {name}  {s}  {qty//100}手  {cost:.2f}→{close:.2f}")
            if bot3 and bot3 != top3:
                lines.append("")
                lines.append("📉 当日收益 Bottom 3")
                for inst, name, qty, cost, close, pnl_val in bot3:
                    s = f"+{_fmt(pnl_val)}" if pnl_val >= 0 else _fmt(pnl_val)
                    lines.append(f"  {inst} {name}  {s}  {qty//100}手  {cost:.2f}→{close:.2f}")
        else:
            lines.append("⚠ Mark-to-Market 不可用")
            lines.append("收盘价数据未就绪（数据同步可能未完成）。")
        return "\n".join(lines)

    def send_notification(self, text: str) -> None:
        from qsys.ops.telegram import send_telegram_message

        print(f"\n{'─' * 50}")
        print("📱 Telegram 通知:")
        print(text)
        print(f"{'─' * 50}\n")
        result = send_telegram_message(text)
        status = result.get("status", "unknown")
        if status == "skipped":
            print(f"  ⚠ Telegram 未配置: {result.get('message', '')}")
        elif status == "failed":
            print(f"  ❌ Telegram 发送失败: {result.get('error', '')}")
        else:
            print(f"  ✅ Telegram 已发送")
