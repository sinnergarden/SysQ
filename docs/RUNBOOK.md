# RUNBOOK

## 启动与日常运行

### 环境准备

```bash
pip install -r requirements.txt
export PYTHONPATH=$PYTHONPATH:$(pwd)
cp config/settings.example.yaml config/settings.yaml
```

最小配置校验：

```bash
python - <<'PY'
from qsys.config.manager import cfg
print("data_root=", cfg.data_root)
print("qlib_bin=", cfg.get_path("qlib_bin"))
PY
```

### 日常主流程

盘前 / 生成次日计划：

```bash
python scripts/run_daily_trading.py --date 2026-03-20
```

盘后 / 回填真实账户并生成 real vs shadow 对账：

```bash
python scripts/run_post_close.py --date 2026-03-20 --real_sync broker/real_sync_2026-03-20.csv
```

### 盘后 CSV 格式

当前最小必填列：

- `symbol`：证券代码
- `amount`：收盘后真实持仓股数
- `price`：收盘快照价格
- `cost_basis`：持仓成本价
- `cash`：账户现金
- `total_assets`：账户总资产

建议补充列（若当天有真实成交）：

- `side`：`buy` / `sell` / `hold`
- `filled_amount`：真实成交股数
- `filled_price`：真实成交均价
- `fee`：手续费
- `tax`：税费
- `total_cost`：成交净额
- `order_id`：订单编号

说明：

- `cash` 与 `total_assets` 可以在每行重复填写，脚本默认取首个非空值。
- 无成交的持仓行可填 `side=hold`。
- 若未提供 `filled_amount` / `filled_price`，会退回使用 `amount` / `price`。

### 常用运行命令

```bash
python scripts/run_train.py --model qlib_lgbm --start 2023-01-01 --end 2026-02-28
python scripts/run_backtest.py
python scripts/run_post_close.py --date 2026-03-20 --real_sync broker/real_sync_2026-03-20.csv
python -m unittest discover tests
```

## 日志查看

- 优先查看脚本标准输出和错误输出。
- 若接入文件日志，默认在 `logs/` 下按日期检索。
- 排障时保留完整报错栈，不要只截取最后一行。
- 排障时优先定位第一处异常，而不是最后一处连锁异常。

## 常见故障与排查

### 模块找不到

表现：
- `ModuleNotFoundError: No module named 'qsys.xxx'`

排查：
1. 执行 `export PYTHONPATH=$PYTHONPATH:$(pwd)`。
2. 确认当前目录是仓库根目录。
3. 确认目标模块文件实际存在。
4. 执行 `python -m compileall qsys` 确认模块可编译。

### 配置接口报错

表现：
- `AttributeError: 'ConfigManager' object has no attribute 'xxx'`

排查：
1. 检查 `qsys/config/manager.py` 是否包含该方法。
2. 检查 `config/settings.yaml` 是否存在且格式正确。
3. 检查调用方是否使用了历史接口名称。
4. 检查 `.gitignore` 是否忽略了配置文件，避免误以为已提交。

### 数据不可用

表现：
- 初始化失败、数据全空、字段缺失。

排查：
1. 检查 `config/settings.yaml` 的数据路径配置。
2. 检查 `data/qlib_bin` 是否存在并包含日历与特征文件。
3. 运行 `tests/test_data_quality.py` 做快速验证。
4. 若日历缺失，优先重建 qlib_bin 而不是直接跳过该错误。

## 发布与回滚

### 发布前

```bash
python -m compileall qsys scripts tests
python -m unittest discover tests
```

### 回滚策略

- 优先按 commit 回滚，不手工逐文件回退。
- 若线上紧急回滚，先恢复上一稳定提交，再补复盘与修复提交。
