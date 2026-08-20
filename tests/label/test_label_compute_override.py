"""label_id_override threading in qsys.label.compute (Stage 9B PIT label build)."""

from __future__ import annotations

import pandas as pd
import pytest

import qsys.label.compute as compute_mod


class _FakeAdapter:
    """Minimal QlibAdapter stand-in returning a tiny OHLC-frame."""

    def __init__(self) -> None:
        self.dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07",
                      "2020-01-08", "2020-01-09", "2020-01-10", "2020-01-13",
                      "2020-01-14", "2020-01-15", "2020-01-16", "2020-01-17"]

    def init_qlib(self) -> None:
        pass

    def get_features(self, universe, fields, start_time=None, end_time=None,
                     freq="day", inst_processors=None, *, margin_lag_sessions=0):
        idx = pd.MultiIndex.from_product([self.dates, ["000001.SZ"]],
                                         names=["datetime", "instrument"])
        n = len(self.dates)
        return pd.DataFrame({
            fields[0]: [10.0 + i for i in range(n)],
            "$factor": [1.0] * n,
        }, index=idx)


def _patch_adapter(monkeypatch) -> None:
    # compute_forward_return does `from qsys.data.adapter import QlibAdapter`
    # inside the function, so the patch must hit the adapter module.
    import qsys.data.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "QlibAdapter", _FakeAdapter)


class TestComputeForwardReturnOverride:
    def test_override_produces_pit_label_id(self, monkeypatch) -> None:
        _patch_adapter(monkeypatch)
        out = compute_mod.compute_forward_return(
            "csi800_pit_union", 5, "2020-01-01", "2020-06-01",
            norm_type="", clip_val=None,
            label_id_override="fwd_ret_5d_raw_pit",
        )
        assert not out.empty
        assert (out["label_id"] == "fwd_ret_5d_raw_pit").all()

    def test_default_still_derives_id(self, monkeypatch) -> None:
        _patch_adapter(monkeypatch)
        out = compute_mod.compute_forward_return(
            "csi800_pit_union", 5, "2020-01-01", "2020-06-01",
            norm_type="", clip_val=None,
        )
        assert (out["label_id"] == "fwd_ret_5d_raw").all()

    def test_raw_helper_passes_override(self, monkeypatch) -> None:
        _patch_adapter(monkeypatch)
        out = compute_mod.compute_raw_forward_return(
            "csi800_pit_union", 5, "2020-01-01", "2020-06-01",
            label_id_override="fwd_ret_5d_raw_pit",
        )
        assert (out["label_id"] == "fwd_ret_5d_raw_pit").all()
