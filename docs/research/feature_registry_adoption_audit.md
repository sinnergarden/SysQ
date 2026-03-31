# Feature Registry / Feature Set 适配审计

## 1. 审计范围与判定口径

本次审计只检查代码库中真实存在的入口，不脑补新系统，不发明新架构。

判定口径：

- `已适配`：入口已经通过 registry / feature set 作为主要正式引用方式
- `部分适配`：入口已接入一部分，但仍保留旧名字、旧列表或仅消费兼容层结果
- `未适配`：入口仍直接写死旧 feature 名或旧列表，且未接入 registry / feature set
- `当前代码库中不存在`：没有对应的独立入口

## 2. 审计结果

### 2.1 feature build / refresh 入口

| 入口 | 状态 | 依据 | 备注 |
|---|---|---|---|
| `scripts/run_feature_build.py` | 已适配 | 已显式接收 `--feature_set`，调用 `resolve_feature_selection()` 和 `build_research_features(feature_set=...)` | 当前是最干净的 research build 入口 |
| `scripts/run_feature_experiment.py` | 部分适配 | 已新增 `--feature_set` 并通过 registry 解析，但仍保留 `with_phase2` 这类历史实验语义开关，默认输出文件名仍是 `phase1_*` | 逻辑已接入新体系，CLI 语义仍带历史包袱 |
| `scripts/run_feature_readiness_audit.py` | 部分适配 | 已新增 `--feature_set` 并通过 registry 解析，但内部仍依赖 legacy flags 强制打开各组 builder | 主体流程已接入，参数语义尚未完全收束 |
| `scripts/run_feature_ablation.py` | 部分适配 | 已新增 `--feature_set` 并改为走 `build_research_features(feature_set=...)`，但 ablation 维度仍用旧 flags 组合定义 | 适合作为兼容研究脚本，尚不是完全语义化入口 |
| 独立的 feature refresh 入口 | 当前代码库中不存在 | 仓库里没有单独的“特征刷新/落库”主入口，现有脚本主要是 build / audit / experiment | 当前特征系统仍以 panel + builder 视图为主 |

### 2.2 train 入口和训练配置

| 入口 | 状态 | 依据 | 备注 |
|---|---|---|---|
| `scripts/run_train.py` | 部分适配 | 现在已接受语义化 `feature_set` 名称，并通过 `FeatureLibrary.normalize_feature_set_name()` / `get_feature_fields_by_set()` 解析；训练时会把 canonical `feature_set_name`、alias、feature ids、native 字段列表写入模型 artifact metadata | 但训练主链路仍只消费 `native_qlib_fields`，无法直接训练 custom derived features |
| `qsys/feature/library.py` | 已适配 | 已成为训练入口与正式 feature set 的兼容映射层，旧 alias 会解析到语义化 set | 它现在更像训练端 adapter，而不是新的 taxonomy source of truth |
| `qsys/model/zoo/qlib_native.py` | 部分适配 | 可消费从 registry 解析出的 native Qlib 字段列表 | 仍不直接理解 feature id / feature set，也不构建 derived columns |

### 2.3 test 入口

| 入口 | 状态 | 依据 | 备注 |
|---|---|---|---|
| `tests/test_feature_registry.py` | 已适配 | 已覆盖 registry 加载、feature set 解析、混合 provider set、列解析 | 是新体系的核心契约测试 |
| `tests/test_feature_phase1.py` | 部分适配 | 同时覆盖新 `feature_set` 构建和旧 `build_phase1_features` 兼容入口 | 测试本身仍保留历史命名语义作为兼容验证 |
| `tests/test_extended_feature_config.py` | 部分适配 | 仍主要验证 `FeatureLibrary` 的旧对外方法，但这些方法内部已映射到新 set | 它更偏兼容性测试，不是新体系原生测试 |
| `python -m unittest discover tests` | 部分适配 | 全量测试能通过，说明兼容层未被破坏 | 但大部分测试并不直接验证 registry / feature set 的采用率 |

### 2.4 backtest / experiment 入口

