# SysQ 开发环境定义

## 1. 运行环境

- 操作系统：macOS / Linux（推荐）
- Python：3.10+
- 核心依赖：见 `requirements.txt`
- 数据框架：Qlib

## 2. 本地初始化

```bash
pip install -r requirements.txt
export PYTHONPATH=$PYTHONPATH:$(pwd)
```

## 3. 目录约定

- 原始数据：`data/raw/daily/`
- 二进制数据：`data/qlib_bin/`
- 模型产物：`data/models/`
- 账户数据库：`data/real_account.db`
- 测试代码：`tests/`

## 4. 开发检查命令

```bash
python -m compileall qsys scripts tests
python -m unittest discover tests
```

## 5. 关键运行命令

```bash
python scripts/run_train.py --model qlib_lgbm --start 2023-01-01 --end 2026-02-28
python scripts/run_backtest.py
python scripts/run_daily_trading.py
```

## 6. 小资金测试命令

```bash
python scripts/run_daily_trading.py --shadow_cash 20000 --top_k 2 --min_trade 2000
```

## 7. 数据与配置说明

1. `config/settings.yaml` 为统一配置来源，建议将路径、Token、Webhook 等放入此文件。
2. 修改 `qsys/data`、`qsys/model`、`qsys/strategy` 等核心目录后，必须执行测试。
3. 若切换开发工具（如 Codex 等），本文件与 `docs/rules`、`docs/manuals` 仍可独立使用。
