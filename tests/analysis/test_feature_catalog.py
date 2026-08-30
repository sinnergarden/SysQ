from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from qsys.analysis.feature_catalog import FeatureCatalog
from qsys.research.feature_catalog_validation import validate_feature_catalog


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "configs/diagnostics/csi1800_pit_full_feature_universe_v1.yaml"


@pytest.fixture(scope="module")
def catalog_run(tmp_path_factory):
    research_root = tmp_path_factory.mktemp("feature-catalog")
    result = FeatureCatalog.from_config(CONFIG, root=research_root).run()
    validation = validate_feature_catalog(CONFIG, root=research_root)
    output_dir = research_root / "feature_catalogs/csi1800_pit_full_feature_universe_v1"
    with (output_dir / "feature_catalog.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        rows = {row["feature_name"]: row for row in csv.DictReader(handle)}
    return result, validation, rows, output_dir


def test_complete_catalog_is_independently_validated(catalog_run):
    result, validation, _, output_dir = catalog_run
    assert validation["validated"] is True
    assert validation["unique_feature_count"] >= 300
    assert validation["financial_pit_b_count"] == 25
    assert validation["shareholder_pit_x_count"] == 10
    assert result["catalog_identity_sha256"] == validation["catalog_identity_sha256"]
    assert (output_dir / "manifest.json").is_file()
    assert (output_dir / "validation.json").is_file()


def test_dependency_tiers_are_inherited_by_feature(catalog_run):
    _, _, rows, _ = catalog_run
    assert rows["$pe"]["pit_tier"] == "PIT-A"
    assert rows["$pe"]["source_tables"] == "tushare.daily_basic"
    assert rows["margin_balance_chg_20d"]["pit_tier"] == "PIT-A"
    assert "one exact open-session lag" in rows["margin_balance_chg_20d"][
        "availability_contract"
    ]
    assert rows["earnings_yield_proxy"]["pit_tier"] == "PIT-B"
    assert rows["holder_num_chg_qoq"]["pit_tier"] == "PIT-X"
    assert rows["industry_ret_60d"]["pit_tier"] == "PIT-A"
    assert "daily taxonomy bound" in rows["industry_ret_60d"][
        "availability_contract"
    ]


def test_catalog_static_checks_and_adjustment_review(catalog_run):
    _, validation, rows, output_dir = catalog_run
    assert validation["future_reference_failures"] == 0
    assert validation["label_contamination_failures"] == 0
    assert rows["($close*$factor)/(Ref($close*$factor, 5)+1e-12)-1"][
        "adjustment_contract"
    ] == "explicit adjusted-price history via factor"
    assert "factor" in rows["ret_60d"]["raw_dependencies"].split("|")
    assert rows["ret_60d"][
        "adjustment_contract"
    ] == "explicit adjusted-price history via factor"
    assert "review required" in rows["Ref($close, 5)/$close"][
        "adjustment_contract"
    ]
    summary = json.loads((output_dir / "review_summary.json").read_text())
    assert summary["feature_config_count"] >= 1
    assert summary["feature_config_reference_count"] >= summary["unique_feature_count"]
