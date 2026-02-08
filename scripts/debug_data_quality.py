import sys
from pathlib import Path
import pandas as pd
import qlib
from qlib.data import D
from qlib.data.dataset import DatasetH

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.feature.library import FeatureLibrary

# Initialize Qlib
print("Initializing Qlib...")
qlib.init(provider_uri=str(cfg.get_path("qlib_bin")))
print(f"Qlib initialized at {cfg.get_path('qlib_bin')}")

# Check Raw Data
print("\n--- Checking Raw Data for 600519.SH (Moutai) ---")
try:
    df_raw = D.features(
        instruments=['600519.SH'], 
        fields=['$close', '$volume', '$amount', '$factor'], 
        start_time='2020-01-01', 
        end_time='2020-01-10'
    )
    print(df_raw)
except Exception as e:
    print(f"Failed to fetch raw data: {e}")

# Define Dataset Configuration
market = "all"
train_period = ("2015-01-01", "2021-12-31")
valid_period = ("2022-01-01", "2022-12-31")
test_period = ("2023-01-01", "2023-12-31")

label = ["(Ref($close, -5) / Ref($close, -1) - 1)"]
label_col = ["LABEL0"]
feature_fields = FeatureLibrary.get_alpha158_config()
data_loader = {
    "class": "QlibDataLoader",
    "kwargs": {
        "config": {
            "feature": (feature_fields, feature_fields),
            "label": (label, label_col),
        }
    }
}

print("\n--- Initializing DatasetH ---")
try:
    dh = DatasetH(
        handler={
            "class": "Alpha158",
            "module_path": "qsys.feature.library",
            "kwargs": {
                "instruments": market,
                "start_time": train_period[0],
                "end_time": test_period[1],
                "fit_start_time": train_period[0],
                "fit_end_time": train_period[1],
                "label": label,
                "data_loader": data_loader,
                "learn_processors": [
                    {'class': 'DropnaLabel'},
                    {'class': 'CSZScoreNorm', 'kwargs': {'fields_group': 'label'}},
                ],
                "infer_processors": [
                    {'class': 'RobustZScoreNorm', 'kwargs': {'fields_group': 'feature', 'clip_outlier': True, 'fit_start_time': train_period[0], 'fit_end_time': train_period[1]}},
                    {'class': 'Fillna', 'kwargs': {'fields_group': 'feature'}},
                ],
            },
        },
        segments={
            "train": train_period,
            "valid": valid_period,
            "test": test_period,
        }
    )
    print("DatasetH initialized successfully.")

    print("\n--- Inspecting Training Data ---")
    df_train_features = dh.prepare("train", col_set="feature")
    df_train_label = dh.prepare("train", col_set="label")
    
    if isinstance(df_train_features, list):
        df_train_features = pd.concat(df_train_features)
    if isinstance(df_train_label, list):
        df_train_label = pd.concat(df_train_label)

    print(f"Data Shape: {df_train_features.shape}")
    
    print("\n=== Label Stats ===")
    print(df_train_label.describe())

    print("\n=== Feature Stats (First 5 Cols) ===")
    print(df_train_features.iloc[:, :5].describe())
    
    zero_count = (df_train_features == 0).sum().sum()
    nan_count = df_train_features.isna().sum().sum()
    total_cells = df_train_features.size
    print(f"\nTotal Cells: {total_cells}")
    print(f"Zero Cells: {zero_count} ({zero_count/total_cells:.2%})")
    print(f"NaN Cells:  {nan_count} ({nan_count/total_cells:.2%})")
    
    print("\n=== Sample Row ===")
    if not df_train_features.empty:
        print(df_train_features.iloc[0])

except Exception as e:
    print(f"Dataset Processing Failed: {e}")
    import traceback
    traceback.print_exc()
