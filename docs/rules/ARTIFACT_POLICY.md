# 产物目录策略（忽略与白名单）

## 1. 目标

减少仓库噪音，避免训练/回测临时产物污染提交历史，保证首次提交可审阅、可回滚。

## 2. 默认忽略（不入库）

以下目录或文件默认不应进入版本库：

- 训练与跟踪产物：`mlruns/`、`Users/`、`notebooks/mlruns/`、`notebooks/Users/`
- 运行日志：`logs/`、`*.log`
- 本地数据与数据库：`data/`、`*.db`
- Python 缓存：`__pycache__/`、`*.pyc`

实际规则以 [.gitignore](file:///Users/liuming/Documents/trae_projects/SysQ/.gitignore) 为准。

## 3. 白名单（建议入库）

即使涉及“结果”，以下文件建议保留在仓库中：

1. 核心代码：`qsys/`、`scripts/`
2. 测试代码：`tests/`
3. 项目文档：`README.md`、`context.md`、`DAILY_TRADING_GUIDE.md`、`docs/`
4. 小规模示例配置：`config/`、必要的样例 CSV（非敏感）

## 4. 提交前检查

```bash
git status --short
```

如果出现大量产物文件，请先清理再提交，避免将一次提交变成“代码 + 大量垃圾产物”的混合包。

## 5. 与工具无关

该策略不依赖 Trae。即使迁移到 Codex 或其他 IDE/Agent，此策略仍然有效。
