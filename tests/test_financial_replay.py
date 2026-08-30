from pathlib import Path

import pandas as pd
import pytest

from qsys.data.storage import StockDataStore


def _store(tmp_path: Path) -> StockDataStore:
    store = StockDataStore.__new__(StockDataStore)
    store.canonical_dir = tmp_path / "canonical"
    store.canonical_dir.mkdir()
    return store


def test_replace_daily_projection_is_field_bounded_audited_and_idempotent(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    code = "000001.SZ"
    original = pd.DataFrame({
        "ts_code": [code] * 3,
        "trade_date": ["20200101", "20200102", "20200103"],
        "close": [10.0, 11.0, 12.0],
        "roe": [2.5, 2.5, 2.5],
        "ann_date": ["20191231"] * 3,
    })
    original.to_feather(store.canonical_dir / f"{code}.feather")
    projected = pd.DataFrame({
        "ts_code": [code, code],
        "trade_date": ["20200102", "20200103"],
        "roe": [0.025, 0.025],
        "ann_date": ["20191231", "20191231"],
    })
    intents: list[dict] = []

    def before_commit(mutation: dict) -> None:
        assert pd.read_feather(store.canonical_dir / f"{code}.feather")["roe"].iloc[-1] == 2.5
        intents.append(mutation)

    mutation = store.replace_daily_projection(
        projected,
        code,
        fields=["roe", "ann_date"],
        date_start="20200102",
        date_end="20200103",
        fetch_receipt_id="receipt-1",
        before_commit=before_commit,
    )

    assert mutation is not None
    assert mutation["fields"] == ["roe"]
    assert mutation["date_start"] == "20200102"
    assert mutation["date_end"] == "20200103"
    assert mutation["fetch_receipt_id"] == "receipt-1"
    assert mutation["before_hash"] != mutation["after_hash"]
    assert intents == [mutation]
    stored = pd.read_feather(store.canonical_dir / f"{code}.feather")
    assert stored["close"].tolist() == [10.0, 11.0, 12.0]
    assert stored["roe"].tolist() == [2.5, 0.025, 0.025]
    assert store.replace_daily_projection(
        projected,
        code,
        fields=["roe", "ann_date"],
        date_start="20200102",
        date_end="20200103",
    ) is None


def test_replace_daily_projection_rejects_incomplete_date_coverage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    code = "000001.SZ"
    pd.DataFrame({
        "ts_code": [code, code],
        "trade_date": ["20200101", "20200102"],
        "roe": [1.0, 1.0],
    }).to_feather(store.canonical_dir / f"{code}.feather")

    with pytest.raises(ValueError, match="date coverage mismatch"):
        store.replace_daily_projection(
            pd.DataFrame({
                "ts_code": [code], "trade_date": ["20200101"], "roe": [0.01]
            }),
            code,
            fields=["roe"],
            date_start="20200101",
            date_end="20200102",
        )
