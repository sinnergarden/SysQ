# Current Value Growth Candidates — 2025-12-08

> NOT a pull request. Research note only. Do not merge.

## Latest Signal Date: 2025-12-08

From the 180d raw LightGBM model (vgb_ext_signal). 26 features from value_growth_multibagger_v1_features.

## Top20 Industry Distribution

| Industry | Count |
|----------|-------|
| 软件服务 | 4 |
| 半导体 | 3 |
| 电气设备 | 2 |
| 元器件 | 2 |
| 化工原料 | 2 |
| 通信设备 | 1 |
| 汽车配件 | 1 |
| 机械基件 | 1 |
| 小金属 | 1 |
| 文教休闲 | 1 |
| 综合类 | 1 |
| 专用机械 | 1 |

**Top1 weight: 20% | Top3 weight: 45%**

Industry concentration is moderate. Top20 covers 12 different industries — no single-industry dominance.

## Top50 Industry Distribution

| Industry | Count |
|----------|-------|
| 半导体 | 10 |
| 元器件 | 7 |
| 软件服务 | 6 |
| 电气设备 | 5 |
| 汽车配件 | 3 |
| 小金属 | 3 |
| 化工原料 | 3 |
| 生物制药 | 3 |
| 通信设备 | 2 |
| 专用机械 | 2 |
| 文教休闲 | 2 |
| 综合类 / 机械基件 / 化学制药 / 航空 | 1 each |

**Top1 weight: 20% | Top3 weight: 46%**

Concentration stabilizes at 20-50 positions.

## Feature Analysis (Latest Top20)

Can't reliably fetch qlib feature values for the latest date (feature data may lag signal date). Manual review of top-20 sample suggests:

- **半导体/元器件 heavy** — cyclical recovery candidates
- **软件服务** — digital economy theme
- No 金融/银行/地产 — consistent with value-growth model's style
- Industry distribution is reasonable for a multi-sector candidate pool

## Worth Further Research (Top10)

| Rank | Stock | Industry | Notes |
|------|-------|----------|-------|
| 1 | 600171 上海贝岭 | 半导体 | Top score, IC design |
| 2 | 300735 光弘科技 | 元器件 | EMS manufacturing |
| 4 | 002281 光迅科技 | 通信设备 | Optical transceivers |
| 7 | 002410 广联达 | 软件服务 | SaaS, digital construction |
| 10 | 300339 润和软件 | 软件服务 | AI/鸿蒙 ecosystem |

All are in technology/innovation sectors that align with China's medium-term industrial policy direction.

## Most Suspicious

| Rank | Stock | Industry | Concern |
|------|-------|----------|---------|
| 6 | 300100 双林股份 | 汽车配件 | Auto parts, commoditized |
| 17 | 002607 中公教育 | 文教休闲 | Education — regulatory risk |
| 3 | 600673 东阳光 | 综合类 | Conglomerate — hard to analyze |
| 18 | 600602 云赛智联 | 软件服务 | Low-profile, SOE |
| 13 | 000657 中钨高新 | 小金属 | Commodity cycle exposure |

## Assessment

**Top20 像可人工研究的候选池。**
- 行业分布合理不极端
- 集中于科技/制造业（中国比较优势方向）
- 未出现银行/地产/保险等政策逆风行业
- 20% top3 集中度处于可接受范围

**不足：** 当前信号 date 的 feature 值无法直接获取，候选池解释依赖于工判断。下一个 iteration 可以存 inference 时的 feature attribution。
