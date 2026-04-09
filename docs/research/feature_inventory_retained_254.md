# Retained Feature Set (254)

当前 UI/registry/默认 snapshot 已收敛到当前 `semantic_all_features` 模型真实 runtime 使用的 254 个特征。

## Group Counts

- `qlib_native`: 158
- `relative_strength`: 16
- `fundamental_context`: 13
- `fundamental`: 12
- `liquidity`: 11
- `tradability`: 9
- `regime`: 8
- `industry_context`: 7
- `margin`: 7
- `microstructure`: 7
- `price`: 6

## Policy

- `keep_core`: 价格/流动性/可交易性底座，直接保留。
- `keep_semantic`: 当前 semantic 派生特征，继续保留。
- `keep_with_review`: 可保留，但需要后续补 PIT/coverage 审计。
- `keep_observe`: 暂保留，但后续需要继续核实表达式/解释性。

## UI Visibility

- sample snapshot visible: 251 / 254
- sample health visible: 252 / 254

详细清单见 `scratch/feature_inventory_retained_254.csv`。
