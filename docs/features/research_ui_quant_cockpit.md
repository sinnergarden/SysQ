# FEATURE: research_ui_quant_cockpit

## Goal

- 将现有 Research UI 重构为更适合量化研究排查的 cockpit，优先提升首屏信息密度、页面联动和下钻效率。
- 在不重写后端业务逻辑的前提下，统一布局、筛选区、表格、状态展示和跨页跳转体验。
- 让研究路径围绕 `backtest -> daily drill-down -> replay/case/feature` 自然闭环，减少来回切页和手工抄参数。

## Use Cases

- 用例 1：研究用户先在 Backtest Explorer 选 run，再点击某个交易日，直接查看当日订单、主要贡献标的，并跳到 Replay 或单票 Case。
- 用例 2：研究用户在 Case Workspace 围绕单票查看价格、信号、特征、仓位/订单和简要解释，确认“特征 -> 信号 -> 仓位/订单”的因果链。
- 用例 3：研究用户在 Feature Health 先看问题队列，再点某个 feature 查看 snapshot、registry 元信息和缺失的诊断占位区。
- 用例 4：研究用户在 Decision Replay 对比 previous positions、candidate pool 和 final orders，回答为什么买/卖/未入选。
- 用例 5：用户在任一页面点击或复制 `instrument_id`、`trade_date`、`feature_id`、`run_id`，快速带上下文跳转到相关页面。

## API Change

- 是否新增 API：否。
- 是否修改现有 API：否，优先复用当前 `qsys.research_ui.api` 提供的 schema。
- 兼容策略：前端对缺失字段显示 placeholder，不把缺口伪装成真实研究结论。

## UI

- 新增统一 cockpit shell：导航、sticky 顶部过滤条、当前上下文条、统一 panel / card / table / badge 组件。
- Backtest Explorer 升级为主页面，首屏优先展示大 equity chart、研究摘要卡和日级 drill-down。
- Case Workspace 升级为单票工作台，主图堆叠展示，右侧显示 signal/meta/links/explanation。
- Feature Health 升级为 diagnosis 页面，左表右详情，问题队列和 summary row 前置。
- Decision Replay 升级为 pipeline 页面，突出 universe -> ranking -> filtered -> portfolio -> orders 的链路。
- 所有页面补充 drill-down / copy 操作，统一行为与视觉语言。

## Constraints

- 不改写已有后端业务逻辑，只允许最小前端交互层整理。
- 保持现有 API endpoint 与 schema 基本稳定，缺少的数据只做 placeholder 提示。
- 入口编排仍保留在当前 Research UI 页面，不下沉交易或研究逻辑到前端。
- 页面在桌面与移动端都要能加载和浏览，表头与顶栏保持 sticky。

## Done Criteria

- 全局布局、顶部过滤条、上下文展示、统一表格/面板/状态样式落地。
- Backtest Explorer 成为主 cockpit，支持 chart-table linking、订单上下文区、直接 Open Replay / Open Case。
- Case Workspace、Feature Health、Decision Replay 都接入统一导航和 drill-down。
- `instrument_id`、`trade_date`、`feature_id`、`run_id` 在主要表格/面板中可点击且可复制。
- 对后端暂缺的数据显式展示 gap/placeholder，不误导为真实结果。

## Test Plan

- `python -m compileall qsys scripts tests`
- `python -m unittest tests/test_research_ui_api.py`
- `python -m unittest tests/test_research_ui_schema.py`
- `python -m unittest discover tests`

## Rollback Plan

- 前端改动集中在 `qsys/research_ui/web/*`，可按单个 commit 回滚。
- 若 UI 引入明显回归，可先回退 web 静态资源，不影响既有 API 和研究产物。

## Notes

- 当前后端对 Feature Health 的 distribution / drift、Replay 的完整 pipeline 分段和 Backtest 的 contributor 归因仍有数据缺口，前端先保留 placeholder。
- 若后续需要长期稳定的 pipeline 细节或因子漂移诊断，再追加 feature 文档或 ADR 讨论 API 扩展。
