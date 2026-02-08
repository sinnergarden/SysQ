import sys
from pathlib import Path
import pandas as pd
import lightgbm as lgb
import numpy as np
import qlib
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.model.gbdt import LGBModel
from qlib.data.dataset import DatasetH
from typing import cast
import matplotlib.pyplot as plt

# Add project root
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary, Alpha158

def analyze_ic(df_combined):
    print("\n=== IC Analysis ===")
    ic = df_combined.groupby("datetime").apply(lambda x: x["score"].corr(x["label"]))
    rank_ic = df_combined.groupby("datetime").apply(lambda x: x["score"].rank(pct=True).corr(x["label"].rank(pct=True)))
    
    print(f"Mean IC: {ic.mean():.4f}")
    print(f"Mean Rank IC: {rank_ic.mean():.4f}")
    print(f"ICIR: {ic.mean() / ic.std():.4f}")
    print(f"Rank ICIR: {rank_ic.mean() / rank_ic.std():.4f}")
    
    return rank_ic

def main():
    # Initialize Qlib (and monkeypatch git diff)
    adapter = QlibAdapter()
    adapter.init_qlib()

    # Configuration
    market = "csi300"
    train_start = "2015-01-01"
    train_end = "2021-12-31"
    valid_start = "2022-01-01"
    valid_end = "2022-12-31"
    
    print(f"Debug Pipeline: {market}")
    print(f"Train: {train_start} to {train_end}")
    print(f"Valid: {valid_start} to {valid_end}")

    # Label Definition (Weekly Return)
    label_fields = ["Ref($close, -5) / Ref($close, -1) - 1"]
    
    # Feature Config
    feature_fields = FeatureLibrary.get_alpha158_config()
    
    # Dataset Handler
    dh = DatasetH(
        handler={
            "class": "Alpha158",
            "module_path": "qsys.feature.library",
            "kwargs": {
                "instruments": market,
                "start_time": train_start,
                "end_time": valid_end,
                "fit_start_time": train_start,
                "fit_end_time": train_end,
                "label": label_fields,
                "learn_processors": [
                    {'class': 'DropnaLabel'},
                    {'class': 'CSZScoreNorm', 'kwargs': {'fields_group': 'label'}}, 
                ],
                "infer_processors": [
                    {'class': 'RobustZScoreNorm', 'kwargs': {'fields_group': 'feature', 'clip_outlier': True, 'fit_start_time': train_start, 'fit_end_time': train_end}},
                    {'class': 'Fillna', 'kwargs': {'fields_group': 'feature'}},
                ],
            },
        },
        segments={
            "train": (train_start, train_end),
            "valid": (valid_start, valid_end),
        }
    )

    print("Data Loaded. Preparing Training Data...")
    
    # Get Dataframes for inspection
    df_train_feature = cast(pd.DataFrame, dh.prepare("train", col_set="feature"))
    df_train_label = cast(pd.DataFrame, dh.prepare("train", col_set="label"))
    df_valid_feature = cast(pd.DataFrame, dh.prepare("valid", col_set="feature"))
    df_valid_label = cast(pd.DataFrame, dh.prepare("valid", col_set="label"))
    
    print(f"Train Feature Shape: {df_train_feature.shape}")
    print(f"Train Label Shape: {df_train_label.shape}")
    
    # 1. Check Label Distribution in Train vs Valid
    print("\n=== Label Distribution ===")
    print("Train Label Stats:")
    print(df_train_label.describe())
    print("Valid Label Stats:")
    print(df_valid_label.describe())
    
    # 2. Train Model
    print("\n=== Training LightGBM ===")
    model = LGBModel(
        loss="mse",
        colsample_bytree=0.8,
        learning_rate=0.05,
        subsample=0.8,
        lambda_l1=0.1,
        lambda_l2=0.1,
        max_depth=6,
        num_leaves=64,
        num_threads=20,
        n_estimators=1000,
        early_stopping_rounds=50
    )
    
    model.fit(dh)
    
    # 3. Predict and Evaluate
    print("\n=== Evaluating on Validation Set ===")
    pred_valid = model.predict(dh, segment="valid")
    
    pred_valid_series = pd.Series(pred_valid, index=df_valid_label.index)
    pred_valid_df = pd.DataFrame({"score": pred_valid_series})
    
    # Combine with label
    df_valid_label.columns = ["label"]
    df_combined = pd.concat([df_valid_label, pred_valid_df], axis=1)
    
    # Calculate IC
    rank_ic = analyze_ic(df_combined)
    
    # 4. Feature Importance
    print("\n=== Feature Importance (Top 20) ===")
    try:
        # Access the underlying LightGBM Booster
        # Qlib LGBModel stores it in self.model
        booster = model.model
        importance = booster.feature_importance(importance_type='gain')
        feature_names = booster.feature_name()
        
        imp_df = pd.DataFrame({'feature': feature_names, 'gain': importance})
        imp_df = imp_df.sort_values(by='gain', ascending=False).head(20)
        
        print(imp_df)
    except Exception as e:
        print(f"Could not extract feature importance: {e}")
        # Try to print available attributes for debugging
        # print(dir(model))
    
    # 5. Check if we are just predicting the mean
    # Ensure aligned
    df_combined = df_combined.dropna()
    mse = ((df_combined["score"] - df_combined["label"]) ** 2).mean()
    label_var = df_combined["label"].var()
    
    print(f"\nMSE: {mse:.6f}")
    print(f"Label Variance: {label_var:.6f}")
    print(f"R2 Score: {1 - mse/label_var:.6f}")

if __name__ == "__main__":
    main()
