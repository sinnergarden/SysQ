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
import hashlib
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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        self._lineage: dict[str, Any] = {}

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
        (output_dir / "size_exposure.csv").unlink(missing_ok=True)

        results: dict[str, Any] = {}

        # 1. Coverage
        if enabled.get("coverage", True):
            cov_df = self._run_coverage()
            cov_df.to_csv(output_dir / "coverage.csv", index=False)
            cov_daily_df, cov_yearly_df = self._run_coverage_by_time()
            cov_daily_df.to_csv(output_dir / "coverage_daily.csv", index=False)
            cov_yearly_df.to_csv(output_dir / "coverage_yearly.csv", index=False)
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

        # 7. Time-split Stage-A evidence.  This remains feature-level research:
        # no model fitting or portfolio result is allowed to affect promotion.
        stage_a_cfg = self._cfg.get("stage_a") or {}
        if stage_a_cfg.get("enabled", False):
            from qsys.research.stage_a import StageAEvaluator

            if self._feature_frame is None:
                raise ValueError("Stage-A requires a loaded feature frame")
            protocol = StageAEvaluator(
                feature_frame=self._feature_frame,
                features=self._features,
                label_data=self._label_data,
                label_configs=[
                    dict(value) for value in self._cfg.get("labels", [])
                    if isinstance(value, dict)
                ],
                config=stage_a_cfg,
                output_dir=output_dir,
            ).run()
            results["stage_a"] = protocol
            log.info(
                "Stage-A: %d trials, %d candidates, %d confirmed",
                protocol["feature_trial_count"],
                protocol["candidate_count"],
                protocol["confirmed_count"],
            )

        # Summary
        summary = _json_safe(self._build_summary(results))
        with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(
                summary, f, indent=2, ensure_ascii=False, allow_nan=False
            )
        config_bytes = json.dumps(
            self._cfg,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        code_path = Path(__file__)
        adapter_path = Path(__import__("qsys.data.adapter", fromlist=["x"]).__file__)
        outputs = {}
        for path in sorted(output_dir.iterdir()):
            if not path.is_file() or path.name == "manifest.json":
                continue
            entry = {
                "sha256": _sha256_file(path),
                "size": path.stat().st_size,
            }
            if path.suffix == ".csv":
                entry["row_count"] = max(
                    sum(1 for _ in path.open("r", encoding="utf-8")) - 1,
                    0,
                )
            outputs[path.name] = entry
        identity_payload = {
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "lineage": self._lineage,
            "diagnostics_code_sha256": _sha256_file(code_path),
            "adapter_code_sha256": _sha256_file(adapter_path),
        }
        if stage_a_cfg.get("enabled", False):
            import qsys.research.stage_a as stage_a_module

            identity_payload["stage_a_code_sha256"] = _sha256_file(
                Path(stage_a_module.__file__).resolve()
            )
        diagnostics_identity_sha256 = hashlib.sha256(
            json.dumps(
                identity_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        manifest = {
            "artifact_type": "research_diagnostics",
            "schema_version": 2,
            "diagnostics_identity_sha256": diagnostics_identity_sha256,
            **identity_payload,
            "outputs": outputs,
        }
        (output_dir / "manifest.json").write_text(
            json.dumps(
                manifest, indent=2, sort_keys=True, ensure_ascii=False,
                allow_nan=False,
            ) + "\n",
            encoding="utf-8",
        )
        results["summary"] = summary
        results["output_dir"] = str(output_dir)
        results["manifest"] = str(output_dir / "manifest.json")
        results["diagnostics_identity_sha256"] = diagnostics_identity_sha256

        return results

    # ── Data loading ────────────────────────────────────────────────────

    def _load_data(self) -> None:
        from qsys.config import cfg as settings

        cache_cfg = self._cfg.get("feature_cache") or {}
        if cache_cfg and not isinstance(cache_cfg, dict):
            raise ValueError("feature_cache must be a mapping")
        if self._cfg.get("require_feature_cache") and not cache_cfg:
            raise ValueError("formal cached diagnostics require feature_cache")
        if not cache_cfg:
            self._adapter.init_qlib()

        source_artifacts = self._cfg.get("source_artifacts", {})
        if self._cfg.get("require_source_artifacts") and not source_artifacts:
            raise ValueError("formal diagnostics require source_artifacts")
        verified_sources: dict[str, Any] = {}
        for name, artifact in sorted(source_artifacts.items()):
            declared_path = str(artifact.get("path", "")).strip()
            expected_sha256 = str(artifact.get("sha256", "")).strip().lower()
            path = Path(declared_path).expanduser()
            if not path.is_absolute():
                path = settings.data_root / path
            if not path.is_file():
                raise FileNotFoundError(
                    f"diagnostics source artifact missing: {path}"
                )
            actual_sha256 = _sha256_file(path)
            if actual_sha256 != expected_sha256:
                raise ValueError(
                    f"diagnostics source artifact hash mismatch for {name}: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
            verified_sources[name] = {
                "path": declared_path,
                "sha256": actual_sha256,
                "size": path.stat().st_size,
            }
        if verified_sources:
            self._lineage["source_artifacts"] = verified_sources

        universe = self._cfg.get("universe", "csi800")
        start = self._cfg.get("start_date", "2024-06-01")
        end = self._cfg.get("end_date", "2025-12-31")
        alignment_cfg = self._cfg.get("feature_label_alignment") or {}
        source_start = str(start)
        if alignment_cfg:
            calendar_path = Path(str(alignment_cfg.get("calendar_path", "")))
            if not calendar_path.is_absolute():
                calendar_path = settings.data_root / calendar_path
            if calendar_path.is_symlink() or not calendar_path.is_file():
                raise ValueError(
                    f"alignment calendar must be a regular file: {calendar_path}"
                )
            if _sha256_file(calendar_path) != str(
                alignment_cfg.get("calendar_sha256", "")
            ):
                raise ValueError("feature-label alignment calendar hash mismatch")
            calendar = sorted({
                line.strip()[:10]
                for line in calendar_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            })
            prior = [date for date in calendar if date < str(start)]
            if not prior:
                raise ValueError("alignment calendar has no session before diagnostics start")
            source_start = prior[-1]
        fids = self._cfg.get("feature_list_id") or self._cfg.get("focus_features", [])

        if self._cfg.get("feature_list_id"):
            self._features = FeatureListRegistry.load(self._cfg["feature_list_id"])
            self._lineage["feature_list"] = {
                key: value
                for key, value in FeatureListRegistry.contract(
                    self._cfg["feature_list_id"]
                ).items()
                if key != "features"
            }
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

        pit_artifact = str(self._cfg.get("pit_universe_artifact", "")).strip()
        pit_mode = str(self._cfg.get("pit_filter_mode", "")).strip()
        if self._cfg.get("require_pit_universe") and not pit_artifact:
            raise ValueError("formal diagnostics require pit_universe_artifact")
        semantic_spans = None
        feature_universe: str | list[str] = universe
        pit_store = None
        if pit_artifact:
            from qsys.research.pit_universe import PitUniverseStore

            pit_store = PitUniverseStore(pit_artifact)
            feature_universe = (
                pit_store.membership_window(start, end)
                if hasattr(pit_store, "membership_window")
                else pit_store.instruments
            )
            semantic_spans = pit_store.spans
            pit_mode = pit_mode or "member_as_of"
            if pit_mode not in {"member_as_of", "ever_member_as_of"}:
                raise ValueError(f"unsupported diagnostics pit_filter_mode: {pit_mode}")
            manifest_path = pit_store.artifact_dir / "manifest.json"
            self._lineage["pit_universe"] = {
                "artifact": pit_artifact,
                "filter_mode": pit_mode,
                "manifest_path": str(manifest_path),
                "manifest_sha256": _sha256_file(manifest_path),
                "membership_sha256": pit_store.provenance.membership_sha256,
                "raw_source_sha256": pit_store.provenance.raw_source_hash,
            }

        if cache_cfg:
            frame = self._load_feature_cache(
                cache_cfg,
                requested_features=all_requested,
                start=source_start,
                end=str(end),
            )
        else:
            raw = self._adapter.get_features(
                feature_universe,
                all_requested + ["$factor"],
                start_time=source_start,
                end_time=end,
                semantic_pit_membership_spans=semantic_spans,
                semantic_pit_filter_mode=pit_mode,
            )
            frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        if frame.columns.duplicated().any():
            duplicated = sorted(set(frame.columns[frame.columns.duplicated()]))
            raise ValueError(
                "diagnostics feature source has duplicate columns: "
                + ", ".join(duplicated)
            )
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        if self._cfg.get("require_feature_label_alignment") and not alignment_cfg:
            raise ValueError("formal diagnostics require feature_label_alignment")
        if alignment_cfg:
            if not isinstance(alignment_cfg, dict):
                raise ValueError("feature_label_alignment must be a mapping")
            frame = self._align_feature_dates(
                frame,
                alignment_cfg,
                data_root=settings.data_root,
                execution_start=str(start),
                execution_end=str(end),
            )
        if pit_store is not None:
            spans = pit_store.spans[
                ["instrument", "effective_from", "effective_to"]
            ].copy()
            spans["effective_from"] = (
                spans["effective_from"].astype(str).str.replace("-", "", regex=False).astype(int)
            )
            spans["effective_to"] = (
                spans["effective_to"].astype(str).str.replace("-", "", regex=False).astype(int)
            )
            merged = frame.merge(spans, on="instrument", how="inner")
            dates = merged["trade_date"].str.replace("-", "", regex=False).astype(int)
            if pit_mode == "ever_member_as_of":
                keep = dates >= merged["effective_from"]
            else:
                keep = (
                    (dates >= merged["effective_from"])
                    & (dates <= merged["effective_to"])
                )
            frame = merged.loc[keep, frame.columns].drop_duplicates(
                ["trade_date", "instrument"]
            )
            if frame.empty:
                raise ValueError("PIT universe filtering removed all diagnostics rows")
        self._feature_frame = frame

        taxonomy_path = settings.get_path("meta") / "industry_map.json"
        if self._cfg.get("require_industry_taxonomy") and not taxonomy_path.is_file():
            raise FileNotFoundError(
                f"formal diagnostics require industry taxonomy: {taxonomy_path}"
            )
        self._lineage["industry_taxonomy"] = {
            "contract": "historical_daily_industry_numeric_map_v1",
            "path": str(taxonomy_path),
            "sha256": (
                _sha256_file(taxonomy_path)
                if taxonomy_path.is_file() else None
            ),
            "source_manifest_hash": self._cfg.get("source_manifest_hash"),
        }

        for f in all_requested:
            self._feature_meta[f] = f in frame.columns

        # Load labels
        for lcfg in self._cfg.get("labels", []):
            lid = lcfg if isinstance(lcfg, str) else lcfg.get("label_id", "")
            if not lid:
                continue
            try:
                label_frame = self._label_store.load_labels(
                    lid, start_date=start, end_date=end,
                )
                raw_row_count = len(label_frame)
                if self._cfg.get("require_executable_labels"):
                    required = {
                        "is_valid", "entry_eligible", "is_mature",
                        "return_type", "label_value",
                    }
                    missing = sorted(required - set(label_frame.columns))
                    if missing:
                        raise ValueError(
                            f"label {lid} lacks executable columns: {missing}"
                        )
                    if label_frame.duplicated(
                        ["trade_date", "instrument"]
                    ).any():
                        raise ValueError(
                            f"label {lid} has duplicate instrument/date rows"
                        )
                    expected_return_type = (
                        str(lcfg.get("return_type", "")).strip()
                        if isinstance(lcfg, dict) else ""
                    )
                    actual_return_types = sorted(
                        label_frame["return_type"].dropna().astype(str).unique()
                    )
                    if expected_return_type and actual_return_types != [
                        expected_return_type
                    ]:
                        raise ValueError(
                            f"label {lid} return_type mismatch: "
                            f"expected {expected_return_type}, "
                            f"got {actual_return_types}"
                        )
                    # Entry eligibility and maturity are already encoded in
                    # is_valid by the label contract.  Future exit status is
                    # intentionally not consulted here.
                    label_frame = label_frame[
                        label_frame["is_valid"].eq(True)
                        & label_frame["label_value"].notna()
                    ].copy()
                    if label_frame.empty:
                        raise ValueError(
                            f"label {lid} has no valid observed executable rows"
                        )
                self._label_data[lid] = label_frame
                manifest_path = self._label_store.paths.label_manifest(lid)
                data_path = self._label_store._resolve_data_path(lid)
                self._lineage.setdefault("labels", {})[lid] = {
                    "manifest_path": str(manifest_path),
                    "manifest_sha256": _sha256_file(manifest_path),
                    "data_path": str(data_path),
                    "data_sha256": _sha256_file(data_path),
                    "raw_row_count": raw_row_count,
                    "consumed_row_count": len(label_frame),
                    "validity_filter_contract": (
                        "label_is_valid_and_observed_v1"
                        if self._cfg.get("require_executable_labels")
                        else "none"
                    ),
                }
            except Exception as exc:
                if self._cfg.get("require_all_labels", True):
                    raise RuntimeError(f"Could not load required label {lid}: {exc}") from exc
                log.warning("Could not load label %s: %s", lid, exc)

    def _load_feature_cache(
        self,
        cache_cfg: dict[str, Any],
        *,
        requested_features: list[str],
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Load only declared columns and years from a validated annual cache."""
        project_root = Path(__file__).resolve().parents[2]

        def resolve_file(value: str, label: str) -> Path:
            path = Path(str(value)).expanduser()
            if not path.is_absolute():
                path = project_root / path
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} must be an existing regular file: {path}")
            return path.resolve()

        manifest_path = resolve_file(
            str(cache_cfg.get("manifest_path", "")), "feature cache manifest"
        )
        validation_path = resolve_file(
            str(cache_cfg.get("validation_path", "")), "feature cache validation"
        )
        manifest_sha256 = _sha256_file(manifest_path)
        validation_sha256 = _sha256_file(validation_path)
        expected_manifest_sha256 = str(
            cache_cfg.get("manifest_sha256", "")
        ).strip().lower()
        expected_validation_sha256 = str(
            cache_cfg.get("validation_sha256", "")
        ).strip().lower()
        if not expected_manifest_sha256 or manifest_sha256 != expected_manifest_sha256:
            raise ValueError("feature cache manifest hash mismatch")
        if not expected_validation_sha256 or validation_sha256 != expected_validation_sha256:
            raise ValueError("feature cache validation hash mismatch")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 2 or validation.get("status") != "pass":
            raise ValueError("feature cache has not passed the required validation schema")
        if validation.get("manifest_sha256") != manifest_sha256:
            raise ValueError("feature cache validation does not bind the manifest")
        shards = list(manifest.get("shards", []))
        if not shards:
            raise ValueError("feature cache manifest has no shards")
        identity = shards[0].get("identity", {})
        column_contract = identity.get("column_contract", {})
        materialized = list(column_contract.get("materialized_features", []))
        consumed = list(column_contract.get("consumed_features", []))
        if self._features != consumed:
            raise ValueError("diagnostics feature list does not match cache consumed contract")
        unavailable = sorted(set(requested_features) - set(materialized))
        if unavailable:
            raise ValueError(f"requested diagnostics columns absent from cache: {unavailable}")
        feature_contract = FeatureListRegistry.contract(str(identity["feature_list_id"]))
        if feature_contract["features"] != consumed:
            raise ValueError("current feature registry differs from cache consumed contract")
        if identity.get("pit_universe_artifact") != self._cfg.get("pit_universe_artifact"):
            raise ValueError("cache and diagnostics PIT universe artifacts differ")
        if identity.get("pit_filter_mode") != self._cfg.get("pit_filter_mode"):
            raise ValueError("cache and diagnostics PIT filter modes differ")
        expected_source = str(self._cfg.get("source_manifest_hash", ""))
        if manifest.get("source_manifest_hash") != expected_source:
            raise ValueError("cache and diagnostics source manifest hashes differ")
        if str(manifest.get("cache_coverage_start", "")) > start:
            raise ValueError("feature cache does not cover diagnostics start")
        if str(manifest.get("cache_coverage_end", "")) < end:
            raise ValueError("feature cache does not cover diagnostics end")

        validation_shards = {
            str(Path(item["path"]).resolve()): item
            for item in validation.get("shards", [])
        }
        selected: list[dict[str, Any]] = []
        frames: list[pd.DataFrame] = []
        columns = ["trade_date", "instrument", *requested_features]
        for shard in shards:
            coverage_start = str(shard["source_coverage_start"])
            coverage_end = str(shard["source_coverage_end"])
            if coverage_end < start or coverage_start > end:
                continue
            path = resolve_file(str(shard["path"]), "feature cache shard")
            data_sha256 = _sha256_file(path)
            if data_sha256 != shard.get("data_sha256"):
                raise ValueError(f"feature cache shard hash mismatch: {path}")
            validation_entry = validation_shards.get(str(path))
            if not validation_entry or validation_entry.get("data_sha256") != data_sha256:
                raise ValueError(f"feature cache validation lacks shard binding: {path}")
            frame = pd.read_parquet(path, columns=columns)
            frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
            frame = frame[
                frame["trade_date"].between(start, end, inclusive="both")
            ]
            frames.append(frame)
            selected.append({
                "path": str(shard["path"]),
                "data_sha256": data_sha256,
                "source_coverage_start": coverage_start,
                "source_coverage_end": coverage_end,
                "consumed_rows": len(frame),
            })
        if not frames:
            raise ValueError("no feature cache shards overlap diagnostics range")
        result = pd.concat(frames, ignore_index=True)
        if result.duplicated(["trade_date", "instrument"]).any():
            raise ValueError("selected feature cache rows contain duplicate keys")
        self._lineage["feature_cache"] = {
            "manifest_path": str(cache_cfg["manifest_path"]),
            "manifest_sha256": manifest_sha256,
            "validation_path": str(cache_cfg["validation_path"]),
            "validation_sha256": validation_sha256,
            "materialized_feature_list_id": identity["feature_cache_list_id"],
            "consumed_feature_list_id": identity["feature_list_id"],
            "materialized_feature_count": len(materialized),
            "consumed_feature_count": len(consumed),
            "selected_shards": selected,
            "rows_before_daily_pit_filter": len(result),
        }
        return result

    def _align_feature_dates(
        self,
        frame: pd.DataFrame,
        alignment_cfg: dict[str, Any],
        *,
        data_root: Path,
        execution_start: str,
        execution_end: str,
    ) -> pd.DataFrame:
        """Map after-close feature date ``f`` to next-session execution date."""
        contract = str(alignment_cfg.get("contract", ""))
        if contract != "previous_open_session_to_execution_date_v1":
            raise ValueError(f"unsupported feature-label alignment contract: {contract}")
        calendar_path = Path(str(alignment_cfg.get("calendar_path", "")))
        if not calendar_path.is_absolute():
            calendar_path = Path(data_root) / calendar_path
        if calendar_path.is_symlink() or not calendar_path.is_file():
            raise ValueError(f"alignment calendar must be a regular file: {calendar_path}")
        calendar_sha256 = _sha256_file(calendar_path)
        if calendar_sha256 != str(alignment_cfg.get("calendar_sha256", "")):
            raise ValueError("feature-label alignment calendar hash mismatch")
        calendar = sorted({
            line.strip()[:10]
            for line in calendar_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        })
        next_session = {
            date: calendar[index + 1]
            for index, date in enumerate(calendar[:-1])
        }
        aligned = frame.copy()
        aligned["data_date"] = aligned["trade_date"].astype(str).str[:10]
        aligned["trade_date"] = aligned["data_date"].map(next_session)
        unresolved = int(aligned["trade_date"].isna().sum())
        aligned = aligned.dropna(subset=["trade_date"])
        aligned = aligned[
            aligned["trade_date"].between(
                execution_start, execution_end, inclusive="both"
            )
        ].copy()
        if aligned.empty:
            raise ValueError("feature-label alignment removed all diagnostics rows")
        if not aligned["data_date"].lt(aligned["trade_date"]).all():
            raise ValueError("feature-label alignment consumed a non-prior feature date")
        if aligned.duplicated(["trade_date", "instrument"]).any():
            raise ValueError("feature-label alignment produced duplicate execution keys")
        self._lineage["feature_label_alignment"] = {
            "contract": contract,
            "calendar_path": str(alignment_cfg["calendar_path"]),
            "calendar_sha256": calendar_sha256,
            "data_date_max": str(aligned["data_date"].max()),
            "execution_date_min": str(aligned["trade_date"].min()),
            "execution_date_max": str(aligned["trade_date"].max()),
            "strict_prior_date_check": "pass",
            "unresolved_terminal_rows": unresolved,
            "aligned_rows": len(aligned),
        }
        return aligned

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

    def _run_coverage_by_time(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Report PIT-filtered feature availability per day and calendar year."""
        frame = self._feature_frame
        columns = ["trade_date", "feature", "eligible_count", "available_count", "coverage"]
        rows: list[dict[str, Any]] = []
        for trade_date, day in frame.groupby("trade_date", sort=True):
            eligible_count = int(day["instrument"].nunique())
            for feature in self._features:
                available_count = (
                    int(pd.to_numeric(day[feature], errors="coerce").notna().sum())
                    if feature in day.columns else 0
                )
                rows.append({
                    "trade_date": trade_date,
                    "feature": feature,
                    "eligible_count": eligible_count,
                    "available_count": available_count,
                    "coverage": (
                        available_count / eligible_count if eligible_count else None
                    ),
                })
        daily = pd.DataFrame(rows, columns=columns)
        if daily.empty:
            yearly = pd.DataFrame(columns=[
                "year", "feature", "eligible_count", "available_count", "coverage"
            ])
            return daily, yearly
        daily["year"] = daily["trade_date"].astype(str).str[:4]
        yearly = daily.groupby(["year", "feature"], as_index=False).agg(
            eligible_count=("eligible_count", "sum"),
            available_count=("available_count", "sum"),
        )
        yearly["coverage"] = np.where(
            yearly["eligible_count"] > 0,
            yearly["available_count"] / yearly["eligible_count"],
            None,
        )
        return daily.drop(columns="year"), yearly

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

        # Average same-date cross-sectional correlations.  Pooling arbitrary
        # rows across years would mix time-series drift into a feature synonym
        # decision.
        corr_mat = (
            frame[["trade_date", *features]]
            .groupby("trade_date")[features]
            .corr(method="pearson")
            .groupby(level=1)
            .mean()
        )

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
        return pd.DataFrame(
            [asdict(p) for p in pairs],
            columns=["feature_a", "feature_b", "corr"],
        )

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
                        daily_ic = sub.groupby("trade_date").apply(
                            lambda group: group[feat].corr(
                                group["label_value"], method="spearman"
                            ) if len(group) >= 5 else np.nan,
                            include_groups=False,
                        ).dropna()
                        size_rows.append(
                            {
                                "label_id": lid,
                                "feature": feat,
                                "size_bucket": bucket,
                                "rank_ic": (
                                    float(daily_ic.mean())
                                    if not daily_ic.empty else None
                                ),
                                "n_dates": int(len(daily_ic)),
                            }
                        )
            if size_rows:
                import csv
                output_dir = self._output_dir()
                with open(output_dir / "size_exposure.csv", "w", newline="") as f:
                    w = csv.DictWriter(
                        f,
                        fieldnames=[
                            "label_id", "feature", "size_bucket", "rank_ic",
                            "n_dates",
                        ],
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
            "stage_a": results.get("stage_a", {}),
        }
        return summary

    # ── Output path ─────────────────────────────────────────────────────

    def _output_dir(self) -> Path:
        eid = self._cfg.get("experiment_id", "unknown")
        return self.root / "experiments" / eid / "diagnostics"
