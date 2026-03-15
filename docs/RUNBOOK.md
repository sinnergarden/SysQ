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

```bash
python scripts/run_daily_trading.py
```

### 常用运行命令

```bash
python scripts/run_train.py --model qlib_lgbm --start 2023-01-01 --end 2026-02-28
python scripts/run_backtest.py
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
