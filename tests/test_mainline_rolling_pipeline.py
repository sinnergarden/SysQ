from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from scripts.run_mainline_rolling_pipeline import main as rolling_pipeline_main


def test_mainline_rolling_pipeline_keeps_stable_entry_contract(tmp_path: Path) -> None:
    runner = CliRunner()
    calls: list[tuple[str, list[str]]] = []

    def _record(name: str):
        def _inner(*, args, standalone_mode=False):
            calls.append((name, list(args)))
            return None
        return _inner

    with patch("scripts.run_mainline_rolling_pipeline.rolling_eval_main.main", side_effect=_record("rolling_eval")), \
         patch("scripts.run_mainline_rolling_pipeline.comparison_main.main", side_effect=_record("comparison")), \
         patch("scripts.run_mainline_rolling_pipeline.update_decision_main.main", side_effect=_record("decision")), \
         patch("scripts.run_mainline_rolling_pipeline.publish_ui_main.main", side_effect=_record("publish")):
        result = runner.invoke(
            rolling_pipeline_main,
            [
                "--start", "2025-01-02",
                "--end", "2026-03-20",
                "--mainline_object", "feature_173",
            ],
        )

    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["rolling_eval", "comparison", "decision", "publish"]
    assert calls[0][1][:6] == ["--start", "2025-01-02", "--end", "2026-03-20", "--output_dir", "experiments/mainline_rolling"]
    assert calls[0][1][-2:] == ["--mainline_object", "feature_173"]