| 入口 | 状态 | 依据 | 备注 |
|---|---|---|---|
| `scripts/run_backtest.py` | 部分适配 | 不直接解析 feature set，而是消费训练好模型 artifact 中保存的 `feature_config`；本轮已补充读取 artifact metadata 并在 backtest report 中展示 canonical `feature_set_name` | 对已训练模型可用，但不是 registry 原生入口 |
| `scripts/run_strict_eval.py` | 部分适配 | 与 backtest 相同，消费模型 artifact；本轮已补充把 baseline / extended artifact 的 canonical `feature_set_name` 写入 strict eval report | 依赖训练阶段把正确输入写入 artifact |
| `scripts/run_compare.py` | 未适配 | 仍直接写死 `data/models/qlib_lgbm`，不经过 registry / feature set | 属于低优先级研究工具，历史味道很重 |
| `scripts/debug_model_performance.py` | 未适配 | 仍直接调用 `FeatureLibrary.get_alpha158_config()` 和 `Alpha158` handler，不使用 registry / feature set 解析 | 属于调试脚本，当前未收束 |

### 2.5 inference / production data prep 入口

| 入口 | 状态 | 依据 | 备注 |
|---|---|---|---|
| `scripts/run_daily_trading.py` | 部分适配 | 现在会优先通过 `SignalGenerator` 读取模型 artifact metadata 中的 canonical `feature_set_name` 作为报告字段 | 但实际取数仍依赖模型 artifact 里的 `feature_config` 列表，不在 runtime 再走 registry |
| `qsys/live/manager.py` | 部分适配 | 运行时根据已加载模型的 `feature_config` 直接取 Qlib 列 | 这是 artifact 驱动，不是 registry 直驱 |
| `qsys/strategy/generator.py` | 部分适配 | 已新增 `load_model_artifact_metadata()`，可统一读取训练阶段保存的 canonical feature metadata；但实际预测仍以 `feature_config` 为主 | 尚未把 feature set 用作推理时的主引用方式 |
| `qsys/dataview/inference.py` | 未适配 | 仅提供 `fields` 级加载接口，不理解 registry / feature set | 当前代码存在，但不是主生产入口 |
| `qsys/dataview/research.py` | 未适配 | 同上，只接受 `fields` 参数 | 当前代码存在，但与新体系尚未打通 |
| 独立的 production inference data prep 主入口 | 当前代码库中不存在 | 生产侧真实入口是 `run_daily_trading.py` + `LiveManager` | 没有单独的 feature-prep orchestration 脚本 |

## 3. 代码与文档是否一致

### 3.1 一致的部分

- 正式 taxonomy 已在 registry 和文档中统一为：`price / liquidity / microstructure / tradability / cross_section / regime / fundamental / event`
- `FeatureLibrary` 的历史 alias 已被文档明确降级为兼容层
- `run_train.py`、`run_feature_build.py` 等主要入口的帮助文字已经开始推荐语义化 feature set

### 3.2 仍存在不一致或半一致的部分

- 一些 research 脚本仍保留 `phase1_*` 输出文件名或 `with_phase2` 这类历史参数语义，代码虽已部分接入新体系，但 CLI 表达还未完全同步
- 调试与比较脚本仍停留在旧特征命名或模型目录硬编码层面，文档没有把它们当成正式新体系入口
- 生产推理链路在报告层面已经能看到 canonical `feature_set_name`，但 runtime 实际仍主要依赖 artifact 中保存的字段列表

## 4. 最小修补清单

本轮已完成的最小修补如下：

1. `scripts/run_train.py`
- 接受语义化 `feature_set` 名称
- 旧 alias 仍可用
- 训练时把 canonical `feature_set_name` / alias / feature ids / native fields 写入模型 `params`
- 额外落盘 `feature_selection.yaml`，作为模型目录下的正式选择摘要

2. `qsys/feature/library.py`
- 新增 `normalize_feature_set_name()`
- 训练配置从 registry / feature set 解析，而不是脚本里手写旧字段组合

