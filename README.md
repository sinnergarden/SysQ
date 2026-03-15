# README

SysQ 是一个面向 A 股场景的系统化量化交易代码库，覆盖数据准备、特征计算、模型训练、策略生成、回测验证与每日交易计划。

## 这个项目是干什么的

- 用统一工程结构承接“研究到交易”的完整闭环。
- 让同一套模型与策略逻辑同时服务回测与日常交易计划生成。
- 提供影子账户和实盘账户两套状态管理，降低执行偏差风险。

## 现在做到哪一步

- 已具备基础训练与推理链路，核心入口可运行。
- 已具备每日交易主流程，包含模型新鲜度检查、影子模拟和计划生成。
- 已建立核心测试集与文档体系，正在收敛首次稳定提交。
- 当前重点见 [ROADMAP.md](file:///Users/liuming/Documents/trae_projects/SysQ/ROADMAP.md)。

当前已知约束：

- 本地运行依赖 `config/settings.yaml`，该文件默认不入库。
- 测试主框架为 `unittest`，使用 `pytest` 时需保证 `PYTHONPATH` 正确。

## 怎么跑起来

1. 安装依赖并设置模块路径。

```bash
pip install -r requirements.txt
export PYTHONPATH=$PYTHONPATH:$(pwd)
```

2. 准备本地配置文件。

```bash
cp config/settings.example.yaml config/settings.yaml
```

3. 训练一个基线模型。

```bash
python scripts/run_train.py --model qlib_lgbm --start 2023-01-01 --end 2026-02-28
```

4. 运行每日交易主入口。

```bash
python scripts/run_daily_trading.py
```

5. 运行测试。

```bash
python -m unittest discover tests
```

## 核心目录结构是什么

```text
SysQ/
├── qsys/                # 核心业务代码（数据、特征、模型、策略、交易）
├── scripts/             # 命令行入口脚本
├── tests/               # 测试代码
├── config/              # 配置文件
├── notebooks/           # 演示与研究 notebook
├── docs/                # 架构、规范、运维、测试、ADR
├── README.md            # 入口地图（人和 AI 都先看）
├── AGENTS.md            # AI 操作说明书
├── CONTRIBUTING.md      # 协作与提交流程
└── ROADMAP.md           # 当前阶段目标与任务焦点
```

## 主要技术栈是什么

- Python 3.10+
- Qlib
- Pandas / NumPy
- LightGBM
- SQLite
- unittest

## 新人第一次该看哪里

建议按以下顺序阅读：

1. [README.md](file:///Users/liuming/Documents/trae_projects/SysQ/README.md)
2. [AGENTS.md](file:///Users/liuming/Documents/trae_projects/SysQ/AGENTS.md)
3. [ARCHITECTURE.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/ARCHITECTURE.md)
4. [STYLEGUIDE.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/STYLEGUIDE.md)
5. [TESTING.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/TESTING.md)
6. [RUNBOOK.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/RUNBOOK.md)
7. [DECISIONS.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/DECISIONS.md)
8. [ROADMAP.md](file:///Users/liuming/Documents/trae_projects/SysQ/ROADMAP.md)

## 功能文档规范

- 每个新功能必须先写：`docs/features/<feature_name>.md`。
- 模板文件是 [new_feature.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/features/new_feature.md)。
- 架构文档只保留大结构，功能细节统一放在 `docs/features/`。

## 要做新功能时先看什么

1. 在 [ARCHITECTURE.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/ARCHITECTURE.md) 判断功能该放在哪个模块。
2. 在 [AGENTS.md](file:///Users/liuming/Documents/trae_projects/SysQ/AGENTS.md) 确认可直接改和需先讨论的边界。
3. 在 [STYLEGUIDE.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/STYLEGUIDE.md) 对齐代码风格。
4. 在 [TESTING.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/TESTING.md) 按改动范围补测试并执行验证。
5. 在 [RUNBOOK.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/RUNBOOK.md) 检查运行与排障步骤。
