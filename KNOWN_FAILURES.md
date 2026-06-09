# KNOWN_FAILURES — Known Pre-Existing Test Failures

该文件记录当前已知但非本次 PR 引入的测试失败，供 CI 忽略和后续修复参考。

## `tests/research/test_rolling_research_runner.py` — 16 failures

**根因**：Matrix experiment 测试创建 label "l1"，但 `LabelStore.validate_label()` 要求对应 label 文件存在于 `data/research/labels/l1/`。测试未 mock LabelStore，因此失败。

**历史**：自 rolling runner v2 matrix 功能合并后持续存在。这些测试创建了完整 matrix 配置但缺少 mock label 数据。

**涉及用例**：

| 类 | 用例数 |
|----|--------|
| `TestBuildRollingWindows` | 2 |
| `TestFixtureSignalGenerator` | 2 |
| `TestRollingResearchRunner` | 2 |
| `TestMatrixExperiment` | 9 |
| `TestMatrixWithCombinations` | 1 |

**修复方案**：在 `setUp` 或 fixture 中创建临时 label 数据 `data/research/labels/l1/labels.parquet`，或 mock `LabelStore.validate_label`。

## `tests/test_mainline_readiness.py` — 1 collection error

**根因**：`qsys/research/readiness.py` 移除了 `write_json` 函数，但 `test_mainline_readiness.py` 仍 `from qsys.research.readiness import write_json`。

**涉及文件**：
- `tests/test_mainline_readiness.py` 

**修复方案**：更新 import 路径或移除已废弃的 import。

## `tests/signal/test_signal_store.py` — 17 failures

**根因**：`_check_no_lookahead_on_frame` 使用 qlib calendar 验证 `data_date < previous_trading_day(trade_date)`。测试使用未来日期（2026-06），qlib calendar 没有这些日期，fallback 到 weekday 逻辑。当 data_date == trade_date 的前一个交易日在 weekday fallback 下匹配时，若 data_date 在时间上太接近则触发 violation。此外 `list_signal_runs` 相关失败是因为测试目录结构不包含 manifest.json。

**历史**：自 PR #134 加入 `_check_no_lookahead_on_frame` 后持续存在。

**涉及用例**：全部 TestSignalStoreSave、TestSignalStoreNoLookahead、TestSignalStoreLoad、TestSignalStoreList。

**修复方案**：测试中使用更早的日期（如 2020-01-01），确保 qlib calendar 覆盖，或 mock `_check_no_lookahead_on_frame`。