3. `scripts/run_feature_experiment.py`
- 新增 `--feature_set`
- 改为走 `resolve_feature_selection()` + `build_research_features(feature_set=...)`

4. `scripts/run_feature_readiness_audit.py`
- 新增 `--feature_set`
- 主体改为基于 feature set 构建再审计

5. `scripts/run_feature_ablation.py`
- 新增 `--feature_set`
- 改为基于 feature set 做 ablation 基座

6. `scripts/run_daily_trading.py`
- 若模型 artifact 中已保存 canonical `feature_set_name`，报告会优先展示它，而不是回退成历史名字

7. `qsys/strategy/generator.py`
- 新增 `load_model_artifact_metadata()`
- 统一读取模型目录中的 canonical feature metadata，供 live / backtest / strict eval 复用

8. `scripts/run_backtest.py` / `qsys/reports/backtest.py`
- backtest report 现在会展示模型 artifact 中保存的 canonical `feature_set_name`

9. `scripts/run_strict_eval.py` / `qsys/reports/strict_eval.py`
- strict eval report 现在会展示 baseline / extended artifact 的 canonical `feature_set_name`

## 5. 当前未做的修补

以下入口本轮未继续改动，原因是它们不是主路径或收益不高：

- `scripts/run_compare.py`
- `scripts/debug_model_performance.py`
- `qsys/dataview/inference.py`
- `qsys/dataview/research.py`

## 6. 验证结果

### 6.1 编译与全量测试

已执行：

```bash
./.envs/test/bin/python -m compileall qsys scripts tests
PYTHONPATH=$(pwd) ./.envs/test/bin/python -m unittest discover tests
```

结果：通过。

### 6.2 feature build / experiment / audit / ablation 脚本最小验证

已执行：

```bash
PYTHONPATH=$(pwd) ./.envs/test/bin/python scripts/run_feature_build.py --feature_set short_horizon_state_core_v1 ...
PYTHONPATH=$(pwd) ./.envs/test/bin/python scripts/run_feature_experiment.py --feature_set short_horizon_state_core_v1 ...
PYTHONPATH=$(pwd) ./.envs/test/bin/python scripts/run_feature_readiness_audit.py --feature_set research_semantic_default_v1 ...
PYTHONPATH=$(pwd) ./.envs/test/bin/python scripts/run_feature_ablation.py --feature_set research_semantic_default_v1 ...
```

结果：均成功生成输出文件。

### 6.3 训练入口 feature set 解析验证

已验证 `FeatureLibrary` 可以同时解析：

- 语义化 set 名称：
  - `price_volume_expression_core_v1`
  - `price_volume_fundamental_core_v1`
  - `price_volume_fundamental_event_core_v1`
- 历史 alias：
  - `alpha158`
  - `extended`
  - `margin_extended`
  - `phase1`

## 7. 总结：哪些链路已经适配，哪些还没适配

### 7.1 已适配的链路

- `scripts/run_feature_build.py`
- `qsys/feature/library.py`（训练兼容映射层）
- `tests/test_feature_registry.py`

### 7.2 部分适配的链路

- `scripts/run_feature_experiment.py`
- `scripts/run_feature_readiness_audit.py`
- `scripts/run_feature_ablation.py`
- `scripts/run_train.py`
- `scripts/run_backtest.py`
- `scripts/run_strict_eval.py`
- `scripts/run_daily_trading.py`
- `qsys/live/manager.py`
- `qsys/strategy/generator.py`
- `tests/test_feature_phase1.py`
- `tests/test_extended_feature_config.py`
- `python -m unittest discover tests`

### 7.3 未适配的链路

- `scripts/run_compare.py`
- `scripts/debug_model_performance.py`
- `qsys/dataview/inference.py`
- `qsys/dataview/research.py`

### 7.4 当前代码库中不存在的链路

- 独立的 feature refresh 主入口
- 独立的 production inference data prep orchestration 入口

当前真实生产相关入口仍是：

- `scripts/run_daily_trading.py`
- `qsys/live/manager.py`
- 模型 artifact 中保存的 `feature_config`
