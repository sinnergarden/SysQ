from __future__ import annotations

import pandas as pd


SIGNAL_VALUE_COLUMNS = ("signal_value", "score", "prob", "expected_return")
OPTIONAL_SIGNAL_COLUMNS = ("binary",)


def to_signal_frame(signals: pd.Series | pd.DataFrame, signal_type: str = "score") -> pd.DataFrame:
    if isinstance(signals, pd.Series):
        frame = signals.rename("signal_value").to_frame()
    else:
        frame = signals.copy()
        if "signal_value" not in frame.columns:
            for column in SIGNAL_VALUE_COLUMNS:
                if column in frame.columns:
                    frame = frame.rename(columns={column: "signal_value"})
                    break
            else:
                frame = frame.rename(columns={frame.columns[0]: "signal_value"})
    for column in OPTIONAL_SIGNAL_COLUMNS:
        if column in frame.columns:
            frame[column] = frame[column]
    frame["signal_type"] = signal_type
    columns = ["signal_value", *(column for column in OPTIONAL_SIGNAL_COLUMNS if column in frame.columns), "signal_type"]
    return frame[columns]
